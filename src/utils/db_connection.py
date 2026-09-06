"""
Database connection helper for church_scrapes on db99 RDS.

Reads credentials from AWS Secrets Manager (primary), falls back to .env
when Secrets Manager is unreachable.

Usage:
    from src.utils.db_connection import connection
    with connection() as conn:      # always closed, even on an exception
        ...

    # or, when you must manage it yourself:
    from src.utils.db_connection import get_connection, close_quietly
    conn = get_connection()
    try:
        ...
    finally:
        close_quietly(conn)
"""

import json
import logging
import os
import threading
import time
from contextlib import contextmanager

import pymysql

logger = logging.getLogger(__name__)

_secrets_cache = {}
_SECRET_ID = "/ben/ai-tool/db99"
_DATABASE = "church_scrapes"


def _get_secret():
    """Retrieve credentials from AWS Secrets Manager (cached, 5s timeout)."""
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
    """Get DB credentials: Secrets Manager first, then env fallback."""
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


# db99 is ONE MySQL instance shared by every project: max_connections=1289.
# On 2026-09-02 it reached 1,286 of 1,289 and began refusing connections with
# errno 1040, breaking five unrelated projects.
#
# wait_timeout was long believed to be 28800 (eight hours). MEASURED LIVE
# 2026-09-04 it is 300 SECONDS -- the census dated the change to a 15-minute
# window on 2026-09-03, when the oldest idle connection fell 9,486s -> 182s.
# Whether 300 is permanent is not established, so do not depend on it: still
# close every connection. Two consequences if it holds -- a leak is a
# five-minute problem rather than an overnight one, and any pool_recycle must
# sit BELOW 300 (600 was silently above it).
# See PROJECT_PLAN.md.
ER_CON_COUNT_ERROR = 1040  # "Too many connections"


def get_connection(database=_DATABASE, autocommit=False, attempts=4, base_delay=2.0):
    """Get a MySQL connection to church_scrapes on db99.

    Retries ONLY errno 1040 (ER_CON_COUNT_ERROR), with linear backoff, so a
    connection-ceiling spike caused by another project costs this project a few
    seconds instead of a night's data. Every other error -- bad credentials, an
    unknown database, a genuine outage -- is raised immediately. Retrying
    everything turns a real fault into a slow one, which is harder to diagnose.
    """
    secret = _get_secret() or {}
    host = os.getenv("DB_HOST") or secret.get("DB_HOST") or "db99.rds.blockshopper.com"
    port = int(os.getenv("DB_PORT") or secret.get("DB_PORT") or "3306")
    user, password = _get_credentials()

    last_err = None
    for attempt in range(attempts):
        try:
            return pymysql.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                connect_timeout=30,
                read_timeout=300,
                write_timeout=300,
                autocommit=autocommit,
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
            )
        except pymysql.err.OperationalError as e:
            code = e.args[0] if e.args else None
            if code != ER_CON_COUNT_ERROR:
                raise
            last_err = e
            if attempt < attempts - 1:
                delay = base_delay * (attempt + 1)  # linear: 2s, 4s, 6s
                logger.warning(
                    "[--] db99 at max connections (1040), retry %d/%d in %.0fs",
                    attempt + 1,
                    attempts - 1,
                    delay,
                )
                time.sleep(delay)
    logger.error("[ERR] db99 refused connection (1040) after %d attempts", attempts)
    raise last_err


def close_quietly(conn):
    """Close a connection, swallowing errors. Safe on None and double-close.

    Use in a `finally`. A connection left open is held by db99 for the full
    eight-hour wait_timeout, so 'we closed it on the happy path' is not enough.
    """
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass


@contextmanager
def connection(database=_DATABASE, autocommit=False):
    """Context manager that always closes its connection.

    Prefer this over a bare get_connection() at every new call site.
    """
    conn = get_connection(database=database, autocommit=autocommit)
    try:
        yield conn
    finally:
        close_quietly(conn)
