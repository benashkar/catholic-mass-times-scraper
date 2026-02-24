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
    python run_resolve_urls.py all --retry-failed       # Retry churches that failed (one pass)
    python run_resolve_urls.py all --retry-loop         # Retry until 100% (up to 5 passes)
    python run_resolve_urls.py texas --retry-loop --max-retry-passes 10  # More attempts

OUTPUT:
    Updates data/output/{state}/church_details.jsonl in-place (adds website_resolved field)
    Creates data/output/{state}/resolve_progress.json for resume support
    Creates data/output/{state}/resolve_failures.json with failure details (reason, attempts)

EXPECTED RUNTIME:
    ~0.5s per church. Ohio (1,239 churches) ≈ 10 min. All 50 states ≈ 4-5 hours.
"""

import argparse
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import OUTPUT_DIR
from src.utils.file_io import load_from_json, save_to_json
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
    "alabama": ("AL", "alabama"),
    "al": ("AL", "alabama"),
    "alaska": ("AK", "alaska"),
    "ak": ("AK", "alaska"),
    "arizona": ("AZ", "arizona"),
    "az": ("AZ", "arizona"),
    "arkansas": ("AR", "arkansas"),
    "ar": ("AR", "arkansas"),
    "california": ("CA", "california"),
    "ca": ("CA", "california"),
    "colorado": ("CO", "colorado"),
    "co": ("CO", "colorado"),
    "connecticut": ("CT", "connecticut"),
    "ct": ("CT", "connecticut"),
    "delaware": ("DE", "delaware"),
    "de": ("DE", "delaware"),
    "florida": ("FL", "florida"),
    "fl": ("FL", "florida"),
    "georgia": ("GA", "georgia"),
    "ga": ("GA", "georgia"),
    "hawaii": ("HI", "hawaii"),
    "hi": ("HI", "hawaii"),
    "idaho": ("ID", "idaho"),
    "id": ("ID", "idaho"),
    "illinois": ("IL", "illinois"),
    "il": ("IL", "illinois"),
    "indiana": ("IN", "indiana"),
    "in": ("IN", "indiana"),
    "iowa": ("IA", "iowa"),
    "ia": ("IA", "iowa"),
    "kansas": ("KS", "kansas"),
    "ks": ("KS", "kansas"),
    "kentucky": ("KY", "kentucky"),
    "ky": ("KY", "kentucky"),
    "louisiana": ("LA", "louisiana"),
    "la": ("LA", "louisiana"),
    "maine": ("ME", "maine"),
    "me": ("ME", "maine"),
    "maryland": ("MD", "maryland"),
    "md": ("MD", "maryland"),
    "massachusetts": ("MA", "massachusetts"),
    "ma": ("MA", "massachusetts"),
    "michigan": ("MI", "michigan"),
    "mi": ("MI", "michigan"),
    "minnesota": ("MN", "minnesota"),
    "mn": ("MN", "minnesota"),
    "mississippi": ("MS", "mississippi"),
    "ms": ("MS", "mississippi"),
    "missouri": ("MO", "missouri"),
    "mo": ("MO", "missouri"),
    "montana": ("MT", "montana"),
    "mt": ("MT", "montana"),
    "nebraska": ("NE", "nebraska"),
    "ne": ("NE", "nebraska"),
    "nevada": ("NV", "nevada"),
    "nv": ("NV", "nevada"),
    "new_hampshire": ("NH", "new_hampshire"),
    "nh": ("NH", "new_hampshire"),
    "new_jersey": ("NJ", "new_jersey"),
    "nj": ("NJ", "new_jersey"),
    "new_mexico": ("NM", "new_mexico"),
    "nm": ("NM", "new_mexico"),
    "new_york": ("NY", "new_york"),
    "ny": ("NY", "new_york"),
    "north_carolina": ("NC", "north_carolina"),
    "nc": ("NC", "north_carolina"),
    "north_dakota": ("ND", "north_dakota"),
    "nd": ("ND", "north_dakota"),
    "ohio": ("OH", "ohio"),
    "oh": ("OH", "ohio"),
    "oklahoma": ("OK", "oklahoma"),
    "ok": ("OK", "oklahoma"),
    "oregon": ("OR", "oregon"),
    "or": ("OR", "oregon"),
    "pennsylvania": ("PA", "pennsylvania"),
    "pa": ("PA", "pennsylvania"),
    "rhode_island": ("RI", "rhode_island"),
    "ri": ("RI", "rhode_island"),
    "south_carolina": ("SC", "south_carolina"),
    "sc": ("SC", "south_carolina"),
    "south_dakota": ("SD", "south_dakota"),
    "sd": ("SD", "south_dakota"),
    "tennessee": ("TN", "tennessee"),
    "tn": ("TN", "tennessee"),
    "texas": ("TX", "texas"),
    "tx": ("TX", "texas"),
    "utah": ("UT", "utah"),
    "ut": ("UT", "utah"),
    "vermont": ("VT", "vermont"),
    "vt": ("VT", "vermont"),
    "virginia": ("VA", "virginia"),
    "va": ("VA", "virginia"),
    "washington": ("WA", "washington"),
    "wa": ("WA", "washington"),
    "west_virginia": ("WV", "west_virginia"),
    "wv": ("WV", "west_virginia"),
    "wisconsin": ("WI", "wisconsin"),
    "wi": ("WI", "wisconsin"),
    "wyoming": ("WY", "wyoming"),
    "wy": ("WY", "wyoming"),
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


def resolve_redirect_url(redirect_path: str, slug: str = "") -> tuple[str | None, str]:
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
        Tuple of (actual_url, fail_reason). actual_url is the resolved URL or None.
        fail_reason is empty string on success, or describes why it failed.
    """
    if not redirect_path or redirect_path == "#" or "/api/out" not in redirect_path:
        return None, "no_redirect_url"

    # Step 1: Try the stored redirect URL directly
    full_url = f"https://catholicindex.org{redirect_path}"
    resp = _rate_limited_get(full_url)

    if resp and resp.status_code == 200:
        # Parse window.location.href = "..." from the interstitial page
        match = re.search(r'window\.location\.href\s*=\s*"([^"]+)"', resp.text)
        if match:
            return match.group(1), ""

        # Fallback: extract URL from <div class="url">https://...</div>
        # (newer CatholicIndex interstitial page format as of Feb 2026)
        url_div = re.search(r'class="url"[^>]*>(https?://[^<]+)<', resp.text)
        if url_div:
            return url_div.group(1).strip(), ""

        # Fallback: look for any non-CatholicIndex URL in the page
        urls = re.findall(r'href="(https?://[^"]+)"', resp.text)
        for url in urls:
            if "catholicindex.org" not in url and "cloudflare" not in url:
                return url, ""

        step1_reason = "no_url_in_page"
    elif resp:
        step1_reason = f"http_{resp.status_code}"
    else:
        step1_reason = "request_failed"

    # Step 2: If stored URL failed (expired sig, 429, etc.), fetch fresh URL from
    # the church detail page
    if slug:
        church_url = f"https://catholicindex.org/churches/{slug}"
        resp2 = _rate_limited_get(church_url)

        if resp2 and resp2.status_code == 200:
            # Extract the fresh signed website URL from the page
            pattern = (
                r"/api/out\?id=" + re.escape(slug) + r"&type=website&t=\d+&sig=[A-Za-z0-9_\-=]+"
            )
            fresh_match = re.search(pattern, resp2.text)
            if fresh_match:
                fresh_url = f"https://catholicindex.org{fresh_match.group(0)}"
                resp3 = _rate_limited_get(fresh_url)

                if resp3 and resp3.status_code == 200:
                    match = re.search(r'window\.location\.href\s*=\s*"([^"]+)"', resp3.text)
                    if match:
                        return match.group(1), ""

                    # Fallback: <div class="url">https://...</div>
                    url_div = re.search(r'class="url"[^>]*>(https?://[^<]+)<', resp3.text)
                    if url_div:
                        return url_div.group(1).strip(), ""

                    return None, f"step1:{step1_reason}|step2:no_url_in_fresh_page"
                elif resp3:
                    return None, f"step1:{step1_reason}|step2:http_{resp3.status_code}"
                else:
                    return None, f"step1:{step1_reason}|step2:fresh_request_failed"
            else:
                return None, f"step1:{step1_reason}|step2:no_website_link_on_church_page"
        elif resp2:
            return None, f"step1:{step1_reason}|step2:church_page_http_{resp2.status_code}"
        else:
            return None, f"step1:{step1_reason}|step2:church_page_request_failed"

    return None, f"step1:{step1_reason}|no_slug_for_step2"


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
    with open(jsonl_path, encoding="utf-8") as f:
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
    failed_churches = []

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
        actual_url, fail_reason = resolve_redirect_url(website, slug=slug)
        church["website_resolved"] = actual_url
        if fail_reason:
            church["resolve_fail_reason"] = fail_reason
        elif "resolve_fail_reason" in church:
            # Clear old fail reason on success
            del church["resolve_fail_reason"]
        completed_slugs.add(slug)
        processed_this_run += 1

        if actual_url:
            resolved_count += 1
        else:
            failed_count += 1
            failed_churches.append(
                {
                    "slug": slug,
                    "name": name,
                    "city": church.get("city", ""),
                    "reason": fail_reason,
                }
            )

        # Progress display
        done = len(completed_slugs)
        elapsed = time.time() - start_time
        if processed_this_run > 0:
            rate = elapsed / processed_this_run
            remaining = total - done
            eta_min = (remaining * rate) / 60
            logger.info(
                f"[{done}/{total}] {name}: " f"{actual_url or 'FAILED'} | ETA: {eta_min:.1f}m"
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

    # Save failure log for easy tracking and retry
    failures_path = OUTPUT_DIR / dir_name / "resolve_failures.json"
    if failed_churches:
        # Merge with any existing failures from previous runs
        existing_failures = {}
        if failures_path.exists():
            existing = load_from_json(failures_path)
            if existing:
                for f in existing.get("failures", []):
                    existing_failures[f["slug"]] = f

        # Update with this run's failures (increment attempt count)
        for f in failed_churches:
            prev = existing_failures.get(f["slug"], {})
            f["attempts"] = prev.get("attempts", 0) + 1
            f["last_reason"] = f.pop("reason")
            f["last_attempt"] = datetime.now(UTC).isoformat()
            existing_failures[f["slug"]] = f

        # Remove any that were resolved this run (no longer failed)
        resolved_slugs = {
            r.get("church", {}).get("slug")
            for r in all_records
            if r.get("church", {}).get("website_resolved")
        }
        remaining_failures = [
            f for slug, f in existing_failures.items() if slug not in resolved_slugs
        ]

        save_to_json(
            {
                "state": state_code,
                "last_run": datetime.now(UTC).isoformat(),
                "total_failures": len(remaining_failures),
                "failures": remaining_failures,
            },
            failures_path,
        )
        logger.info(f"Failure log saved: {failures_path} ({len(remaining_failures)} entries)")
    elif failures_path.exists():
        # All resolved! Clear the failure log
        save_to_json(
            {
                "state": state_code,
                "last_run": datetime.now(UTC).isoformat(),
                "total_failures": 0,
                "failures": [],
            },
            failures_path,
        )
        logger.info("All URLs resolved! Failure log cleared.")

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

    return failed_count


def run_resolve(
    states: list[str],
    resume: bool = False,
    limit: int | None = None,
    retry_failed: bool = False,
    retry_loop: bool = False,
    max_retry_passes: int = 5,
):
    """Resolve URLs for one or more states.

    If retry_loop=True, after the initial pass for each state, keeps retrying
    failed churches (with increasing delay) until all resolve or max_retry_passes
    is reached. This handles Cloudflare rate limiting by spacing out retries.
    """
    state_list = []
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
                matched = STATE_ALIASES.get(dir_name)
                code = matched[0] if matched else dir_name.upper()
                state_list.append((code, dir_name))
        else:
            resolved = resolve_state(state_input)
            if not resolved:
                logger.error(f"Unknown state: '{state_input}'")
                continue
            state_list.append(resolved)

    for state_code, dir_name in state_list:
        failed = resolve_urls_for_state(
            state_code,
            dir_name,
            resume=resume,
            limit=limit,
            retry_failed=retry_failed,
        )

        # Retry loop: keep retrying failed churches until all resolve
        if retry_loop and failed and failed > 0:
            for attempt in range(2, max_retry_passes + 1):
                wait_min = attempt * 2  # 4, 6, 8, 10 minutes between passes
                logger.info(
                    f"\n[RETRY LOOP] {state_code}: {failed} failures remain. "
                    f"Pass {attempt}/{max_retry_passes}. "
                    f"Waiting {wait_min} minutes before retry..."
                )
                time.sleep(wait_min * 60)

                failed = resolve_urls_for_state(
                    state_code,
                    dir_name,
                    resume=True,
                    limit=limit,
                    retry_failed=True,
                )

                if not failed or failed == 0:
                    logger.info(f"[RETRY LOOP] {state_code}: All URLs resolved!")
                    break
            else:
                logger.warning(
                    f"[RETRY LOOP] {state_code}: {failed} failures remain "
                    f"after {max_retry_passes} passes. Check resolve_failures.json."
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
    parser.add_argument(
        "--retry-loop",
        action="store_true",
        help=(
            "Keep retrying failed churches until all resolve or max attempts "
            "reached (default 5 passes). Waits longer between each pass to "
            "avoid Cloudflare rate limits. Implies --retry-failed."
        ),
    )
    parser.add_argument(
        "--max-retry-passes",
        type=int,
        default=5,
        help="Max retry passes for --retry-loop (default: 5)",
    )
    args = parser.parse_args()
    # --retry-loop implies --retry-failed which implies --resume
    retry_failed = args.retry_failed or args.retry_loop
    resume = args.resume or retry_failed
    run_resolve(
        args.states,
        resume=resume,
        limit=args.limit,
        retry_failed=retry_failed,
        retry_loop=args.retry_loop,
        max_retry_passes=args.max_retry_passes,
    )
