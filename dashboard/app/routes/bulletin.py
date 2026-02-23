"""
Bulletin Names Browser — View extracted names from church bulletins.
Filterable by state, city, church. Shows full provenance (PDF link, date).
"""
from collections import Counter
from flask import Blueprint, render_template, abort, request
from app.data_loader import get_bulletin_names, get_states_with_bulletins, get_bulletin_stats

bp = Blueprint("bulletin", __name__, url_prefix="/bulletin")


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


@bp.route("/<state>/")
def state_view(state):
    """Show all bulletin names for a state in a filterable DataTable."""
    df = get_bulletin_names(state)
    if df is None:
        abort(404)

    stats = get_bulletin_stats(state)
    display_name = state.replace("_", " ").title()

    # Get unique cities and churches for filter dropdowns
    cities = sorted(df["city"].dropna().unique().tolist()) if "city" in df.columns else []

    # Build church dropdown entries with city disambiguation for duplicates
    # e.g. "All Saints" appears in both Mesa and Ganado → show as
    # "All Saints (Mesa)" and "All Saints (Ganado)"
    church_names = df["church_name"].dropna().unique().tolist()
    name_counts = Counter(
        df.groupby("church_name")["city"].first().to_dict().values()
    )  # not what we need — we need count of distinct church_name values
    # Actually: count how many distinct cities each church_name appears in
    if "city" in df.columns:
        church_city_pairs = (
            df[df["church_name"].notna() & df["city"].notna()]
            .groupby("church_name")["city"]
            .apply(lambda x: sorted(x.unique().tolist()))
            .to_dict()
        )
    else:
        church_city_pairs = {name: [] for name in church_names}

    # Names that appear in multiple cities need disambiguation
    church_options = []  # list of {"label": display, "church": name, "city": city_or_empty}
    for name in sorted(church_names):
        cities_for_church = church_city_pairs.get(name, [])
        if len(cities_for_church) > 1:
            # Duplicate name — add one entry per city
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
    # 'role' = positional role (Pastor, Chairman, etc.) — new field added Feb 2026
    # 'title' = honorific prefix (Fr., Rev., etc.)
    columns = [
        "person_name", "title", "first_name", "middle_name", "last_name",
        "role", "church_name", "city", "full_street", "category", "confidence",
        "pdf_url", "pdf_date",
    ]
    # Only include columns that exist
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
        total_names=total_names,
        truncated=truncated,
    )
