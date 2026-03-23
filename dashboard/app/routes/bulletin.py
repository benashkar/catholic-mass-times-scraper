"""
Bulletin Names Browser — View extracted names from church bulletins.
Filterable by state, city, church, confidence. Shows full provenance (PDF link, date).
"""

import csv
import io
import json
import os

from flask import Blueprint, Response, abort, jsonify, render_template, request

from app.data_loader import (
    get_bulletin_filters,
    get_bulletin_names,
    get_bulletin_names_page,
    get_bulletin_stats,
    get_states_with_bulletins,
)

bp = Blueprint("bulletin", __name__, url_prefix="/bulletin")

# Path to removed names file (persists across restarts)
_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "reference")
_REMOVED_NAMES_PATH = os.path.join(_DATA_DIR, "removed_names.json")


def _load_removed_names():
    """Load the set of removed (suspect) names."""
    if os.path.isfile(_REMOVED_NAMES_PATH):
        with open(_REMOVED_NAMES_PATH, encoding="utf-8") as f:
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
    from datetime import date, timedelta

    states = get_states_with_bulletins()
    state_stats = []
    for s in states:
        stats = get_bulletin_stats(s["state_dir"])
        state_stats.append({**s, **(stats or {})})
    seven_days_ago = str(date.today() - timedelta(days=7))
    return render_template("bulletin/index.html", states=state_stats, seven_days_ago=seven_days_ago)


@bp.route("/suspect/")
def suspect_names():
    """Show low-confidence names across all states for review."""
    states = get_states_with_bulletins()
    removed = _load_removed_names()
    removed_keys = {(r["person_name"], r["state"]) for r in removed}

    suspect = []
    for s in states:
        df = get_bulletin_names(s["state_dir"], include_low=True)
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
            suspect.append(
                {
                    "person_name": row.get("person_name", ""),
                    "church_name": row.get("church_name", ""),
                    "city": row.get("city", ""),
                    "state": state_name,
                    "category": row.get("category", ""),
                    "confidence": row.get("confidence", ""),
                    "confidence_score": float(row.get("confidence_score", 0))
                    if row.get("confidence_score", "") != ""
                    else 0,
                }
            )

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
    removed.append(
        {
            "person_name": data["person_name"],
            "state": data["state"],
            "church_name": data.get("church_name", ""),
            "city": data.get("city", ""),
            "category": data.get("category", ""),
            "confidence_score": data.get("confidence_score", 0),
        }
    )
    _save_removed_names(removed)

    return jsonify({"status": "ok", "removed_count": len(removed)})


@bp.route("/suspect/removed/")
def view_removed():
    """View all removed names for research."""
    removed = _load_removed_names()
    return render_template("bulletin/removed.html", removed=removed)


@bp.route("/<state>/")
def state_view(state):
    """Show all bulletin names for a state in a filterable DataTable (AJAX).

    Uses pre-computed stats and filter dropdowns from startup so the page
    shell loads instantly.  Actual data arrives via the AJAX api_names endpoint.
    """
    stats = get_bulletin_stats(state)
    if stats is None:
        abort(404)

    filters = get_bulletin_filters(state)
    display_name = state.replace("_", " ").title()

    # Read query params for pre-filtering (shareable URLs)
    prefilter_church = request.args.get("church", "")
    prefilter_city = request.args.get("city", "")
    min_confidence = request.args.get("confidence", "")

    return render_template(
        "bulletin/state.html",
        state=state,
        display_name=display_name,
        stats=stats,
        cities=filters["cities"] if filters else [],
        church_options=filters["church_options"] if filters else [],
        prefilter_church=prefilter_church,
        prefilter_city=prefilter_city,
        min_confidence=min_confidence,
        total_names=stats["total_names"],
        truncated=False,
    )


@bp.route("/<state>/api/names")
def api_names(state):
    """DataTables server-side AJAX endpoint for bulletin names."""
    stats = get_bulletin_stats(state)
    if stats is None:
        abort(404)

    # DataTables parameters
    draw = request.args.get("draw", 1, type=int)
    start = request.args.get("start", 0, type=int)
    length = request.args.get("length", 50, type=int)
    search = request.args.get("search[value]", "")
    order_col = request.args.get("order[0][column]", 0, type=int)
    order_dir = request.args.get("order[0][dir]", "asc")

    # Custom filter params
    church = request.args.get("church", "")
    city = request.args.get("city", "")
    category = request.args.get("category", "")
    confidence = request.args.get("confidence", "")

    rows, total, filtered, unique_filtered = get_bulletin_names_page(
        state,
        start=start,
        length=length,
        search=search,
        order_col=order_col,
        order_dir=order_dir,
        church_filter=church,
        city_filter=city,
        category_filter=category,
        confidence_filter=confidence,
    )

    return jsonify(
        {
            "draw": draw,
            "recordsTotal": total,
            "recordsFiltered": filtered,
            "uniqueFiltered": unique_filtered,
            "data": rows,
        }
    )


@bp.route("/<state>/api/names.csv")
def api_names_csv(state):
    """Download filtered bulletin names as CSV."""
    stats = get_bulletin_stats(state)
    if stats is None:
        abort(404)

    church = request.args.get("church", "")
    city = request.args.get("city", "")
    category = request.args.get("category", "")
    confidence = request.args.get("confidence", "")
    search = request.args.get("q", "")

    rows, _total, _filtered, _unique = get_bulletin_names_page(
        state,
        start=0,
        length=500000,
        search=search,
        order_col=8,
        order_dir="desc",
        church_filter=church,
        city_filter=city,
        category_filter=category,
        confidence_filter=confidence,
    )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Name",
            "Role",
            "Title",
            "First",
            "Last",
            "Church",
            "City",
            "Category",
            "Confidence",
            "PDF URL",
            "Date",
        ]
    )
    writer.writerows(rows)

    filename = f"bulletin_names_{state}"
    if confidence:
        filename += f"_{confidence}"
    filename += ".csv"

    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
