"""
run_resolve_urls.py

Resolve CatholicIndex /api/out redirect URLs to actual church website URLs.

CatholicIndex stores website links as signed redirect URLs (/api/out?id=...&sig=...).
These serve an interstitial "Leaving Catholic Index" page with a JS redirect containing
the actual URL: window.location.href = "https://realchurchsite.org"

This script fetches each redirect page, parses the real URL, and updates the JSONL
data files with a new 'website_resolved' field on each church record.

HOW TO RUN:
    python run_resolve_urls.py ohio              # Single state
    python run_resolve_urls.py ohio texas        # Multiple states
    python run_resolve_urls.py all               # All states
    python run_resolve_urls.py ohio --resume     # Resume interrupted run
    python run_resolve_urls.py ohio --limit 10   # Test with first 10 churches

OUTPUT:
    Updates data/output/{state}/church_details.jsonl in-place (adds website_resolved field)
    Creates data/output/{state}/resolve_progress.json for resume support

EXPECTED RUNTIME:
    ~0.5s per church. Ohio (1,239 churches) ≈ 10 min. All 50 states ≈ 4-5 hours.
"""

import sys
import json
import time
import re
import argparse
import requests
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import OUTPUT_DIR
from src.utils.file_io import save_to_json, load_from_json
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Rate limit: 1.5s between requests to avoid Cloudflare rate limiting (429)
# The /api/out endpoint has aggressive Cloudflare protection. If we go faster,
# we get error 1015 (rate limited). 1.5s matches the main scraper delay.
RESOLVE_DELAY = 1.5
PROGRESS_SAVE_INTERVAL = 25
REQUEST_TIMEOUT = 10
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

_last_request_time = 0.0

# State aliases — same as run_parse_addresses.py
STATE_ALIASES = {
    "alabama": ("AL", "alabama"), "al": ("AL", "alabama"),
    "alaska": ("AK", "alaska"), "ak": ("AK", "alaska"),
    "arizona": ("AZ", "arizona"), "az": ("AZ", "arizona"),
    "arkansas": ("AR", "arkansas"), "ar": ("AR", "arkansas"),
    "california": ("CA", "california"), "ca": ("CA", "california"),
    "colorado": ("CO", "colorado"), "co": ("CO", "colorado"),
    "connecticut": ("CT", "connecticut"), "ct": ("CT", "connecticut"),
    "delaware": ("DE", "delaware"), "de": ("DE", "delaware"),
    "florida": ("FL", "florida"), "fl": ("FL", "florida"),
    "georgia": ("GA", "georgia"), "ga": ("GA", "georgia"),
    "hawaii": ("HI", "hawaii"), "hi": ("HI", "hawaii"),
    "idaho": ("ID", "idaho"), "id": ("ID", "idaho"),
    "illinois": ("IL", "illinois"), "il": ("IL", "illinois"),
    "indiana": ("IN", "indiana"), "in": ("IN", "indiana"),
    "iowa": ("IA", "iowa"), "ia": ("IA", "iowa"),
    "kansas": ("KS", "kansas"), "ks": ("KS", "kansas"),
    "kentucky": ("KY", "kentucky"), "ky": ("KY", "kentucky"),
    "louisiana": ("LA", "louisiana"), "la": ("LA", "louisiana"),
    "maine": ("ME", "maine"), "me": ("ME", "maine"),
    "maryland": ("MD", "maryland"), "md": ("MD", "maryland"),
    "massachusetts": ("MA", "massachusetts"), "ma": ("MA", "massachusetts"),
    "michigan": ("MI", "michigan"), "mi": ("MI", "michigan"),
    "minnesota": ("MN", "minnesota"), "mn": ("MN", "minnesota"),
    "mississippi": ("MS", "mississippi"), "ms": ("MS", "mississippi"),
    "missouri": ("MO", "missouri"), "mo": ("MO", "missouri"),
    "montana": ("MT", "montana"), "mt": ("MT", "montana"),
    "nebraska": ("NE", "nebraska"), "ne": ("NE", "nebraska"),
    "nevada": ("NV", "nevada"), "nv": ("NV", "nevada"),
    "new_hampshire": ("NH", "new_hampshire"), "nh": ("NH", "new_hampshire"),
    "new_jersey": ("NJ", "new_jersey"), "nj": ("NJ", "new_jersey"),
    "new_mexico": ("NM", "new_mexico"), "nm": ("NM", "new_mexico"),
    "new_york": ("NY", "new_york"), "ny": ("NY", "new_york"),
    "north_carolina": ("NC", "north_carolina"), "nc": ("NC", "north_carolina"),
    "north_dakota": ("ND", "north_dakota"), "nd": ("ND", "north_dakota"),
    "ohio": ("OH", "ohio"), "oh": ("OH", "ohio"),
    "oklahoma": ("OK", "oklahoma"), "ok": ("OK", "oklahoma"),
    "oregon": ("OR", "oregon"), "or": ("OR", "oregon"),
    "pennsylvania": ("PA", "pennsylvania"), "pa": ("PA", "pennsylvania"),
    "rhode_island": ("RI", "rhode_island"), "ri": ("RI", "rhode_island"),
    "south_carolina": ("SC", "south_carolina"), "sc": ("SC", "south_carolina"),
    "south_dakota": ("SD", "south_dakota"), "sd": ("SD", "south_dakota"),
    "tennessee": ("TN", "tennessee"), "tn": ("TN", "tennessee"),
    "texas": ("TX", "texas"), "tx": ("TX", "texas"),
    "utah": ("UT", "utah"), "ut": ("UT", "utah"),
    "vermont": ("VT", "vermont"), "vt": ("VT", "vermont"),
    "virginia": ("VA", "virginia"), "va": ("VA", "virginia"),
    "washington": ("WA", "washington"), "wa": ("WA", "washington"),
    "west_virginia": ("WV", "west_virginia"), "wv": ("WV", "west_virginia"),
    "wisconsin": ("WI", "wisconsin"), "wi": ("WI", "wisconsin"),
    "wyoming": ("WY", "wyoming"), "wy": ("WY", "wyoming"),
    "dc": ("DC", "dc"),
}


