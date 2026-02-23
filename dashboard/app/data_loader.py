"""
Data Loader — Reads CSV/JSONL data from the data/output/ directory.

STRATEGY:
  - On startup: Load all parsed_addresses.csv files into one DataFrame (master church list)
  - On demand: Load all_services.csv and bulletin_names.csv per state (lazy, LRU cached)

This avoids needing a database for the POC while keeping memory usage manageable.
"""
import os
import pandas as pd
from functools import lru_cache

# Module-level globals set during init_data()
DATA_DIR = None
_churches_df = None   # Master church list (all states, ~23K rows, ~4 MB)
_state_list = None    # Cached list of state dicts


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


@lru_cache(maxsize=5)
def get_services(state_dir):
    """
    Load and return services DataFrame for a state.
    Cached for the 5 most recently accessed states.
    """
    path = os.path.join(DATA_DIR, state_dir, "all_services.csv")
    if not os.path.isfile(path):
        return pd.DataFrame()

    df = pd.read_csv(path, encoding="utf-8-sig")

    # Parse city from Address: "street, city, state zip"
    if "Address" in df.columns:
        parts = df["Address"].str.split(",")
        # City is usually the second part; state+zip is the third
        df["city"] = parts.str[1].str.strip()
        df["city"] = df["city"].fillna("Unknown")
    else:
        df["city"] = "Unknown"

    return df


@lru_cache(maxsize=5)
def get_bulletin_names(state_dir):
    """
    Load and return bulletin names DataFrame for a state.
    Joins with parsed_addresses to add city/address info.
    Returns None if no bulletin data exists.
    """
    path = os.path.join(DATA_DIR, state_dir, "bulletin_names.csv")
    if not os.path.isfile(path):
        return None

    df = pd.read_csv(path, encoding="utf-8-sig")

    # Join with churches to get city + full_street
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

    return {
        "total_names": len(df),
        "unique_names": df["person_name"].nunique(),
        "church_count": df["church_name"].nunique(),
        "city_count": df["city"].nunique() if "city" in df.columns else 0,
    }
