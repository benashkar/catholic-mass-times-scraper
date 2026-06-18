"""
cr_markets.py — Cherry Road market geography (the relationship model), one place.

Limpar (Lumen Postgres) is the SOURCE OF TRUTH for which geographies (shapes) belong to
each Cherry Road project/market. We cache that mapping in the db99 `cr_market_shape`
table so the church pipeline can join coverage against it in SQL and re-sync whenever the
Limpar relationships change.

This module centralizes everything that defines the relationship so there's a single place
to evolve it: the Limpar query, the table schema, the city-name normalizer, and both
connections. Refresh + coverage scripts import from here.

Reference: C:\\Users\\cashk\\OneDrive\\Projects\\global-config\\limpar_queries.md
"""

import json
import re

# One row per shape (geography) in a non-archived Cherry Road project's coverage area.
# project_pipeline_id is the bridge to jns_wire communities.id (production side).
LIMPAR_QUERY = """
SELECT n.name AS network, p.name AS project, p.pipeline_id AS project_pipeline_id,
       p.shape_kind AS project_shape_type, s.name AS shape, s.kind AS shape_type, s.state_abbr
FROM pipeline_networks  n
JOIN pipeline_projects  p  ON p.network_id = n.id
JOIN project_shapes     ps ON ps.project_id = p.id
JOIN shapes             s  ON s.id = ps.shape_id
WHERE n.name ILIKE %s AND p.archived_at IS NULL
ORDER BY p.name, s.name;
"""

NETWORK_FILTER = "%cherry road%"

TABLE = "cr_market_shape"

# MySQL DDL. Idempotent — new standalone table, no impact on hot tables (no MDL contention).
TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    network             VARCHAR(255) NOT NULL,
    project             VARCHAR(255) NOT NULL,
    project_pipeline_id BIGINT       NULL,
    project_shape_type  VARCHAR(64)  NULL,
    shape               VARCHAR(255) NOT NULL,
    shape_type          VARCHAR(64)  NULL,
    state_abbr          VARCHAR(8)   NULL,
    shape_norm          VARCHAR(255) NULL,
    refreshed_at        DATETIME     NOT NULL,
    KEY idx_state_norm (state_abbr, shape_norm),
    KEY idx_project (project),
    KEY idx_shape_type (shape_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# Columns pulled from Limpar (order matches LIMPAR_QUERY).
LIMPAR_COLUMNS = [
    "network", "project", "project_pipeline_id", "project_shape_type",
    "shape", "shape_type", "state_abbr",
]

_ABBR = {"st": "saint", "ste": "sainte", "ft": "fort", "mt": "mount", "mtn": "mountain"}


def norm_city(name: str) -> str:
    """Normalize a city name for safe matching across Limpar and CatholicIndex spellings.

    Lowercases, drops punctuation, and expands common abbreviations (St.->Saint,
    Ft.->Fort, Mt.->Mount) so "St. Louis" and "Saint Louis" match. Both sides of the
    coverage join MUST be normalized through this same function.
    """
    c = (name or "").lower().strip()
    c = re.sub(r"[.\-']", " ", c)
    parts = [_ABBR.get(p, p) for p in c.split()]
    return re.sub(r"\s+", "", "".join(parts))


def get_limpar_connection():
    """Read-only Limpar connection. Local VPN IP, TLS off (blackholes over the VPN)."""
    import boto3
    import psycopg2

    secret = json.loads(
        boto3.client("secretsmanager", region_name="us-east-1")
        .get_secret_value(SecretId="/ben/ai-tool/limpar")["SecretString"]
    )
    return psycopg2.connect(
        host="10.11.1.124",
        port=int(secret.get("DB_PORT", 5432)),
        user=secret["DB_USER"],
        password=secret["DB_PASSWORD"],
        dbname="limpar",
        connect_timeout=10,
        sslmode="disable",
        options="-c statement_timeout=60000",
    )
