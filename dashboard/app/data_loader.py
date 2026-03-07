"""
Data Loader — Reads data from db99 MySQL (church_scrapes database).

Replaces the CSV-based data loading with direct database queries.
All data comes from the church_scrapes database on db99.rds.blockshopper.com.
"""

import json
import logging
import os
import threading
from collections import defaultdict
from functools import lru_cache

import pandas as pd
import pymysql

logger = logging.getLogger(__name__)

# ── Module-level globals set during init_data() ─────────────────────────
_state_list = None
_bulletin_stats_cache = {}
_bulletin_filters_cache = {}

# Keep DATA_DIR for backward compat (calendar_download imports it).
# Set to None since we no longer use CSV files.
DATA_DIR = None

# ── State code mappings ─────────────────────────────────────────────────

STATE_DIR_TO_NAME = {
    "alabama": "Alabama",
    "alaska": "Alaska",
    "arizona": "Arizona",
    "arkansas": "Arkansas",
    "california": "California",
    "colorado": "Colorado",
    "connecticut": "Connecticut",
    "delaware": "Delaware",
    "florida": "Florida",
    "georgia": "Georgia",
    "hawaii": "Hawaii",
    "idaho": "Idaho",
    "illinois": "Illinois",
    "indiana": "Indiana",
    "iowa": "Iowa",
    "kansas": "Kansas",
    "kentucky": "Kentucky",
    "louisiana": "Louisiana",
    "maine": "Maine",
    "maryland": "Maryland",
    "massachusetts": "Massachusetts",
    "michigan": "Michigan",
    "minnesota": "Minnesota",
    "mississippi": "Mississippi",
    "missouri": "Missouri",
    "montana": "Montana",
    "nebraska": "Nebraska",
    "nevada": "Nevada",
    "new_hampshire": "New Hampshire",
    "new_jersey": "New Jersey",
    "new_mexico": "New Mexico",
    "new_york": "New York",
    "north_carolina": "North Carolina",
    "north_dakota": "North Dakota",
    "ohio": "Ohio",
    "oklahoma": "Oklahoma",
    "oregon": "Oregon",
    "pennsylvania": "Pennsylvania",
    "rhode_island": "Rhode Island",
    "south_carolina": "South Carolina",
    "south_dakota": "South Dakota",
    "tennessee": "Tennessee",
    "texas": "Texas",
    "utah": "Utah",
    "vermont": "Vermont",
    "virginia": "Virginia",
    "washington": "Washington",
    "west_virginia": "West Virginia",
    "wisconsin": "Wisconsin",
    "wyoming": "Wyoming",
}

STATE_ABBREV_TO_DIR = {
    "AL": "alabama",
    "AK": "alaska",
    "AZ": "arizona",
    "AR": "arkansas",
    "CA": "california",
    "CO": "colorado",
    "CT": "connecticut",
    "DE": "delaware",
    "FL": "florida",
    "GA": "georgia",
    "HI": "hawaii",
    "ID": "idaho",
    "IL": "illinois",
    "IN": "indiana",
    "IA": "iowa",
    "KS": "kansas",
    "KY": "kentucky",
    "LA": "louisiana",
    "ME": "maine",
    "MD": "maryland",
    "MA": "massachusetts",
    "MI": "michigan",
    "MN": "minnesota",
    "MS": "mississippi",
    "MO": "missouri",
    "MT": "montana",
    "NE": "nebraska",
    "NV": "nevada",
    "NH": "new_hampshire",
    "NJ": "new_jersey",
    "NM": "new_mexico",
    "NY": "new_york",
    "NC": "north_carolina",
    "ND": "north_dakota",
    "OH": "ohio",
    "OK": "oklahoma",
    "OR": "oregon",
    "PA": "pennsylvania",
    "RI": "rhode_island",
    "SC": "south_carolina",
    "SD": "south_dakota",
    "TN": "tennessee",
    "TX": "texas",
    "UT": "utah",
    "VT": "vermont",
    "VA": "virginia",
    "WA": "washington",
    "WV": "west_virginia",
    "WI": "wisconsin",
    "WY": "wyoming",
}

