"""
Bulletin Names Browser — View extracted names from church bulletins.
Filterable by state, city, church. Shows full provenance (PDF link, date).
"""
from collections import Counter
from flask import Blueprint, render_template, abort, request, jsonify
from app.data_loader import (
    get_bulletin_names, get_states_with_bulletins, get_bulletin_stats,
    get_bulletin_names_page,
)

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
    """Show all bulletin names for a state in a filterable DataTable (AJAX)."""
    df = get_bulletin_names(state)
    if df is None:
        abort(404)

    stats = get_bulletin_stats(state)
    display_name = state.replace("_", " ").title()

    # Get unique cities and churches for filter dropdowns
    cities = sorted(df["city"].dropna().unique().tolist()) if "city" in df.columns else []

    # Build church dropdown entries with city disambiguation for duplicates
    church_names = df["church_name"].dropna().unique().tolist()
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

    return render_template(
        "bulletin/state.html",
        state=state,
        display_name=display_name,
        stats=stats,
        cities=cities,
        church_options=church_options,
        prefilter_church=prefilter_church,
        prefilter_city=prefilter_city,
    )


@bp.route("/<state>/api/names")
def api_names(state):
    """DataTables server-side AJAX endpoint for bulletin names."""
    df = get_bulletin_names(state)
    if df is None:
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

    rows, total, filtered = get_bulletin_names_page(
        state,
        start=start,
        length=length,
        search=search,
        order_col=order_col,
        order_dir=order_dir,
        church_filter=church,
        city_filter=city,
        category_filter=category,
    )

    return jsonify({
        "draw": draw,
        "recordsTotal": total,
        "recordsFiltered": filtered,
        "data": rows,
    })
