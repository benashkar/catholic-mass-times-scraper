"""
Data Loader — Reads CSV/JSONL data from the data/output/ directory.

STRATEGY:
  - On startup: Load all parsed_addresses.csv files into one DataFrame (master church list)
  - On demand: Load all_services.csv and bulletin_names.csv per state (lazy, LRU cached)

This avoids needing a database for the POC while keeping memory usage manageable.
"""
import os
import re
import pandas as pd
import numpy as np
from functools import lru_cache

# Module-level globals set during init_data()
DATA_DIR = None
_churches_df = None   # Master church list (all states, ~23K rows, ~4 MB)
_state_list = None    # Cached list of state dicts

# Maps state directory names to expected state name in addresses
STATE_DIR_TO_NAME = {
    "alabama": "Alabama", "alaska": "Alaska", "arizona": "Arizona",
    "arkansas": "Arkansas", "california": "California", "colorado": "Colorado",
    "connecticut": "Connecticut", "delaware": "Delaware", "florida": "Florida",
    "georgia": "Georgia", "hawaii": "Hawaii", "idaho": "Idaho",
    "illinois": "Illinois", "indiana": "Indiana", "iowa": "Iowa",
    "kansas": "Kansas", "kentucky": "Kentucky", "louisiana": "Louisiana",
    "maine": "Maine", "maryland": "Maryland", "massachusetts": "Massachusetts",
    "michigan": "Michigan", "minnesota": "Minnesota", "mississippi": "Mississippi",
    "missouri": "Missouri", "montana": "Montana", "nebraska": "Nebraska",
    "nevada": "Nevada", "new_hampshire": "New Hampshire", "new_jersey": "New Jersey",
    "new_mexico": "New Mexico", "new_york": "New York",
    "north_carolina": "North Carolina", "north_dakota": "North Dakota",
    "ohio": "Ohio", "oklahoma": "Oklahoma", "oregon": "Oregon",
    "pennsylvania": "Pennsylvania", "rhode_island": "Rhode Island",
    "south_carolina": "South Carolina", "south_dakota": "South Dakota",
    "tennessee": "Tennessee", "texas": "Texas", "utah": "Utah",
    "vermont": "Vermont", "virginia": "Virginia", "washington": "Washington",
    "west_virginia": "West Virginia", "wisconsin": "Wisconsin", "wyoming": "Wyoming",
}

# Also map abbreviations
STATE_ABBREV_TO_DIR = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas",
    "CA": "california", "CO": "colorado", "CT": "connecticut", "DE": "delaware",
    "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho",
    "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
    "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi",
    "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new_hampshire", "NJ": "new_jersey", "NM": "new_mexico", "NY": "new_york",
    "NC": "north_carolina", "ND": "north_dakota", "OH": "ohio", "OK": "oklahoma",
    "OR": "oregon", "PA": "pennsylvania", "RI": "rhode_island", "SC": "south_carolina",
    "SD": "south_dakota", "TN": "tennessee", "TX": "texas", "UT": "utah",
    "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west_virginia",
    "WI": "wisconsin", "WY": "wyoming",
}


def _extract_state_from_address(address):
    """Extract state name from an address like '123 Main St, City, State 12345'."""
    if not isinstance(address, str):
        return None
    # Try full state name pattern: ", StateName 12345"
    m = re.search(r",\s*([A-Za-z]+(?:\s+[A-Za-z]+)*)\s+\d{5}", address)
    if m:
        return m.group(1).strip()
    # Try abbreviation pattern: ", ST 12345"
    m = re.search(r",\s*([A-Z]{2})\s+\d{5}", address)
    if m:
        abbrev = m.group(1)
        state_dir = STATE_ABBREV_TO_DIR.get(abbrev)
        if state_dir:
            return STATE_DIR_TO_NAME.get(state_dir)
    return None


def _clean_nan(df):
    """Replace all NaN/None values with empty strings for display.
    Pandas float('nan') is truthy, so Jinja2 'or' doesn't catch it."""
    return df.fillna("")


