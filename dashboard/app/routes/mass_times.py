"""
Mass Times Browser — Navigate state → church (by slug) → services.
Uses slug as unique church ID for navigation. Falls back to name matching
for legacy URLs.
"""
from flask import Blueprint, render_template, abort, redirect, url_for, request, Response
from app.data_loader import (
    get_services, get_dated_services, get_church_website,
    church_has_bulletin_names, _load_church_details_jsonl, get_bulletin_names,
)

bp = Blueprint("mass_times", __name__, url_prefix="/mass-times")


@bp.route("/<state>/")
def state_view(state):
    """Show all churches in a state with name, website, address, city."""
    services = get_services(state)
    if services.empty:
        abort(404)

    # Get unique churches with their details
    # Use slug as group key where available; fall back to church_display
    # for churches without a slug (empty string = no match to parsed_addresses)
    has_slugs = "church_slug" in services.columns
    if has_slugs:
        # For rows with empty slug, use church_display as fallback group key
        services["_group_key"] = services.apply(
            lambda r: r["church_slug"] if r["church_slug"] else r["church_display"],
            axis=1,
        )
    else:
        services["_group_key"] = services["church_display"]

    churches = (
        services.groupby("_group_key")
        .agg(
            Church=("Church", "first"),
            church_display=("church_display", "first"),
            church_slug=("church_slug", "first") if has_slugs else ("church_display", "first"),
            address=("Address", "first"),
            phone=("Phone", "first"),
            city=("city", "first"),
            service_count=("Church", "count"),
        )
        .reset_index(drop=True)
        .sort_values("Church")
    )

    # For churches without a slug, use church_display as fallback ID
    churches["church_id"] = churches.apply(
        lambda r: r["church_slug"] if r["church_slug"] else r["church_display"],
        axis=1,
    )

    # Add website URLs from JSONL (ensure always string, never NaN)
    website_lookup = _load_church_details_jsonl(state)
    churches["website"] = churches["Church"].apply(
        lambda name: website_lookup.get(name, {}).get("website", "") or ""
    )

    # Add bulletin names availability
    bulletin_df = get_bulletin_names(state)
    if bulletin_df is not None and not bulletin_df.empty:
        bulletin_churches = set(bulletin_df["church_name"].unique())
        churches["has_bulletin"] = churches["Church"].isin(bulletin_churches)
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


@bp.route("/<state>/church/<path:church_id>/")
def church_view(state, church_id):
    """Show full schedule for one church.
    church_id can be a slug (preferred) or a church name (legacy).
    """
    services = get_services(state)
    if services.empty:
        abort(404)

    # Try matching by slug first (unique ID)
    church_services = services[services["church_slug"] == church_id] if "church_slug" in services.columns else services.iloc[0:0]

    # Fallback: match by church_display (name with city disambiguation)
    if church_services.empty:
        church_services = services[services["church_display"] == church_id]

    # Fallback: match by original Church name (legacy URLs)
    if church_services.empty:
        church_services = services[services["Church"] == church_id]
        if not church_services.empty:
            # If multiple slugs exist, show disambiguation page
            unique_slugs = church_services["church_slug"].nunique() if "church_slug" in church_services.columns else 0
            unique_addresses = church_services["Address"].nunique()
            if unique_addresses > 1:
                options = (
                    church_services.groupby(
                        "church_slug" if "church_slug" in church_services.columns else "church_display"
                    )
                    .agg(
                        church_display=("church_display", "first"),
                        Church=("Church", "first"),
                        church_slug=("church_slug", "first") if "church_slug" in church_services.columns else ("church_display", "first"),
                        address=("Address", "first"),
                        phone=("Phone", "first"),
                        city=("city", "first"),
                        service_count=("Church", "count"),
                    )
                    .reset_index(drop=True)
                    .sort_values("city")
                )
                options["church_id"] = options.apply(
                    lambda r: r["church_slug"] if r["church_slug"] else r["church_display"],
                    axis=1,
                )
                display_name = state.replace("_", " ").title()
                return render_template(
                    "mass_times/disambiguate.html",
                    state=state,
                    display_name=display_name,
                    church_name=church_id,
                    options=options.to_dict("records"),
                )

    if church_services.empty:
        abort(404)

    info = church_services.iloc[0]
    address = info.get("Address", "")
    phone = info.get("Phone", "")
    actual_name = info.get("Church", church_id)
    slug = info.get("church_slug", "")

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
        church_slug=slug,
        address=address,
        phone=phone,
        website_url=website_url,
        has_bulletin=has_bulletin,
        categories=sorted_cats,
    )


@bp.route("/<state>/calendar/")
def calendar_view(state):
    """Show all dated activities for a state with filters."""
    df = get_dated_services(state)
    if df.empty:
        abort(404)

    # Get filter values from query params
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    church_filter = request.args.get("church", "")
    category_filter = request.args.get("category", "")

    # Build filter option lists before filtering
    all_churches = sorted(df["Church"].unique().tolist()) if "Church" in df.columns else []
    all_categories = sorted(df["Category"].unique().tolist()) if "Category" in df.columns else []

    # Apply filters
    if date_from and "Date Sort" in df.columns:
        df = df[df["Date Sort"] >= date_from]
    if date_to and "Date Sort" in df.columns:
        df = df[df["Date Sort"] <= date_to]
    if church_filter and "Church" in df.columns:
        df = df[df["Church"] == church_filter]
    if category_filter and "Category" in df.columns:
        df = df[df["Category"] == category_filter]

    rows = df.to_dict("records")

    display_name = state.replace("_", " ").title()
    return render_template(
        "mass_times/calendar.html",
        state=state,
        display_name=display_name,
        rows=rows,
        total_rows=len(rows),
        all_churches=all_churches,
        all_categories=all_categories,
        date_from=date_from,
        date_to=date_to,
        church_filter=church_filter,
        category_filter=category_filter,
    )


@bp.route("/<state>/calendar/download/")
def calendar_download(state):
    """Download dated_services.csv as a file."""
    import os
    from app.data_loader import DATA_DIR

    path = os.path.join(DATA_DIR, state, "dated_services.csv")
    if not os.path.isfile(path):
        abort(404)

    with open(path, "r", encoding="utf-8-sig") as f:
        csv_content = f.read()

    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={state}_dated_services.csv"},
    )