STATE_DIR_TO_CODE = {v: k for k, v in STATE_ABBREV_TO_DIR.items()}

# ── Database connection ─────────────────────────────────────────────────

_SECRET_ID = "/ben/ai-tool/db99"
_DATABASE = "church_scrapes"
_secrets_cache = {}


def _get_secret():
    """Retrieve DB credentials from AWS Secrets Manager (cached, 5s timeout)."""
    if _SECRET_ID in _secrets_cache:
        return _secrets_cache[_SECRET_ID]

    result = [None]

    def _fetch():
        try:
            import boto3
            from botocore.config import Config

            config = Config(connect_timeout=3, read_timeout=3, retries={"max_attempts": 0})
            client = boto3.client("secretsmanager", region_name="us-east-1", config=config)
            resp = client.get_secret_value(SecretId=_SECRET_ID)
            result[0] = json.loads(resp["SecretString"])
        except Exception:
            pass

    t = threading.Thread(target=_fetch, daemon=True)
    t.start()
    t.join(timeout=5)

    if result[0]:
        _secrets_cache[_SECRET_ID] = result[0]
    return result[0]


def _get_credentials():
    """Get DB credentials: AWS Secrets Manager first, then env fallback."""
    secret = _get_secret()
    if secret:
        user = secret.get("username") or secret.get("DB_USER") or ""
        password = secret.get("password") or secret.get("DB_PASSWORD") or ""
        if user and password:
            return user, password

    user = os.getenv("DB_USER", "")
    password = os.getenv("DB_PASSWORD", "")
    if user and password:
        return user, password

    raise ValueError(
        "Database credentials not found. Check AWS Secrets Manager access "
        "or set DB_USER/DB_PASSWORD in environment."
    )


def _get_db_connection():
    """Create a new MySQL connection to church_scrapes on db99."""
    host = os.getenv("DB_HOST", "db99.rds.blockshopper.com")
    port = int(os.getenv("DB_PORT", "3306"))
    user, password = _get_credentials()

    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=_DATABASE,
        connect_timeout=30,
        read_timeout=300,
        write_timeout=300,
        autocommit=True,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


# ── Helpers ─────────────────────────────────────────────────────────────


def _format_time(td):
    """Format MySQL TIME (timedelta) as 'H:MM AM/PM' to match original CSV format."""
    if td is None or (isinstance(td, float) and pd.isna(td)) or pd.isna(td):
        return ""
    try:
        total_seconds = int(td.total_seconds())
    except (ValueError, AttributeError):
        return ""
    if total_seconds < 0:
        return ""
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    period = "AM" if hours < 12 else "PM"
    display_hour = hours % 12 or 12
    return f"{display_hour}:{minutes:02d} {period}"


def _state_code(state_dir):
    """Convert state_dir (e.g. 'ohio') to state_code (e.g. 'OH')."""
    return STATE_DIR_TO_CODE.get(state_dir)


# ── Initialization ──────────────────────────────────────────────────────


