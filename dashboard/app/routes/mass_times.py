"""
Mass Times Browser — Navigate state → city → church → services.
"""
from flask import Blueprint, render_template, abort
from app.data_loader import get_services

bp = Blueprint("mass_times", __name__, url_prefix="/mass-times")


@bp.route("/<state>/")
def state_view(state):
    """Show cities in a state with church/service counts."""
    services = get_services(state)
    if services.empty:
        abort(404)

    cities = (
        services.groupby("city")
        .agg(
            church_count=("Church", "nunique"),
            service_count=("Church", "count"),
        )
        .reset_index()
        .sort_values("city")
    )
    display_name = state.replace("_", " ").title()
    return render_template(
        "mass_times/state.html",
        state=state,
        display_name=display_name,
        cities=cities.to_dict("records"),
    )


@bp.route("/<state>/city/<city>/")
def city_view(state, city):
    """Show churches in a city."""
    services = get_services(state)
    if services.empty:
        abort(404)

    city_services = services[services["city"] == city]
    if city_services.empty:
        abort(404)

    churches = (
        city_services.groupby("Church")
        .agg(
            address=("Address", "first"),
            phone=("Phone", "first"),
            service_count=("Church", "count"),
        )
        .reset_index()
        .sort_values("Church")
    )
    display_name = state.replace("_", " ").title()
    return render_template(
        "mass_times/city.html",
        state=state,
        display_name=display_name,
        city=city,
        churches=churches.to_dict("records"),
    )


@bp.route("/<state>/church/<path:church_name>/")
def church_view(state, church_name):
    """Show full schedule for one church."""
    services = get_services(state)
    if services.empty:
        abort(404)

    church_services = services[services["Church"] == church_name]
    if church_services.empty:
        abort(404)

    info = church_services.iloc[0]
    address = info.get("Address", "")
    phone = info.get("Phone", "")

    # Group by category
    categories = {}
    for cat_name, group in church_services.groupby("Category"):
        rows = group.sort_values(["Day", "Time Start"]).to_dict("records")
        categories[cat_name] = rows

    # Sort categories: Mass first, then alphabetical
    cat_order = ["Mass", "Confession", "Adoration", "Devotions", "Education", "Community", "Other"]
    sorted_cats = []
    for c in cat_order:
        if c in categories:
            sorted_cats.append((c, categories[c]))
    for c in sorted(categories.keys()):
        if c not in cat_order:
            sorted_cats.append((c, categories[c]))

    display_name = state.replace("_", " ").title()
    return render_template(
        "mass_times/church.html",
        state=state,
        display_name=display_name,
        church_name=church_name,
        address=address,
        phone=phone,
        categories=sorted_cats,
    )
