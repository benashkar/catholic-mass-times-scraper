"""
health_checks.py — Structured health check functions for the church_scrapes pipeline.

Returns dict results (not just print output) so they can be served as JSON
from the /health endpoint and also used by the CLI test runner.
"""

from datetime import UTC, datetime


def check_table_counts(cur):
    """Verify core tables have expected row counts."""
    tables = {
        "church": 20000,
        "service": 200000,
        "bulletin_name": 1000000,
        "bulletin_pdf": 200000,
        "bulletin_source": 4000,
        "bulletin_state_stats": 40,
        "ref_ssa_names": 90000,
        "ref_census_surnames": 150000,
    }
    counts = {}
    issues = []
    for table, min_expected in tables.items():
        try:
            cur.execute(f"SELECT COUNT(*) AS cnt FROM {table}")
            count = cur.fetchone()["cnt"]
            counts[table] = count
            if count < min_expected:
                issues.append(f"{table}: {count:,} (expected {min_expected:,}+)")
        except Exception as e:
            counts[table] = None
            issues.append(f"{table}: query failed — {e}")
    return {"counts": counts, "issues": issues}


def check_confidence_distribution(cur):
    """Verify confidence distribution is reasonable."""
    cur.execute("""
        SELECT confidence, COUNT(*) cnt,
               ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM bulletin_name), 1) AS pct
        FROM bulletin_name GROUP BY confidence ORDER BY confidence
    """)
    distribution = {}
    issues = []
    for r in cur.fetchall():
        conf = r["confidence"] or "(empty)"
        distribution[conf] = {"count": r["cnt"], "pct": float(r["pct"] or 0)}
        if conf == "low" and r["pct"] and r["pct"] > 50:
            issues.append(f"Low confidence is {r['pct']}% — scoring may be broken")
        if conf == "(empty)" and r["cnt"] > 1000:
            issues.append(f"{r['cnt']:,} names have no confidence score")
    return {"distribution": distribution, "issues": issues}


def check_junk_rate_by_state(cur, state=None):
    """Check junk (low/suspect) rate per state — flag outliers."""
    where = f"AND c.state_code = '{state}'" if state else ""
    cur.execute(f"""
        SELECT c.state_code,
               COUNT(*) AS total,
               SUM(CASE WHEN bn.confidence = 'low' OR bn.is_suspect = 1 THEN 1 ELSE 0 END) AS junk,
               ROUND(SUM(CASE WHEN bn.confidence = 'low' OR bn.is_suspect = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS junk_pct
        FROM bulletin_name bn
        JOIN bulletin_pdf bp ON bn.bulletin_pdf_id = bp.bulletin_pdf_id
        JOIN bulletin_source bs ON bp.bulletin_source_id = bs.bulletin_source_id
        JOIN church c ON bs.church_id = c.church_id
        {where}
        GROUP BY c.state_code
        HAVING junk_pct > 30
        ORDER BY junk_pct DESC
    """)
    flagged_states = []
    issues = []
    for r in cur.fetchall():
        flagged_states.append(
            {
                "state": r["state_code"],
                "junk_pct": float(r["junk_pct"]),
                "junk": r["junk"],
                "total": r["total"],
            }
        )
        if r["junk_pct"] > 50:
            issues.append(f"{r['state_code']} has {r['junk_pct']}% junk rate")
    return {"flagged_states": flagged_states, "issues": issues}


def check_known_junk_still_present(cur):
    """Verify known junk terms are not in high/medium confidence."""
    junk_terms = [
        "Alzheimer",
        "Fish Fry",
        "Cancer",
        "Access Code",
        "Full Page",
        "Bulletin",
        "Sacrament",
        "Committee",
        "Receptionist",
    ]
    found = {}
    issues = []
    for term in junk_terms:
        cur.execute(
            "SELECT COUNT(*) cnt FROM bulletin_name "
            "WHERE confidence IN ('high','medium') AND is_suspect = 0 "
            "AND person_name LIKE %s",
            (f"%{term}%",),
        )
        cnt = cur.fetchone()["cnt"]
        if cnt > 0:
            found[term] = cnt
            issues.append(f"'{term}' found in {cnt} high/medium names")
    return {"found": found, "issues": issues}


