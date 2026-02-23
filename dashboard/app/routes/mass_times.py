"""
Mass Times Browser — Navigate state → city → church → services.
Uses church_display (which includes city for disambiguation) as the URL key.
Filters out cross-state contamination via data_loader.
"""
from flask import Blueprint, render_template, abort
from app.data_loader import (
    get_services, get_church_website, church_has_bulletin_names,
    _load_church_details_jsonl,
)

bp = Blueprint("mass_times", __name__, url_prefix="/mass-times")


@bp.route("/<state>/")
def state_view(state):
    """Show all churches in a state with name, website, address, city."""
    services = get_services(state)
    if services.empty:
        abort(404)

    # Get unique churches with their details
    churches = (
        services.groupby("church_display")
        .agg(
            Church=("Church", "first"),
            address=("Address", "first"),
            phone=("Phone", "first"),
            city=("city", "first"),
            service_count=("church_display", "count"),
        )
        .reset_index()
        .sort_values("Church")
    )

    # Add website URLs from JSONL
    website_lookup = _load_church_details_jsonl(state)
    churches["website"] = churches["Church"].apply(
        lambda name: website_lookup.get(name, {}).get("website", "")
    )

    # Add bulletin names availability
    from app.data_loader import get_bulletin_names
    bulletin_df = get_bulletin_names(state)
    if bulletin_df is not None and not bulletin_df.empty:
        bulletin_churches = set(bulletin_df["church_name"].unique())
        churches["has_bulletin"] = churches["Church"].isin(bulletin_churches)
        # Count names per church
        name_counts = bulletin_df.groupby("church_name").size().to_dict()
        churches["bulletin_count"] = churches["Church"].map(name_counts).fillna(0).astype(int)
    else:
        churches["has_bulletin"] = False
        churches["bulletin_count"] = 0

    display_name = state.replace("_", " ").title()
    return render_template(
        "mass_times/state.html",
        state=state,
        display_name=display_name,
        churches=churches.to_dict("records"),
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

    # Look up website URL and bulletin availability
    website_url = get_church_website(state, actual_name)
    has_bulletin = church_has_bulletin_names(state, actual_name)

    display_name = state.replace("_", " ").title()
    return render_template(
        "mass_times/church.html",
        state=state,
        display_name=display_name,
        church_name=actual_name,
        address=address,
        phone=phone,
        website_url=website_url,
        has_bulletin=has_bulletin,
        categories=sorted_cats,
    )