def resolve_state(name: str) -> tuple[str, str] | None:
    key = name.lower().replace(" ", "_").replace("-", "_")
    return STATE_ALIASES.get(key)


def _rate_limited_get(url: str) -> requests.Response | None:
    """
    Make a rate-limited GET request.

    Returns the Response object, or None on failure.
    """
    global _last_request_time

    elapsed = time.time() - _last_request_time
    if elapsed < RESOLVE_DELAY:
        time.sleep(RESOLVE_DELAY - elapsed)

    _last_request_time = time.time()

    try:
        resp = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        return resp
    except requests.RequestException as e:
        logger.debug(f"Request failed for {url}: {e}")
        return None


def resolve_redirect_url(redirect_path: str, slug: str = "") -> str | None:
    """
    Resolve a CatholicIndex /api/out redirect URL to the actual church website URL.

    Two-step approach:
    1. First try using the stored redirect URL directly.
    2. If that fails (expired signature / Cloudflare 429), fetch the church's detail
       page to get a fresh signed URL, then follow that.

    The interstitial page contains: window.location.href = "https://actualsite.org";
    We parse that to get the real church website URL.

    Args:
        redirect_path: The /api/out?... path from CatholicIndex
        slug: The church slug (used to fetch fresh URL if stored one is expired)

    Returns:
        The actual church website URL, or None on failure.
    """
    if not redirect_path or redirect_path == "#" or "/api/out" not in redirect_path:
        return None

    # Step 1: Try the stored redirect URL directly
    full_url = f"https://catholicindex.org{redirect_path}"
    resp = _rate_limited_get(full_url)

    if resp and resp.status_code == 200:
        # Parse window.location.href = "..." from the interstitial page
        match = re.search(r'window\.location\.href\s*=\s*"([^"]+)"', resp.text)
        if match:
            return match.group(1)

        # Fallback: extract URL from <div class="url">https://...</div>
        # (newer CatholicIndex interstitial page format as of Feb 2026)
        url_div = re.search(r'class="url"[^>]*>(https?://[^<]+)<', resp.text)
        if url_div:
            return url_div.group(1).strip()

        # Fallback: look for any non-CatholicIndex URL in the page
        urls = re.findall(r'href="(https?://[^"]+)"', resp.text)
        for url in urls:
            if "catholicindex.org" not in url and "cloudflare" not in url:
                return url

    # Step 2: If stored URL failed (expired sig, 429, etc.), fetch fresh URL from
    # the church detail page
    if slug:
        church_url = f"https://catholicindex.org/churches/{slug}"
        resp2 = _rate_limited_get(church_url)

        if resp2 and resp2.status_code == 200:
            # Extract the fresh signed website URL from the page
            pattern = (
                r'/api/out\?id=' + re.escape(slug)
                + r'&type=website&t=\d+&sig=[A-Za-z0-9_\-=]+'
            )
            fresh_match = re.search(pattern, resp2.text)
            if fresh_match:
                fresh_url = f"https://catholicindex.org{fresh_match.group(0)}"
                resp3 = _rate_limited_get(fresh_url)

                if resp3 and resp3.status_code == 200:
                    match = re.search(
                        r'window\.location\.href\s*=\s*"([^"]+)"', resp3.text
                    )
                    if match:
                        return match.group(1)

                    # Fallback: <div class="url">https://...</div>
                    url_div = re.search(
                        r'class="url"[^>]*>(https?://[^<]+)<', resp3.text
                    )
                    if url_div:
                        return url_div.group(1).strip()

    return None