def init_data(app):
    """
    Called once on app startup. Builds state list and pre-computes
    bulletin stats/filters from db99.
    """
    global _state_list, _bulletin_stats_cache, _bulletin_filters_cache

    app.logger.info("Loading church data from db99...")

    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        # 1. State list with church counts
        cur.execute("""
            SELECT c.state_code, s.state_name, COUNT(*) as church_count
            FROM church c
            JOIN lk_state s ON c.state_code = s.state_code
            GROUP BY c.state_code, s.state_name
            ORDER BY s.state_name
        """)
        state_rows = cur.fetchall()

        # 2. States that have services (use mass_count on church for speed)
        cur.execute("""
            SELECT DISTINCT state_code
            FROM church
            WHERE mass_count > 0 OR confession_count > 0 OR adoration_count > 0
        """)
        states_with_services = {r["state_code"] for r in cur.fetchall()}

        # 3. Bulletin stats from pre-computed summary table (instant)
        cur.execute("SELECT * FROM bulletin_state_stats")
        bulletin_summary = {r["state_code"]: r for r in cur.fetchall()}

        # 5. Church/city combos for filter dropdowns (churches with bulletin sources)
        cur.execute("""
            SELECT c.state_code, c.name AS church_name, COALESCE(c.city, 'Unknown') AS city
            FROM church c
            INNER JOIN bulletin_source bs ON c.church_id = bs.church_id
            ORDER BY c.state_code, c.name, c.city
        """)
        filter_rows = cur.fetchall()

        conn.close()
    except Exception as e:
        app.logger.error(f"Failed to load data from db99: {e}")
        _state_list = []
        return

    # Build state list
    _state_list = []
    for row in state_rows:
        sc = row["state_code"]
        state_dir = STATE_ABBREV_TO_DIR.get(sc)
        if not state_dir:
            continue
        _state_list.append(
            {
                "state_dir": state_dir,
                "state_code": sc,
                "display_name": row["state_name"],
                "church_count": int(row["church_count"]),
                "has_bulletin": sc in bulletin_summary,
                "has_services": sc in states_with_services,
            }
        )
    _state_list.sort(key=lambda x: x["display_name"])

    total_churches = sum(s["church_count"] for s in _state_list)
    app.logger.info(f"Loaded {total_churches} churches across {len(_state_list)} states")

    # Build bulletin stats cache from bulletin_state_stats table
    for sc, bs in bulletin_summary.items():
        state_dir = STATE_ABBREV_TO_DIR.get(sc)
        if not state_dir:
            continue
        _bulletin_stats_cache[state_dir] = {
            "total_names": int(bs["total_names"]),
            "unique_names": int(bs["unique_names"]),
            "church_count": int(bs["church_count"]),
            "city_count": int(bs["city_count"]),
        }

    app.logger.info(f"Pre-computed bulletin stats for {len(_bulletin_stats_cache)} states")

    # Build bulletin filters cache
    state_churches = defaultdict(list)
    for row in filter_rows:
        state_dir = STATE_ABBREV_TO_DIR.get(row["state_code"])
        if state_dir:
            state_churches[state_dir].append((row["church_name"], row["city"]))

    for state_dir, pairs in state_churches.items():
        cities = sorted({city for _, city in pairs})

        church_city_map = defaultdict(list)
        for name, city in pairs:
            if city not in church_city_map[name]:
                church_city_map[name].append(city)

        church_options = []
        for name in sorted(church_city_map.keys()):
            cities_for = sorted(church_city_map[name])
            if len(cities_for) > 1:
                for city in cities_for:
                    church_options.append(
                        {"label": f"{name} ({city})", "church": name, "city": city}
                    )
            else:
                church_options.append({"label": name, "church": name, "city": ""})

        _bulletin_filters_cache[state_dir] = {
            "cities": cities,
            "church_options": church_options,
        }

    app.logger.info(f"Pre-computed bulletin filters for {len(_bulletin_filters_cache)} states")


# ── Public API ──────────────────────────────────────────────────────────


def get_states():
    """Return list of state dicts with counts and data availability."""
    return _state_list


def get_states_with_bulletins():
    """Return list of state dicts that have bulletin name data."""
    return [s for s in _state_list if s["has_bulletin"]]