def init_data(app):
    """
    Called once on app startup. Loads the master church directory
    from all states' parsed_addresses.csv files.
    """
    global DATA_DIR, _churches_df, _state_list
    DATA_DIR = app.config["DATA_DIR"]

    app.logger.info(f"Loading church data from: {DATA_DIR}")

    frames = []
    for state_dir in sorted(os.listdir(DATA_DIR)):
        state_path = os.path.join(DATA_DIR, state_dir)
        if not os.path.isdir(state_path):
            continue
        addr_path = os.path.join(state_path, "parsed_addresses.csv")
        if os.path.isfile(addr_path):
            try:
                df = pd.read_csv(addr_path, encoding="utf-8-sig")
                df["state_dir"] = state_dir  # e.g., "arizona"
                frames.append(df)
            except Exception as e:
                app.logger.warning(f"Failed to load {addr_path}: {e}")

    if frames:
        _churches_df = pd.concat(frames, ignore_index=True)
        # Fill NaN cities with empty string
        _churches_df["city"] = _churches_df["city"].fillna("Unknown")
        _churches_df["state_code"] = _churches_df["state_code"].fillna("")
        app.logger.info(f"Loaded {len(_churches_df)} churches across {_churches_df['state_dir'].nunique()} states")
    else:
        _churches_df = pd.DataFrame()
        app.logger.warning("No church data found!")

    # Pre-compute state list with counts
    _state_list = _build_state_list()


def _build_state_list():
    """Build a sorted list of states with church counts and bulletin availability."""
    if _churches_df is None or _churches_df.empty:
        return []

    state_counts = _churches_df.groupby("state_dir").agg(
        church_count=("slug", "count"),
        state_code=("state_code", "first"),
    ).reset_index()

    result = []
    for _, row in state_counts.iterrows():
        state_dir = row["state_dir"]
        has_bulletin = os.path.isfile(os.path.join(DATA_DIR, state_dir, "bulletin_names.csv"))
        has_services = os.path.isfile(os.path.join(DATA_DIR, state_dir, "all_services.csv"))
        result.append({
            "state_dir": state_dir,
            "state_code": row["state_code"] or state_dir[:2].upper(),
            "display_name": state_dir.replace("_", " ").title(),
            "church_count": int(row["church_count"]),
            "has_bulletin": has_bulletin,
            "has_services": has_services,
        })

    return sorted(result, key=lambda x: x["display_name"])


def get_states():
    """Return list of state dicts with counts and data availability."""
    return _state_list


def get_states_with_bulletins():
    """Return list of state dicts that have bulletin_names.csv."""
    return [s for s in _state_list if s["has_bulletin"]]


def get_churches_for_state(state_dir):
    """Return DataFrame of churches for a given state."""
    if _churches_df is None or _churches_df.empty:
        return pd.DataFrame()
    return _churches_df[_churches_df["state_dir"] == state_dir].copy()