def resolve_urls_for_state(
    state_code: str,
    dir_name: str,
    resume: bool = False,
    limit: int | None = None,
    retry_failed: bool = False,
):
    """
    Resolve all website redirect URLs for a state's churches.

    Reads church_details.jsonl, resolves each /api/out URL, writes the resolved
    URL back into the JSONL as church.website_resolved.

    Args:
        state_code: 2-letter state code
        dir_name: Output directory name
        resume: If True, skip already-resolved churches
        limit: If set, only process first N churches (for testing)
    """
    jsonl_path = OUTPUT_DIR / dir_name / "church_details.jsonl"
    progress_path = OUTPUT_DIR / dir_name / "resolve_progress.json"

    if not jsonl_path.exists():
        logger.error(f"No JSONL file for {state_code}: {jsonl_path}")
        return

    # Load all records
    all_records = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                all_records.append(json.loads(line))

    # When using --limit, only process first N but keep all records for rewrite
    records = all_records[:limit] if limit else all_records
    total = len(records)

    # Load progress for resume
    # --resume: skip all churches in completed_slugs (even if they failed)
    # --retry-failed: only skip churches that SUCCESSFULLY resolved (have a real URL)
    completed_slugs = set()
    if resume and progress_path.exists():
        prog = load_from_json(progress_path)
        if prog:
            if retry_failed:
                # Only skip churches that already have a real resolved URL
                # This retries churches where resolve_redirect_url returned None
                successfully_resolved = set()
                for record in all_records:
                    ch = record.get("church", {})
                    s = ch.get("slug", "")
                    wr = ch.get("website_resolved")
                    ws = ch.get("website", "") or ""
                    # Skip if: has a real URL, OR has no website to resolve
                    if wr:
                        successfully_resolved.add(s)
                    elif not ws or ws == "#" or "/api/out" not in ws:
                        successfully_resolved.add(s)
                completed_slugs = successfully_resolved
                total_in_prog = len(prog.get("completed_slugs", []))
                retry_count = total_in_prog - len(completed_slugs)
                logger.info(
                    f"Retry-failed mode: {len(completed_slugs)} already resolved, "
                    f"{retry_count} failed churches will be retried"
                )
            else:
                completed_slugs = set(prog.get("completed_slugs", []))
                logger.info(f"Resuming — {len(completed_slugs)} already resolved")

    logger.info("=" * 60)
    logger.info(f"RESOLVING URLS: {state_code} ({total} churches)")
    logger.info("=" * 60)

    start_time = time.time()
    resolved_count = 0
    skipped_count = 0
    failed_count = 0
    no_website_count = 0
    processed_this_run = 0

    for i, record in enumerate(records):
        church = record.get("church", {})
        slug = church.get("slug", "")
        name = church.get("name", "?")
        website = church.get("website") or ""

        # Skip if already resolved
        if slug in completed_slugs:
            skipped_count += 1
            continue

        # Skip if no website or already a real URL
        if not website or website == "#" or "/api/out" not in website:
            # Already resolved or no website — mark as done
            if website and website != "#" and "/api/out" not in website:
                # Already a real URL (shouldn't happen but handle it)
                church["website_resolved"] = website
            else:
                church["website_resolved"] = None
            no_website_count += 1
            completed_slugs.add(slug)
            processed_this_run += 1
            continue

        # Resolve the redirect URL (pass slug for fresh-URL fallback)
        actual_url = resolve_redirect_url(website, slug=slug)
        church["website_resolved"] = actual_url
        completed_slugs.add(slug)
        processed_this_run += 1

        if actual_url:
            resolved_count += 1
        else:
            failed_count += 1

        # Progress display
        done = len(completed_slugs)
        elapsed = time.time() - start_time
        if processed_this_run > 0:
            rate = elapsed / processed_this_run
            remaining = total - done
            eta_min = (remaining * rate) / 60
            logger.info(
                f"[{done}/{total}] {name}: "
                f"{actual_url or 'FAILED'} | ETA: {eta_min:.1f}m"
            )

        # Periodic progress save
        if processed_this_run % PROGRESS_SAVE_INTERVAL == 0:
            save_to_json({"completed_slugs": list(completed_slugs)}, progress_path)

    # Final progress save
    save_to_json({"completed_slugs": list(completed_slugs)}, progress_path)

    # Rewrite the JSONL file with resolved URLs (all records, not just processed ones)
    logger.info("\nRewriting JSONL with resolved URLs...")
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for record in all_records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    elapsed_total = (time.time() - start_time) / 60
    logger.info(f"\n{'=' * 60}")
    logger.info(f"URL RESOLUTION COMPLETE: {state_code}")
    logger.info(f"Total churches: {total}")
    logger.info(f"Resolved: {resolved_count}")
    logger.info(f"Failed: {failed_count}")
    logger.info(f"No website: {no_website_count}")
    logger.info(f"Skipped (already done): {skipped_count}")
    logger.info(f"Time: {elapsed_total:.1f} minutes")
    logger.info(f"{'=' * 60}")


