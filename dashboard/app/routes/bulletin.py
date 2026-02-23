"""
Bulletin Names Browser — View extracted names from church bulletins.
Filterable by state, city, church. Shows full provenance (PDF link, date).
"""
from flask import Blueprint, render_template, abort
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
    churches = sorted(df["church_name"].dropna().unique().tolist())

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
    names = df[available].fillna("").to_dict("records")

    return render_template(
        "bulletin/state.html",
        state=state,
        display_name=display_name,
        names=names,
        stats=stats,
        cities=cities,
        churches=churches,
    )
