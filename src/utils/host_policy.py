"""
host_policy.py — per-host egress policy: does THIS host need the proxy?

The project default is direct. A proxy is expensive (metered GB), slower, and
has repeatedly made scrapes worse, so it must never be switched on globally to
fix a handful of hosts — that is how unrelated spiders end up tunnelled.

Three states, because two is not enough to describe what we measured:

    direct        fetches fine from the scraper's own egress -> NO proxy
    needs_proxy   fails direct, and the proxy demonstrably FIXES it -> use proxy
    blocked       fails direct AND through the proxy -> proxy is pointless here

`blocked` is the state that matters most. On 2026-08-07 ~1,441 parish hosts
returned 403 from Render while returning 200 from a residential connection —
and the 711 residential proxy ALSO got 403 (0 of 18 fixed). Marking those
"needs_proxy" would burn metered GB for nothing, which is exactly the trap this
table exists to prevent.

Policy is keyed on the HOST, since that is the unit at which blocking happens.
"""

import os
import threading
from urllib.parse import urlparse

PROXY_URL = os.environ.get("PROXY_URL", "").strip() or None

_lock = threading.Lock()
_cache = None  # host -> policy string


def host_of(url):
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def ensure_schema(cur):
    """Create the policy table if absent (idempotent)."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS scrape_host_policy (
            host           VARCHAR(255) NOT NULL,
            policy         VARCHAR(16)  NOT NULL DEFAULT 'direct',
            direct_status  VARCHAR(32)  NULL,
            proxy_status   VARCHAR(32)  NULL,
            checked_from   VARCHAR(64)  NULL,
            checked_at     DATETIME     NULL,
            note           VARCHAR(255) NULL,
            PRIMARY KEY (host),
            KEY idx_policy (policy)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    # Collation must match the rest of church_scrapes. Left at the MySQL 8
    # default (utf8mb4_0900_ai_ci) this table cannot be joined or compared
    # against church.website_url: "Illegal mix of collations ... for operation
    # '='". Fix any table created before this was pinned.
    cur.execute(
        """
        SELECT table_collation FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = 'scrape_host_policy'
        """
    )
    row = cur.fetchone()
    current = (row or {}).get("table_collation") or (row or {}).get("TABLE_COLLATION")
    if current and current != "utf8mb4_unicode_ci":
        cur.execute(
            "ALTER TABLE scrape_host_policy "
            "CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        print("  [OK] scrape_host_policy collation -> utf8mb4_unicode_ci")


def load(cur):
    """Load host -> policy once per process."""
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        policy = {}
        try:
            cur.execute("SELECT host, policy FROM scrape_host_policy")
            for row in cur.fetchall():
                policy[(row["host"] or "").lower()] = row["policy"]
        except Exception:
            # No table yet -> everything direct. Failing open to DIRECT is the
            # safe default: worst case we skip a proxy we might have wanted,
            # rather than tunnelling the whole estate by accident.
            policy = {}
        _cache = policy
        return _cache


def policy_for(url, policy=None):
    """The recorded verdict for this URL's host, or None if unmeasured.

    Exposed because 'blocked' is no longer a dead end. Measured 2026-08-17 on
    eight walled hosts across two states: requests 403, requests+proxy 403,
    browser 403, browser+proxy 200. The WAF wants a residential address AND a
    real browser together, so a blocked host is worth one browser attempt
    THROUGH the proxy before being written off — see find_bulletin_page
    strategy 5.
    """
    if policy is None:
        policy = _cache or {}
    return policy.get(host_of(url))


def proxies_for(url, policy=None):
    """requests-style proxies dict for this URL, or None for direct.

    Returns a proxy ONLY for hosts explicitly labelled needs_proxy. Everything
    else — including hosts we know are blocked — goes direct.

    NOTE: this stays as it is deliberately. Sending plain requests through the
    proxy does NOT rescue a blocked host — measured 403 either way. Only a
    browser through the proxy does, which is handled in the scraper rather than
    here, because it costs a browser launch per call.
    """
    if not PROXY_URL:
        return None
    if policy is None:
        policy = _cache or {}
    if policy.get(host_of(url)) == "needs_proxy":
        return {"http": PROXY_URL, "https": PROXY_URL}
    return None


def set_policy(cur, host, policy, direct_status=None, proxy_status=None,
               checked_from=None, note=None):
    """Record a measured verdict for one host."""
    cur.execute(
        """
        INSERT INTO scrape_host_policy
            (host, policy, direct_status, proxy_status, checked_from, checked_at, note)
        VALUES (%s, %s, %s, %s, %s, NOW(), %s)
        ON DUPLICATE KEY UPDATE
            policy = VALUES(policy),
            direct_status = VALUES(direct_status),
            proxy_status = VALUES(proxy_status),
            checked_from = VALUES(checked_from),
            checked_at = NOW(),
            note = VALUES(note)
        """,
        (host.lower()[:255], policy, direct_status, proxy_status, checked_from,
         (note or "")[:255]),
    )


def classify(direct_code, proxy_code):
    """Verdict from a measured direct/proxy pair.

    A proxy earns 'needs_proxy' only by actually fixing the fetch; a host that
    fails both ways is 'blocked', and sending it through the proxy would spend
    metered bandwidth to get the same 403.
    """
    direct_ok = direct_code == 200
    proxy_ok = proxy_code == 200
    if direct_ok:
        return "direct"
    if proxy_ok:
        return "needs_proxy"
    return "blocked"
