"""
Data Loader — Reads data from db99 MySQL (church_scrapes database).

Replaces the CSV-based data loading with direct database queries.
All data comes from the church_scrapes database on db99.rds.blockshopper.com.
"""

import json
import logging
import os
import threading
import time
from collections import defaultdict
from functools import lru_cache

import pandas as pd
import pymysql
from flask import g, has_app_context

logger = logging.getLogger(__name__)

# ── Module-level globals set during init_data() ─────────────────────────
_state_list = None
_bulletin_stats_cache = {}
_bulletin_filters_cache = {}

# False until a load has fully succeeded in THIS worker. Each gunicorn worker
# has its own copy, which is why one worker's failed boot used to be invisible:
# its siblings kept serving real data and the fault looked intermittent.
_data_loaded = False
_last_load_attempt = 0.0
_RELOAD_COOLDOWN_SECONDS = 30.0

# Keep DATA_DIR for backward compat (calendar_download imports it).
# Set to None since we no longer use CSV files.
DATA_DIR = None

# A mass schedule older than this is shown as unverified rather than current.
# The mass-times cron sweeps all 50 states every week, so 30 days is well past
# "we missed a run" and means no live source still lists that church.
STALE_AFTER_DAYS = 30

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
    """Retrieve DB credentials from AWS Secrets Manager (cached, 30s timeout).

    Uses a thread with generous timeout to avoid blocking forever on cold starts
    while still allowing enough time for boto3 import + API call on slow instances.
    """
    if _SECRET_ID in _secrets_cache:
        return _secrets_cache[_SECRET_ID]

    result = [None]
    error = [None]

    def _fetch():
        try:
            import boto3
            from botocore.config import Config

            config = Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 1})
            client = boto3.client("secretsmanager", region_name="us-east-1", config=config)
            resp = client.get_secret_value(SecretId=_SECRET_ID)
            result[0] = json.loads(resp["SecretString"])
        except Exception as e:
            error[0] = str(e)

    t = threading.Thread(target=_fetch, daemon=True)
    t.start()
    t.join(timeout=30)

    if result[0]:
        _secrets_cache[_SECRET_ID] = result[0]
    else:
        logger.error(
            "Failed to fetch secret %s: %s",
            _SECRET_ID,
            error[0] or "thread timed out after 30s",
        )
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


# db99 is ONE MySQL instance shared by every project: max_connections=1289,
# wait_timeout=28800 (EIGHT HOURS). On 2026-09-02 it reached 1,286 of 1,289 and
# began refusing connections with errno 1040, breaking five unrelated projects.
ER_CON_COUNT_ERROR = 1040  # "Too many connections"

# Flask `g` key holding every connection opened during the current app context.
_G_CONNS = "_db99_conns"


def close_db99_conns(exc=None):
    """Close every connection opened during this Flask app context.

    Registered as a teardown_appcontext handler by create_app(). This is the
    safety net: every query function in this module was shaped

        conn = _get_db_connection()
        ...queries...
        conn.close()          # only reached when nothing raises
        ...
        except Exception:
            log and return an empty result

    so any query error stranded a connection for eight hours -- in a gunicorn
    process that never restarts. Twelve call sites across this file and
    app/__init__.py had that shape and not one `finally` between them.

    Patching each site individually would leave the next new route free to
    repeat the bug, so the close is anchored here instead. The existing
    conn.close() calls remain correct and simply make this a no-op.
    """
    conns = g.pop(_G_CONNS, None) or []
    for conn in conns:
        try:
            conn.close()
        except Exception:  # already closed by the caller, or a dead socket
            pass