def get_churches_for_state(state_dir):
    """Return DataFrame of churches for a given state."""
    sc = _state_code(state_dir)
    if not sc:
        return pd.DataFrame()

    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT church_id, slug, name, street, city, state_code, postal_code AS zip5,
                   phone, website_url,
                   CONCAT_WS(', ', NULLIF(street, ''), city,
                             CONCAT(state_code, ' ', postal_code)) AS full_street
            FROM church
            WHERE state_code = %s
            ORDER BY name
            """,
            (sc,),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        logger.error(f"Error loading churches for {state_dir}: {e}")
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["state_dir"] = state_dir
    df["city"] = df["city"].fillna("Unknown")
    df["state_code"] = df["state_code"].fillna("")
    return df


@lru_cache(maxsize=3)
def get_services(state_dir):
    """
    Load and return services DataFrame for a state.
    Returns columns matching the original CSV format expected by routes:
    Church, Address, Phone, Category, Day, Time Start, Time End,
    Service Name, church_slug, city, church_display.
    """
    sc = _state_code(state_dir)
    if not sc:
        return pd.DataFrame()

    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                c.name                                          AS `Church`,
                c.slug                                          AS church_slug,
                CONCAT_WS(', ', NULLIF(c.street, ''), c.city,
                          CONCAT(c.state_code, ' ', c.postal_code))  AS `Address`,
                COALESCE(c.phone, '')                           AS `Phone`,
                COALESCE(c.city, 'Unknown')                     AS city,
                COALESCE(lcat.display_name, 'Other')            AS `Category`,
                COALESCE(d.day_name, '')                        AS `Day`,
                s.time_start                                    AS time_start_raw,
                s.time_end                                      AS time_end_raw,
                COALESCE(s.display_name, '')                    AS `Service Name`,
                COALESCE(lst.display_name, '')                  AS `Schedule Type`,
                COALESCE(ll.display_name, '')                   AS `Language`,
                COALESCE(s.location, '')                        AS `Location`,
                COALESCE(s.notes_raw, '')                       AS `Notes`
            FROM service s
            JOIN church c ON s.church_id = c.church_id
            LEFT JOIN lk_service_category lcat ON s.category_code = lcat.category_code
            LEFT JOIN lk_day_of_week d ON s.day_code = d.day_code
            LEFT JOIN lk_schedule_type lst ON s.schedule_type_code = lst.schedule_type_code
            LEFT JOIN lk_language ll ON s.language_code = ll.language_code
            WHERE c.state_code = %s
              AND s.is_active = 1
              AND s.event_date IS NULL
            ORDER BY c.name, COALESCE(lcat.sort_order, 99),
                     COALESCE(d.sort_order, 99), s.time_start
            """,
            (sc,),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        logger.error(f"Error loading services for {state_dir}: {e}")
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # Format time columns to match CSV format ("9:00 AM")
    df["Time Start"] = df["time_start_raw"].apply(_format_time)
    df["Time End"] = df["time_end_raw"].apply(_format_time)
    df.drop(columns=["time_start_raw", "time_end_raw"], inplace=True)

    # Build church_display: "Church Name (City)" for duplicate names
    if "Church" in df.columns and "city" in df.columns:
        name_counts = df.groupby("Church")["city"].nunique()
        dup_names = set(name_counts[name_counts > 1].index)
        df["church_display"] = df.apply(
            lambda r: f"{r['Church']} ({r['city']})" if r["Church"] in dup_names else r["Church"],
            axis=1,
        )
    else:
        df["church_display"] = df.get("Church", "")

    # Fill NaN for display
    df = df.fillna("")

    return df