@lru_cache(maxsize=3)
def get_services(state_dir):
    """
    Load and return services DataFrame for a state.
    Cached for the 5 most recently accessed states.
    Filters out cross-state contamination (churches whose address
    doesn't match the expected state).
    """
    path = os.path.join(DATA_DIR, state_dir, "all_services.csv")
    if not os.path.isfile(path):
        return pd.DataFrame()

    df = pd.read_csv(path, encoding="utf-8-sig")

    # Parse city and address_state from Address: "street, City, State Zip"
    if "Address" in df.columns:
        parts = df["Address"].str.split(",")
        # City is usually the second part; state+zip is the third
        df["city"] = parts.str[1].str.strip()
        df["city"] = df["city"].fillna("Unknown")

        # Filter: only keep rows whose address state matches this state_dir
        expected_state = STATE_DIR_TO_NAME.get(state_dir, "")
        if expected_state:
            df["_addr_state"] = df["Address"].apply(_extract_state_from_address)
            before = len(df)
            # Keep rows where address state matches OR where we couldn't parse state
            df = df[
                (df["_addr_state"].isna()) |
                (df["_addr_state"] == "") |
                (df["_addr_state"].str.lower() == expected_state.lower())
            ].copy()
            removed = before - len(df)
            if removed > 0:
                import logging
                logging.getLogger(__name__).info(
                    f"Filtered {removed} cross-state services from {state_dir} "
                    f"(kept {len(df)})"
                )
            df.drop(columns=["_addr_state"], inplace=True, errors="ignore")
    else:
        df["city"] = "Unknown"

    # Join services to slugs from parsed_addresses for unique identification
    churches_df = get_churches_for_state(state_dir)
    if not churches_df.empty and "Church" in df.columns and "city" in df.columns:
        # Build lookup: (name, city) -> slug, and name -> slug (fallback)
        slug_by_name_city = {}
        slug_by_name = {}
        for _, row in churches_df.iterrows():
            name = str(row.get("name", "")).strip()
            city = str(row.get("city", "")).strip()
            slug = str(row.get("slug", "")).strip()
            if name and slug:
                slug_by_name_city[(name, city)] = slug
                if name not in slug_by_name:
                    slug_by_name[name] = slug

        def _lookup_slug(row):
            name = str(row.get("Church", "")).strip()
            city = str(row.get("city", "")).strip()
            return slug_by_name_city.get((name, city)) or slug_by_name.get(name, "")

        df["church_slug"] = df.apply(_lookup_slug, axis=1)
    else:
        df["church_slug"] = ""

    # Create display name: "Church Name (City)" for duplicate names
    if "Church" in df.columns and "city" in df.columns:
        name_counts = df.groupby("Church")["city"].nunique()
        dup_names = set(name_counts[name_counts > 1].index)
        df["church_display"] = df.apply(
            lambda r: f"{r['Church']} ({r['city']})" if r["Church"] in dup_names else r["Church"],
            axis=1,
        )
    else:
        df["church_display"] = df.get("Church", "")

    # Clean all NaN values for display
    df = _clean_nan(df)

    return df


@lru_cache(maxsize=2)
def get_bulletin_names(state_dir):
    """
    Load and return bulletin names DataFrame for a state.
    Joins with parsed_addresses to add city/address info.
    Returns None if no bulletin data exists.
    """
    path = os.path.join(DATA_DIR, state_dir, "bulletin_names.csv")
    if not os.path.isfile(path):
        return None

    df = pd.read_csv(path, encoding="utf-8-sig", nrows=50_000)

    # If CSV already has city column (new format), use it directly
    if "city" in df.columns and df["city"].notna().any():
        df["city"] = df["city"].fillna("Unknown")
        # Still try to join for full_street if available
        churches = get_churches_for_state(state_dir)
        if not churches.empty and "church_slug" in df.columns:
            addr_cols = [c for c in ["slug", "full_street", "state_code", "zip5"] if c in churches.columns]
            if addr_cols:
                merged = df.merge(
                    churches[addr_cols],
                    left_on="church_slug",
                    right_on="slug",
                    how="left",
                    suffixes=("", "_addr"),
                )
                merged["full_street"] = merged.get("full_street", pd.Series("")).fillna("")
                return merged
        return df

    # Legacy: CSV without city column — join with churches to get city + full_street
    churches = get_churches_for_state(state_dir)
    if not churches.empty and "church_slug" in df.columns:
        merged = df.merge(
            churches[["slug", "city", "full_street", "state_code", "zip5"]],
            left_on="church_slug",
            right_on="slug",
            how="left",
            suffixes=("", "_addr"),
        )
        merged["city"] = merged["city"].fillna("Unknown")
        merged["full_street"] = merged["full_street"].fillna("")
        return merged

    return df


def get_bulletin_stats(state_dir):
    """Return summary stats for bulletin names in a state."""
    df = get_bulletin_names(state_dir)
    if df is None or df.empty:
        return None

    # Unique names: count by (person_name, city) pairs so the same name
    # at different churches in different cities counts as separate people
    if "city" in df.columns:
        unique = df.groupby(["person_name", "city"]).ngroups
    else:
        unique = df["person_name"].nunique()

    return {
        "total_names": len(df),
        "unique_names": unique,
        "church_count": df["church_name"].nunique(),
        "city_count": df["city"].nunique() if "city" in df.columns else 0,
    }