def _get_db_connection(attempts=4, base_delay=2.0):
    """Create a new MySQL connection to church_scrapes on db99.

    Inside a Flask app context the connection is registered on `g` so
    close_db99_conns() closes it however the view exits. Outside one (the
    cache-loader path, scripts) the behaviour is unchanged.

    Retries ONLY errno 1040 with linear backoff. Every other error is raised
    immediately -- retrying everything turns a real fault into a slow one.
    """
    secret = _get_secret() or {}
    host = os.getenv("DB_HOST") or secret.get("DB_HOST") or "db99.rds.blockshopper.com"
    port = int(os.getenv("DB_PORT") or secret.get("DB_PORT") or "3306")
    user, password = _get_credentials()

    last_err = None
    conn = None
    for attempt in range(attempts):
        try:
            conn = _connect(host, port, user, password)
            break
        except pymysql.err.OperationalError as e:
            code = e.args[0] if e.args else None
            if code != ER_CON_COUNT_ERROR:
                raise
            last_err = e
            if attempt < attempts - 1:
                delay = base_delay * (attempt + 1)  # linear: 2s, 4s, 6s
                logger.warning(
                    "[--] db99 at max connections (1040), retry %d/%d in %.0fs",
                    attempt + 1, attempts - 1, delay,
                )
                time.sleep(delay)
    if conn is None:
        logger.error("[ERR] db99 refused connection (1040) after %d attempts", attempts)
        raise last_err

    if has_app_context():
        conns = g.get(_G_CONNS)
        if conns is None:
            conns = []
            setattr(g, _G_CONNS, conns)
        conns.append(conn)
    return conn


def _connect(host, port, user, password):
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
    """Called once on app startup — see _load_from_db for the retry semantics."""
    _load_from_db(app.logger)


def _ensure_loaded():
    """Reload if the startup load never succeeded.

    The startup load is six heavy queries against db99, and gunicorn boots
    several workers at once. When one of them timed out, the old code set
    _state_list = [] and returned, so THAT worker served "No bulletin name data
    available yet" for the rest of its life while its siblings served the real
    thing — the dashboard looked randomly broken depending on which worker took
    the request, and reloading "fixed" it about half the time.

    A failed load is now a retryable condition rather than a permanent verdict.
    The cooldown stops a dead database from turning every page view into six
    more slow queries.
    """
    global _last_load_attempt

    if _data_loaded:
        return
    now = time.monotonic()
    if now - _last_load_attempt < _RELOAD_COOLDOWN_SECONDS:
        return
    _last_load_attempt = now
    logger.warning("State list is empty; retrying the db99 load")
    _load_from_db(logger)


def _load_from_db(log):
    """Build the state list and precomputed caches. Returns True on success."""
    global _state_list, _bulletin_stats_cache, _bulletin_filters_cache
    global _data_loaded, _last_load_attempt

    _last_load_attempt = time.monotonic()
    logger.info("Loading church data from db99...")

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

        # 5. Last PDF extraction date per state (for monitoring pipeline freshness)
        cur.execute("""
            SELECT c.state_code, MAX(bp.downloaded_at) AS last_updated
            FROM bulletin_pdf bp
            JOIN bulletin_source bs ON bp.bulletin_source_id = bs.bulletin_source_id
            JOIN church c ON bs.church_id = c.church_id
            WHERE bp.text_extracted = 1
            GROUP BY c.state_code
        """)
        last_updated_by_state = {r["state_code"]: r["last_updated"] for r in cur.fetchall()}

        # 6. Church/city combos for filter dropdowns (churches with bulletin sources)
        cur.execute("""
            SELECT c.state_code, c.name AS church_name, COALESCE(c.city, 'Unknown') AS city
            FROM church c
            INNER JOIN bulletin_source bs ON c.church_id = bs.church_id
            ORDER BY c.state_code, c.name, c.city
        """)
        filter_rows = cur.fetchall()

        conn.close()
    except Exception as e:
        # Leave whatever we already have in place — a transient failure must not
        # replace a good state list with an empty one, and must not mark the
        # load done. _ensure_loaded() will try again on the next request.
        logger.error(f"Failed to load data from db99: {e}")
        if _state_list is None:
            _state_list = []
        return False

    # Build state list
    new_state_list = []
    for row in state_rows:
        sc = row["state_code"]
        state_dir = STATE_ABBREV_TO_DIR.get(sc)
        if not state_dir:
            continue
        new_state_list.append(
            {
                "state_dir": state_dir,
                "state_code": sc,
                "display_name": row["state_name"],
                "church_count": int(row["church_count"]),
                "has_bulletin": sc in bulletin_summary,
                "has_services": sc in states_with_services,
            }
        )
    new_state_list.sort(key=lambda x: x["display_name"])
    # Swap in only once it is fully built, so a request landing mid-load sees
    # the previous list rather than a half-populated one.
    _state_list = new_state_list

    total_churches = sum(s["church_count"] for s in _state_list)
    logger.info(f"Loaded {total_churches} churches across {len(_state_list)} states")

    # Build bulletin stats cache from bulletin_state_stats table
    for sc, bs in bulletin_summary.items():
        state_dir = STATE_ABBREV_TO_DIR.get(sc)
        if not state_dir:
            continue
        lu = last_updated_by_state.get(sc)
        _bulletin_stats_cache[state_dir] = {
            "total_names": int(bs["total_names"]),
            "unique_names": int(bs["unique_names"]),
            "church_count": int(bs["church_count"]),
            "city_count": int(bs["city_count"]),
            "last_updated": str(lu.date()) if lu else "",
        }

    logger.info(f"Pre-computed bulletin stats for {len(_bulletin_stats_cache)} states")

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

    logger.info(f"Pre-computed bulletin filters for {len(_bulletin_filters_cache)} states")

    # Only now is the worker genuinely usable.
    _data_loaded = True
    return True