def run_resolve(
    states: list[str],
    resume: bool = False,
    limit: int | None = None,
    retry_failed: bool = False,
):
    """Resolve URLs for one or more states."""
    for state_input in states:
        if state_input.lower() == "all":
            found_dirs = sorted(
                d.name
                for d in OUTPUT_DIR.iterdir()
                if d.is_dir() and (d / "church_details.jsonl").exists()
            )
            if not found_dirs:
                logger.error("No scraped states found.")
                return
            logger.info(f"Found {len(found_dirs)} states to process")
            for dir_name in found_dirs:
                # Look up state code from dir name
                matched = STATE_ALIASES.get(dir_name)
                code = matched[0] if matched else dir_name.upper()
                resolve_urls_for_state(
                    code, dir_name, resume=resume, limit=limit,
                    retry_failed=retry_failed,
                )
            return

        resolved = resolve_state(state_input)
        if not resolved:
            logger.error(f"Unknown state: '{state_input}'")
            continue
        state_code, dir_name = resolved
        resolve_urls_for_state(
            state_code, dir_name, resume=resume, limit=limit,
            retry_failed=retry_failed,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Resolve CatholicIndex redirect URLs to actual church websites."
    )
    parser.add_argument(
        "states",
        nargs="+",
        help="States to process (e.g., ohio texas). Use 'all' for all states.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume interrupted run (skip already-resolved churches)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process first N churches per state (for testing)",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help=(
            "Re-attempt resolution for churches that previously failed. "
            "Implies --resume but only skips churches with a REAL resolved URL "
            "(retries ones where website_resolved is None)."
        ),
    )
    args = parser.parse_args()
    # --retry-failed implies --resume (we still want to skip successes)
    resume = args.resume or args.retry_failed
    run_resolve(
        args.states,
        resume=resume,
        limit=args.limit,
        retry_failed=args.retry_failed,
    )