@lru_cache(maxsize=3)
def _load_church_details_jsonl(state_dir):
    """Load church_details.jsonl and build a lookup by church name.
    Returns dict mapping church name -> {website_resolved, slug}.
    """
    import json as _json
    path = os.path.join(DATA_DIR, state_dir, "church_details.jsonl")
    if not os.path.isfile(path):
        return {}

    lookup = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                entry = _json.loads(line)
                church = entry.get("church", {})
                name = church.get("name", "").strip()
                slug = church.get("slug", "")
                website = entry.get("website_resolved") or church.get("website_resolved", "") or ""
                if name:
                    lookup[name] = {"website": website, "slug": slug or ""}
    except Exception:
        pass
    return lookup


def get_church_website(state_dir, church_name):
    """Look up a church's resolved website URL from JSONL data."""
    lookup = _load_church_details_jsonl(state_dir)
    info = lookup.get(church_name, {})
    return info.get("website", "")


def get_church_slug(state_dir, church_name):
    """Look up a church's slug from JSONL data."""
    lookup = _load_church_details_jsonl(state_dir)
    info = lookup.get(church_name, {})
    return info.get("slug", "")


@lru_cache(maxsize=3)
def get_dated_services(state_dir):
    """
    Load and return dated services DataFrame for a state.
    Cached for the 5 most recently accessed states.
    """
    path = os.path.join(DATA_DIR, state_dir, "dated_services.csv")
    if not os.path.isfile(path):
        return pd.DataFrame()

    df = pd.read_csv(path, encoding="utf-8-sig")
    df = _clean_nan(df)
    return df


def church_has_bulletin_names(state_dir, church_name):
    """Check if a church has any bulletin-extracted names."""
    df = get_bulletin_names(state_dir)
    if df is None or df.empty:
        return False
    return (df["church_name"] == church_name).any()


# Columns served to the DataTables AJAX endpoint (order matters — matches column indices)
_BULLETIN_PAGE_COLS = [
    "person_name", "role", "title", "first_name", "last_name",
    "church_name", "city", "category", "confidence", "pdf_url", "pdf_date",
]


def get_bulletin_names_page(state_dir, start=0, length=50, search="",
                            order_col=0, order_dir="asc",
                            church_filter="", city_filter="",
                            category_filter=""):
    """
    Return a page of bulletin names for DataTables server-side processing.

    Returns (rows_list, total_records, filtered_records) where rows_list
    is a list of lists (one per row, columns in _BULLETIN_PAGE_COLS order).
    """
    df = get_bulletin_names(state_dir)
    if df is None or df.empty:
        return [], 0, 0

    total_records = len(df)

    # --- Apply filters ---
    if church_filter and "church_name" in df.columns:
        df = df[df["church_name"] == church_filter]
    if city_filter and "city" in df.columns:
        df = df[df["city"] == city_filter]
    if category_filter and "category" in df.columns:
        df = df[df["category"] == category_filter]

    # Global search across all display columns
    if search:
        search_lower = search.lower()
        available = [c for c in _BULLETIN_PAGE_COLS if c in df.columns and c != "pdf_url"]
        mask = pd.Series(False, index=df.index)
        for col in available:
            mask = mask | df[col].astype(str).str.lower().str.contains(search_lower, na=False)
        df = df[mask]

    filtered_records = len(df)

    # --- Sort ---
    available_cols = [c for c in _BULLETIN_PAGE_COLS if c in df.columns]
    if 0 <= order_col < len(available_cols):
        sort_col = available_cols[order_col]
        ascending = order_dir != "desc"
        df = df.sort_values(sort_col, ascending=ascending, na_position="last")

    # --- Paginate ---
    page = df.iloc[start:start + length]

    # Build list-of-lists with only available columns (fill missing with "")
    rows = []
    for _, row in page.iterrows():
        rows.append([str(row.get(c, "") if pd.notna(row.get(c, "")) else "")
                      for c in _BULLETIN_PAGE_COLS])

    return rows, total_records, filtered_records