# ── Public API ──────────────────────────────────────────────────────────


def get_states():
    """Return list of state dicts with counts and data availability."""
    _ensure_loaded()
    return _state_list or []


def get_states_with_bulletins():
    """Return list of state dicts that have bulletin name data."""
    _ensure_loaded()
    return [s for s in (_state_list or []) if s["has_bulletin"]]


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
                COALESCE(s.notes_raw, '')                       AS `Notes`,
                -- Surfaced so the UI can say when a schedule stopped being
                -- refreshed. DiscoverMass does not list ~7.2k of our churches
                -- (chapels, missions, seminaries, campus and hospital
                -- ministries that CatholicIndex carried), so their times have
                -- been frozen since CatholicIndex went behind Cloudflare in
                -- April. Showing them undated reads as "current".
                c.last_scraped_at                               AS last_scraped_at
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

    # Staleness, computed once here so every view agrees on the threshold.
    # The mass-times cron refreshes the whole country weekly, so anything past
    # 30 days is not "a run we missed" — it is a church no source still covers.
    # -1 means "never scraped", which is stale by definition — not 0 days ago.
    df["days_since_scrape"] = (
        (
            pd.Timestamp.now().normalize() - pd.to_datetime(df["last_scraped_at"], errors="coerce")
        ).dt.days
    ).fillna(-1).astype(int)
    df["is_stale"] = (df["days_since_scrape"] > STALE_AFTER_DAYS) | (df["days_since_scrape"] < 0)

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


