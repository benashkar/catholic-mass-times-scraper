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

# Rate limit: 0.3s between requests (lightweight pages, not full scrapes)
RESOLVE_DELAY = 0.3
PROGRESS_SAVE_INTERVAL = 50
REQUEST_TIMEOUT = 10
USER_AGENT = "CatholicMassTimesScraper/1.0 (CR Community News; URL resolver)"

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


def resolve_redirect_url(redirect_path: str) -> str | None:
    """
    Fetch the CatholicIndex /api/out interstitial page and extract the real URL.

    The interstitial page contains: window.location.href = "https://actualsite.org";
    We parse that to get the real church website URL.

    Args:
        redirect_path: The /api/out?... path from CatholicIndex

    Returns:
        The actual church website URL, or None on failure.
    """
    global _last_request_time

    if not redirect_path or redirect_path == "#" or "/api/out" not in redirect_path:
        return None

    # Rate limiting
    elapsed = time.time() - _last_request_time
    if elapsed < RESOLVE_DELAY:
        time.sleep(RESOLVE_DELAY - elapsed)

    full_url = f"https://catholicindex.org{redirect_path}"
    _last_request_time = time.time()

    try:
        resp = requests.get(
            full_url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        if resp.status_code != 200:
            return None

        # Parse window.location.href = "..." from the interstitial page
        match = re.search(r'window\.location\.href\s*=\s*"([^"]+)"', resp.text)
        if match:
            return match.group(1)

        # Fallback: look for any non-CatholicIndex URL in the page
        urls = re.findall(r'href="(https?://[^"]+)"', resp.text)
        for url in urls:
            if "catholicindex.org" not in url and "cloudflare" not in url:
                return url

        return None

    except requests.RequestException as e:
        logger.debug(f"Request failed for {redirect_path}: {e}")
        return None


def resolve_urls_for_state(
    state_code: str, dir_name: str, resume: bool = False, limit: int | None = None
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
    completed_slugs = set()
    if resume and progress_path.exists():
        prog = load_from_json(progress_path)
        if prog:
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

        # Resolve the redirect URL
        actual_url = resolve_redirect_url(website)
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


def run_resolve(states: list[str], resume: bool = False, limit: int | None = None):
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
                resolve_urls_for_state(code, dir_name, resume=resume, limit=limit)
            return

        resolved = resolve_state(state_input)
        if not resolved:
            logger.error(f"Unknown state: '{state_input}'")
            continue
        state_code, dir_name = resolved
        resolve_urls_for_state(state_code, dir_name, resume=resume, limit=limit)


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
    args = parser.parse_args()
    run_resolve(args.states, resume=args.resume, limit=args.limit)
