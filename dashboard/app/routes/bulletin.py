"""
Bulletin Names Browser — View extracted names from church bulletins.
Filterable by state, city, church, confidence. Shows full provenance (PDF link, date).
"""
import json
import os
from collections import Counter
from flask import Blueprint, render_template, abort, request, jsonify
from app.data_loader import get_bulletin_names, get_states_with_bulletins, get_bulletin_stats

bp = Blueprint("bulletin", __name__, url_prefix="/bulletin")

# Path to removed names file (persists across restarts)
_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "reference")
_REMOVED_NAMES_PATH = os.path.join(_DATA_DIR, "removed_names.json")


def _load_removed_names():
    """Load the set of removed (suspect) names."""
    if os.path.isfile(_REMOVED_NAMES_PATH):
        with open(_REMOVED_NAMES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_removed_names(removed):
    """Save the list of removed names."""
    os.makedirs(os.path.dirname(_REMOVED_NAMES_PATH), exist_ok=True)
    with open(_REMOVED_NAMES_PATH, "w", encoding="utf-8") as f:
        json.dump(removed, f, indent=2)


@bp.route("/")
def index():
    """Show states that have bulletin name data."""
    states = get_states_with_bulletins()
    # Get stats for each state
    state_stats = []
    for s in states:
        stats = get_bulletin_stats(s["state_dir"])
        state_stats.append({**s, **(stats or {})})
    return render_template("bulletin/index.html", states=state_stats)


@bp.route("/suspect/")
def suspect_names():
    """Show low-confidence names across all states for review."""
    states = get_states_with_bulletins()
    removed = _load_removed_names()
    removed_keys = {(r["person_name"], r["state"]) for r in removed}

    suspect = []
    for s in states:
        df = get_bulletin_names(s["state_dir"])
        if df is None:
            continue

        state_name = s["state_dir"]

        # Filter to low-confidence names
        if "confidence_score" in df.columns:
            low = df[df["confidence_score"].astype(float) < 0.4].copy()
        elif "confidence" in df.columns:
            low = df[df["confidence"] == "low"].copy()
        else:
            continue

        # Exclude already-removed names
        for _, row in low.head(200).iterrows():
            key = (row.get("person_name", ""), state_name)
            if key in removed_keys:
                continue
            suspect.append({
                "person_name": row.get("person_name", ""),
                "church_name": row.get("church_name", ""),
                "city": row.get("city", ""),
                "state": state_name,
                "category": row.get("category", ""),
                "confidence": row.get("confidence", ""),
                "confidence_score": float(row.get("confidence_score", 0))
                    if row.get("confidence_score", "") != "" else 0,
            })

    # Sort by confidence_score ascending (worst first)
    suspect.sort(key=lambda x: x.get("confidence_score", 0))

    return render_template(
        "bulletin/suspect.html",
        suspect=suspect,
        removed_count=len(removed),
    )


@bp.route("/suspect/remove", methods=["POST"])
def remove_suspect():
    """Remove a suspect name — saves it to removed_names.json."""
    data = request.get_json()
    if not data or "person_name" not in data or "state" not in data:
        return jsonify({"error": "Missing person_name or state"}), 400

    removed = _load_removed_names()
    removed.append({
        "person_name": data["person_name"],
        "state": data["state"],
        "church_name": data.get("church_name", ""),
        "city": data.get("city", ""),
        "category": data.get("category", ""),
        "confidence_score": data.get("confidence_score", 0),
    })
    _save_removed_names(removed)

    return jsonify({"status": "ok", "removed_count": len(removed)})


@bp.route("/suspect/removed/")
def view_removed():
    """View all removed names for research."""
    removed = _load_removed_names()
    return render_template("bulletin/removed.html", removed=removed)


@bp.route("/<state>/")
def state_view(state):
    """Show all bulletin names for a state in a filterable DataTable."""
    df = get_bulletin_names(state)
    if df is None:
        abort(404)

    stats = get_bulletin_stats(state)
    display_name = state.replace("_", " ").title()

    # Apply min_confidence filter
    min_confidence = request.args.get("min_confidence", "")
    if min_confidence:
        try:
            min_conf = float(min_confidence)
            if "confidence_score" in df.columns:
                df = df[df["confidence_score"].astype(float) >= min_conf]
            elif min_conf >= 0.7:
                df = df[df["confidence"] == "high"]
            elif min_conf >= 0.4:
                df = df[df["confidence"].isin(["high", "medium"])]
        except ValueError:
            pass

    # Exclude removed names
    removed = _load_removed_names()
    removed_keys = {(r["person_name"], r["state"]) for r in removed}
    if removed_keys and "person_name" in df.columns:
        df = df[~df["person_name"].apply(lambda n: (n, state) in removed_keys)]

    # Get unique cities and churches for filter dropdowns
    cities = sorted(df["city"].dropna().unique().tolist()) if "city" in df.columns else []

    # Build church dropdown entries with city disambiguation for duplicates
    church_names = df["church_name"].dropna().unique().tolist()
    name_counts = Counter(
        df.groupby("church_name")["city"].first().to_dict().values()
    )
    if "city" in df.columns:
        church_city_pairs = (
            df[df["church_name"].notna() & df["city"].notna()]
            .groupby("church_name")["city"]
            .apply(lambda x: sorted(x.unique().tolist()))
            .to_dict()
        )
    else:
        church_city_pairs = {name: [] for name in church_names}

    church_options = []
    for name in sorted(church_names):
        cities_for_church = church_city_pairs.get(name, [])
        if len(cities_for_church) > 1:
            for city in cities_for_church:
                church_options.append({
                    "label": f"{name} ({city})",
                    "church": name,
                    "city": city,
                })
        else:
            church_options.append({
                "label": name,
                "church": name,
                "city": "",
            })

    # Read ?church= and ?city= query params for pre-filtering from mass-times page
    prefilter_church = request.args.get("church", "")
    prefilter_city = request.args.get("city", "")

    # Apply pre-filters BEFORE truncating to reduce dataset for large states
    if prefilter_church and "church_name" in df.columns:
        df = df[df["church_name"] == prefilter_church]
    if prefilter_city and "city" in df.columns:
        df = df[df["city"] == prefilter_city]

    # Convert to list of dicts for the template
    columns = [
        "person_name", "title", "first_name", "middle_name", "last_name",
        "role", "church_name", "city", "full_street", "category", "confidence",
        "confidence_score", "pdf_url", "pdf_date",
    ]
    available = [c for c in columns if c in df.columns]

    # Cap rows to prevent timeout on large states (IL=223K, CA=257K)
    MAX_ROWS = 50000
    total_names = len(df)
    truncated = total_names > MAX_ROWS
    if truncated:
        df = df.head(MAX_ROWS)
    names = df[available].fillna("").to_dict("records")

    return render_template(
        "bulletin/state.html",
        state=state,
        display_name=display_name,
        names=names,
        stats=stats,
        cities=cities,
        church_options=church_options,
        prefilter_church=prefilter_church,
        prefilter_city=prefilter_city,
        min_confidence=min_confidence,
        total_names=total_names,
        truncated=truncated,
    )