@lru_cache(maxsize=8)
def get_bulletin_names(state_dir):
    """
    Load and return bulletin names DataFrame for a state from v_bulletin_ui_names.
    Returns None if no bulletin data exists. Capped at 50,000 rows.
    """
    sc = _state_code(state_dir)
    if not sc or state_dir not in _bulletin_stats_cache:
        return None

    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                person_name,
                COALESCE(title, '')                         AS title,
                COALESCE(first_name, '')                    AS first_name,
                COALESCE(middle_name, '')                   AS middle_name,
                COALESCE(last_name, '')                     AS last_name,
                COALESCE(category, '')                      AS category,
                COALESCE(confidence, '')                    AS confidence,
                is_suspect,
                is_verified,
                COALESCE(church_name, '')                   AS church_name,
                COALESCE(church_city, 'Unknown')            AS city,
                COALESCE(church_street, '')                 AS full_street,
                COALESCE(state_code, '')                    AS state_code,
                COALESCE(church_zip, '')                    AS zip5,
                COALESCE(pdf_url, '')                       AS pdf_url,
                bulletin_date                               AS pdf_date,
                church_id
            FROM v_bulletin_ui_names
            WHERE state_code = %s
            LIMIT 50000
            """,
            (sc,),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        logger.error(f"Error loading bulletin names for {state_dir}: {e}")
        return None

    if not rows:
        return None

    df = pd.DataFrame(rows)
    return df


def get_bulletin_stats(state_dir):
    """Return summary stats for bulletin names in a state (pre-computed)."""
    return _bulletin_stats_cache.get(state_dir)


def get_bulletin_filters(state_dir):
    """Return pre-computed filter dropdown data (cities + church options)."""
    return _bulletin_filters_cache.get(state_dir)


# ── Bulletin names server-side pagination (SQL) ─────────────────────────

_BULLETIN_PAGE_COLS = [
    "person_name",
    "role",
    "title",
    "first_name",
    "last_name",
    "church_name",
    "city",
    "category",
    "confidence",
    "pdf_url",
    "pdf_date",
]

# Map DataTables column index → SQL column name in v_bulletin_ui_names
_COL_INDEX_TO_SQL = {
    0: "person_name",
    1: "title",       # "role" doesn't exist; sort by title instead
    2: "title",
    3: "first_name",
    4: "last_name",
    5: "church_name",
    6: "church_city",
    7: "category",
    8: "confidence",
    9: "pdf_url",
    10: "bulletin_date",
}


def get_bulletin_names_page(
    state_dir,
    start=0,
    length=50,
    search="",
    order_col=0,
    order_dir="asc",
    church_filter="",
    city_filter="",
    category_filter="",
):
    """
    Return a page of bulletin names for DataTables server-side processing.
    Uses SQL LIMIT/OFFSET for efficiency instead of loading all rows.
    Returns (rows_list, total_records, filtered_records).
    """
    sc = _state_code(state_dir)
    if not sc:
        return [], 0, 0

    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        # Base filter
        where = ["state_code = %s"]
        params = [sc]

        if church_filter:
            where.append("church_name = %s")
            params.append(church_filter)
        if city_filter:
            where.append("church_city = %s")
            params.append(city_filter)
        if category_filter:
            where.append("category = %s")
            params.append(category_filter)

        # Total count (unfiltered for this state)
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM v_bulletin_ui_names WHERE state_code = %s",
            (sc,),
        )
        total_records = cur.fetchone()["cnt"]

        # Search across text columns
        if search:
            search_like = f"%{search}%"
            search_cols = [
                "person_name", "title", "first_name", "last_name",
                "church_name", "church_city", "category", "confidence",
            ]
            search_clause = " OR ".join(f"{c} LIKE %s" for c in search_cols)
            where.append(f"({search_clause})")
            params.extend([search_like] * len(search_cols))

        where_sql = " AND ".join(where)

        # Filtered count
        cur.execute(
            f"SELECT COUNT(*) AS cnt FROM v_bulletin_ui_names WHERE {where_sql}",
            params,
        )
        filtered_records = cur.fetchone()["cnt"]

        # Order — whitelist column names to prevent injection
        sort_col = _COL_INDEX_TO_SQL.get(order_col, "person_name")
        sort_dir = "DESC" if order_dir == "desc" else "ASC"

        # Fetch page
        cur.execute(
            f"""
            SELECT person_name, title, first_name, last_name,
                   church_name, church_city, category, confidence,
                   pdf_url, bulletin_date
            FROM v_bulletin_ui_names
            WHERE {where_sql}
            ORDER BY {sort_col} {sort_dir}
            LIMIT %s OFFSET %s
            """,
            params + [length, start],
        )

        rows = []
        for r in cur.fetchall():
            rows.append(
                [
                    r["person_name"] or "",
                    "",  # role (not in DB)
                    r["title"] or "",
                    r["first_name"] or "",
                    r["last_name"] or "",
                    r["church_name"] or "",
                    r["church_city"] or "",
                    r["category"] or "",
                    r["confidence"] or "",
                    r["pdf_url"] or "",
                    str(r["bulletin_date"]) if r["bulletin_date"] else "",
                ]
            )

        conn.close()
        return rows, total_records, filtered_records

    except Exception as e:
        logger.error(f"Error in get_bulletin_names_page: {e}")
        return [], 0, 0


# ── Dated services (calendar) ──────────────────────────────────────────


@lru_cache(maxsize=3)
def get_dated_services(state_dir):
    """Load and return dated services DataFrame for a state (services with event_date)."""
    sc = _state_code(state_dir)
    if not sc:
        return pd.DataFrame()

    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                DATE_FORMAT(s.event_date, '%%a, %%b %%e, %%Y')  AS `Date`,
                DATE_FORMAT(s.event_date, '%%Y-%%m-%%d')         AS `Date Sort`,
                COALESCE(d.day_name, '')                         AS `Day`,
                c.name                                           AS `Church`,
                CONCAT_WS(', ', NULLIF(c.street, ''), c.city)   AS `Address`,
                COALESCE(c.phone, '')                            AS `Phone`,
                COALESCE(lcat.display_name, 'Other')             AS `Category`,
                s.time_start                                     AS time_raw,
                s.time_end                                       AS time_end_raw,
                COALESCE(s.display_name, '')                     AS `Service Name`,
                COALESCE(ll.display_name, '')                    AS `Language`,
                COALESCE(s.location, '')                         AS `Location`,
                COALESCE(s.notes_raw, '')                        AS `Notes`
            FROM service s
            JOIN church c ON s.church_id = c.church_id
            LEFT JOIN lk_service_category lcat ON s.category_code = lcat.category_code
            LEFT JOIN lk_day_of_week d ON s.day_code = d.day_code
            LEFT JOIN lk_language ll ON s.language_code = ll.language_code
            WHERE c.state_code = %s
              AND s.event_date IS NOT NULL
              AND s.is_active = 1
            ORDER BY s.event_date, s.time_start
            """,
            (sc,),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        logger.error(f"Error loading dated services for {state_dir}: {e}")
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["Time"] = df["time_raw"].apply(_format_time)
    df["End Time"] = df["time_end_raw"].apply(_format_time)
    df.drop(columns=["time_raw", "time_end_raw"], inplace=True)
    df = df.fillna("")
    return df


