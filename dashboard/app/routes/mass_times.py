"""
Mass Times Browser — Navigate state → city → church → services.
Uses church_display (which includes city for disambiguation) as the URL key.
Filters out cross-state contamination via data_loader.
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
            church_count=("church_display", "nunique"),
            service_count=("church_display", "count"),
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
        city_services.groupby("church_display")
        .agg(
            address=("Address", "first"),
            phone=("Phone", "first"),
            service_count=("church_display", "count"),
            Church=("Church", "first"),
        )
        .reset_index()
        .sort_values("church_display")
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
    """Show full schedule for one church.
    church_name may be 'St. Joseph (Springfield)' for disambiguation,
    or just 'St. Joseph' for unique names.
    Also supports legacy URLs that match by Church column directly.
    """
    services = get_services(state)
    if services.empty:
        abort(404)

    # First try matching by church_display (new disambiguated key)
    church_services = services[services["church_display"] == church_name]

    # Fallback: match by original Church name (legacy URLs)
    # But only if there's exactly one address (not ambiguous)
    if church_services.empty:
        church_services = services[services["Church"] == church_name]
        if not church_services.empty:
            # If multiple addresses exist, show disambiguation page
            unique_addresses = church_services["Address"].nunique()
            if unique_addresses > 1:
                # Multiple churches with same name — show a picker
                options = (
                    church_services.groupby("church_display")
                    .agg(
                        address=("Address", "first"),
                        phone=("Phone", "first"),
                        city=("city", "first"),
                        service_count=("church_display", "count"),
                    )
                    .reset_index()
                    .sort_values("city")
                )
                display_name = state.replace("_", " ").title()
                return render_template(
                    "mass_times/disambiguate.html",
                    state=state,
                    display_name=display_name,
                    church_name=church_name,
                    options=options.to_dict("records"),
                )

    if church_services.empty:
        abort(404)

    info = church_services.iloc[0]
    address = info.get("Address", "")
    phone = info.get("Phone", "")
    actual_name = info.get("Church", church_name)

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
        church_name=actual_name,
        address=address,
        phone=phone,
        categories=sorted_cats,
    )