@lru_cache(maxsize=16)
def get_bulletin_names(state_dir, include_low=False):
    """
    Load and return bulletin names DataFrame for a state from v_bulletin_ui_names.
    Returns None if no bulletin data exists. Capped at 50,000 rows.

    By default filters to medium+high confidence non-suspect names.
    Pass include_low=True for the suspect review page.
    """
    sc = _state_code(state_dir)
    if not sc or state_dir not in _bulletin_stats_cache:
        return None

    confidence_filter = ""
    if not include_low:
        confidence_filter = "AND confidence IN ('high', 'medium') AND is_suspect = 0"

    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            f"""
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
            {confidence_filter}
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
    """Return summary stats for bulletin names in a state (pre-computed).

    Routes treat None as "no such state" and abort(404), so on a worker whose
    startup load failed every state 404'd — /bulletin/wisconsin/ was reachable
    or missing depending purely on which worker answered.
    """
    _ensure_loaded()
    return _bulletin_stats_cache.get(state_dir)


def get_bulletin_filters(state_dir):
    """Return pre-computed filter dropdown data (cities + church options)."""
    _ensure_loaded()
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
    1: "title",  # "role" doesn't exist; sort by title instead
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
    confidence_filter="",
):
    """
    Return a page of bulletin names for DataTables server-side processing.
    Uses SQL LIMIT/OFFSET for efficiency instead of loading all rows.
    Returns (rows_list, total_records, filtered_records, unique_filtered).
    """
    sc = _state_code(state_dir)
    if not sc:
        return [], 0, 0, 0

    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        # Base filter — non-suspect names
        where = [
            "state_code = %s",
            "is_suspect = 0",
        ]
        params = [sc]

        # Confidence filter (server-side)
        if confidence_filter == "high":
            where.append("confidence = 'high'")
        elif confidence_filter == "medium":
            where.append("confidence IN ('high', 'medium')")
        elif confidence_filter == "low":
            where.append("confidence = 'low'")
        else:
            # Default: medium+high
            where.append("confidence IN ('high', 'medium')")

        if church_filter:
            where.append("church_name = %s")
            params.append(church_filter)
        if city_filter:
            where.append("church_city = %s")
            params.append(city_filter)
        if category_filter:
            where.append("category = %s")
            params.append(category_filter)

        # Baseline count, grouped the same way the table is — otherwise the
        # "filtered from N total" footer compares people against mentions.
        cur.execute(
            """SELECT COUNT(*) AS cnt FROM (
                   SELECT 1 FROM v_bulletin_ui_names
                   WHERE state_code = %s AND confidence IN ('high','medium')
                     AND is_suspect = 0
                   GROUP BY person_name, church_name) t""",
            (sc,),
        )
        total_records = cur.fetchone()["cnt"]

        # Search across text columns
        if search:
            search_like = f"%{search}%"
            search_cols = [
                "person_name",
                "title",
                "first_name",
                "last_name",
                "church_name",
                "church_city",
                "category",
                "confidence",
            ]
            search_clause = " OR ".join(f"{c} LIKE %s" for c in search_cols)
            where.append(f"({search_clause})")
            params.extend([search_like] * len(search_cols))

        where_sql = " AND ".join(where)

        # Filtered count + unique names count.
        #
        # cnt counts one row per person-per-church, matching what the table now
        # lists. It used to count raw mentions: Wisconsin read "398,086 entries"
        # while showing Madelyn Barr four times at the same parish, because she
        # is named in four different bulletins there.
        #
        # uniq stays a STATEWIDE distinct-person count — it answers "how many
        # different people", so someone serving at two parishes counts once.
        # That is why the two numbers differ (WI high: 28,478 vs 15,004).
        cur.execute(
            f"""SELECT COUNT(*) AS cnt FROM (
                    SELECT 1 FROM v_bulletin_ui_names WHERE {where_sql}
                    GROUP BY person_name, church_name) t""",
            params,
        )
        filtered_records = cur.fetchone()["cnt"]

        # Counted over the raw rows, not the grouped set: collapsing first,
        # then counting distinct names, loses people whose name was split
        # differently in different bulletins (14,995 vs the true 15,004).
        cur.execute(
            f"""SELECT COUNT(DISTINCT CONCAT(first_name, '|', last_name)) AS uniq
                FROM v_bulletin_ui_names WHERE {where_sql}""",
            params,
        )
        unique_filtered = cur.fetchone()["uniq"]

        # Order — whitelist column names to prevent injection
        sort_col = _COL_INDEX_TO_SQL.get(order_col, "person_name")
        sort_dir = "DESC" if order_dir == "desc" else "ASC"

        # Fetch page — one row per person per church.
        #
        # pdf_url is taken from the person's MOST RECENT bulletin at that
        # church rather than an arbitrary one, so the provenance link still
        # points at a real document. Packing date and url into one string and
        # taking MAX() picks the pair together; CHAR(31) is the separator
        # because it cannot occur in a URL.
        cur.execute(
            f"""
            SELECT person_name,
                   MAX(title)                AS title,
                   MAX(first_name)           AS first_name,
                   MAX(last_name)            AS last_name,
                   church_name,
                   MAX(church_city)          AS church_city,
                   MAX(category)             AS category,
                   MAX(confidence)           AS confidence,
                   SUBSTRING_INDEX(
                       MAX(CONCAT(COALESCE(bulletin_date, '1000-01-01'),
                                  CHAR(31), COALESCE(pdf_url, ''))),
                       CHAR(31), -1)         AS pdf_url,
                   MAX(bulletin_date)        AS bulletin_date
            FROM v_bulletin_ui_names
            WHERE {where_sql}
            GROUP BY person_name, church_name
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
                    "",  # role (not in DB yet)
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
        return rows, total_records, filtered_records, unique_filtered

    except Exception as e:
        logger.error(f"Error in get_bulletin_names_page: {e}")
        return [], 0, 0, 0


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