def generate_dated_services_csv(state_dir):
    """Generate CSV content string for dated services download."""
    df = get_dated_services(state_dir)
    if df.empty:
        return None
    return df.to_csv(index=False)


# ── Church lookups ──────────────────────────────────────────────────────


@lru_cache(maxsize=8)
def _load_church_details_jsonl(state_dir):
    """
    Load church details from db99. Returns dict mapping
    church name -> {"website": url, "slug": slug}.

    Function name kept for backward compat with mass_times.py import.
    """
    sc = _state_code(state_dir)
    if not sc:
        return {}

    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT name, COALESCE(website_url, '') AS website_url, slug "
            "FROM church WHERE state_code = %s",
            (sc,),
        )
        lookup = {}
        for row in cur.fetchall():
            name = (row["name"] or "").strip()
            if name:
                lookup[name] = {
                    "website": row["website_url"] or "",
                    "slug": row["slug"] or "",
                }
        conn.close()
        return lookup
    except Exception as e:
        logger.error(f"Error loading church details for {state_dir}: {e}")
        return {}


def get_church_website(state_dir, church_name):
    """Look up a church's resolved website URL."""
    lookup = _load_church_details_jsonl(state_dir)
    info = lookup.get(church_name, {})
    return info.get("website", "")


def get_church_slug(state_dir, church_name):
    """Look up a church's slug."""
    lookup = _load_church_details_jsonl(state_dir)
    info = lookup.get(church_name, {})
    return info.get("slug", "")


def church_has_bulletin_names(state_dir, church_name):
    """Check if a church has any bulletin-extracted names."""
    df = get_bulletin_names(state_dir)
    if df is None or df.empty:
        return False
    return (df["church_name"] == church_name).any()