def check_lowercase_names(cur):
    """Verify no all-lowercase names in high/medium."""
    cur.execute("""
        SELECT COUNT(*) cnt FROM bulletin_name
        WHERE confidence IN ('high','medium') AND is_suspect = 0
        AND person_name = BINARY LOWER(person_name) AND person_name REGEXP '^[a-z]'
    """)
    cnt = cur.fetchone()["cnt"]
    issues = []
    if cnt > 0:
        issues.append(f"{cnt:,} lowercase names still scored high/medium")
    return {"count": cnt, "issues": issues}


def check_scrape_recency(cur):
    """Check if scrapes are running on schedule."""
    cur.execute("""
        SELECT scrape_type, MAX(completed_at) AS last_run,
               TIMESTAMPDIFF(HOUR, MAX(completed_at), NOW()) AS hours_ago
        FROM scrape_log
        GROUP BY scrape_type
    """)
    recency = {}
    issues = []
    rows = cur.fetchall()
    if not rows:
        issues.append("scrape_log is empty — pipeline may not be logging runs")
    else:
        for r in rows:
            hours = r["hours_ago"] or 999
            recency[r["scrape_type"]] = {
                "last_run": r["last_run"].isoformat() if r["last_run"] else None,
                "hours_ago": hours,
            }
            if hours > 48:
                issues.append(f"{r['scrape_type']} last ran {hours}h ago")
    return {"recency": recency, "issues": issues}


def check_empty_names(cur):
    """Check for records with empty first or last names in high/medium."""
    cur.execute("""
        SELECT COUNT(*) cnt FROM bulletin_name
        WHERE confidence IN ('high','medium') AND is_suspect = 0
        AND (first_name = '' OR last_name = '')
    """)
    cnt = cur.fetchone()["cnt"]
    issues = []
    if cnt > 0:
        issues.append(f"{cnt:,} names missing first/last in high/medium")
    return {"count": cnt, "issues": issues}


def check_backfill_coverage(cur):
    """How many high/medium rows still have empty critical fields?"""
    cur.execute("""
        SELECT
            SUM(CASE WHEN first_name = '' OR last_name = '' THEN 1 ELSE 0 END) AS empty_name_parts,
            SUM(CASE WHEN category = '' THEN 1 ELSE 0 END) AS empty_category,
            SUM(CASE WHEN role = '' THEN 1 ELSE 0 END) AS empty_role,
            COUNT(*) AS total
        FROM bulletin_name
        WHERE confidence IN ('high', 'medium') AND is_suspect = 0
    """)
    row = cur.fetchone()
    return {
        "empty_name_parts": row["empty_name_parts"] or 0,
        "empty_category": row["empty_category"] or 0,
        "empty_role": row["empty_role"] or 0,
        "total_high_medium": row["total"] or 0,
        "issues": [],
    }


def check_recent_pipeline_runs(cur):
    """Last 10 scrape_log entries for recent pipeline visibility."""
    cur.execute("""
        SELECT scrape_type, completed_at, status, notes
        FROM scrape_log
        ORDER BY completed_at DESC
        LIMIT 10
    """)
    runs = []
    for r in cur.fetchall():
        runs.append(
            {
                "scrape_type": r["scrape_type"],
                "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
                "status": r["status"],
                "notes": r["notes"],
            }
        )
    return {"recent_runs": runs, "issues": []}


def run_all_checks(cur, quick=False):
    """Run all health checks and return combined result."""
    results = {}
    results["table_counts"] = check_table_counts(cur)

    if not quick:
        results["confidence_distribution"] = check_confidence_distribution(cur)
        results["junk_rate_by_state"] = check_junk_rate_by_state(cur)
        results["known_junk"] = check_known_junk_still_present(cur)
        results["lowercase_names"] = check_lowercase_names(cur)
        results["scrape_recency"] = check_scrape_recency(cur)
        results["empty_names"] = check_empty_names(cur)
        results["backfill_coverage"] = check_backfill_coverage(cur)
        results["recent_pipeline_runs"] = check_recent_pipeline_runs(cur)

    all_issues = []
    for check_result in results.values():
        all_issues.extend(check_result.get("issues", []))

    return {
        "status": "healthy" if not all_issues else "degraded",
        "checks": results,
        "all_issues": all_issues,
        "issue_count": len(all_issues),
        "checked_at": datetime.now(UTC).isoformat(),
    }
