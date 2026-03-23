"""
Database connection helper for church_scrapes on db99 RDS.

Reads credentials from AWS Secrets Manager (primary), falls back to .env
when Secrets Manager is unreachable.

Usage:
    from src.utils.db_connection import get_connection
    conn = get_connection()
"""

import json
import os
import threading

import pymysql

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


def get_connection(database=_DATABASE, autocommit=False):
    """Get a MySQL connection to church_scrapes on db99."""
    secret = _get_secret() or {}
    host = os.getenv("DB_HOST") or secret.get("DB_HOST") or "db99.rds.blockshopper.com"
    port = int(os.getenv("DB_PORT") or secret.get("DB_PORT") or "3306")
    user, password = _get_credentials()

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
