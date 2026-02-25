"""
Re-scrape churches that hit the 100-PDF cap during initial bulletin scraping.

Discovers the true PDF count for each capped church, downloads additional older
PDFs we missed, extracts names, and merges results into the existing
bulletin_names.csv.

Usage:
    python run_rescrape_capped.py alabama                    # one state
    python run_rescrape_capped.py all                        # all 50 states
    python run_rescrape_capped.py alabama --max-total 200    # default: 200 total per church
    python run_rescrape_capped.py alabama --max-total 0      # unlimited
    python run_rescrape_capped.py alabama --resume           # skip already-rescrapped churches
    python run_rescrape_capped.py alabama --limit 3          # test with first 3 capped churches
    python run_rescrape_capped.py alabama --dry-run          # preview without downloading
    python run_rescrape_capped.py alabama --discover-only    # just learn true totals
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

import run_bulletin_scraper as rbs  # noqa: E402
from config.settings import OUTPUT_DIR  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger(__name__)

RESCRAPE_PROGRESS_FILE = "rescrape_progress.json"

CSV_FIELDNAMES = [
    "church_name",
    "church_slug",
    "city",
    "church_url",
    "pdf_file",
    "pdf_url",
    "pdf_date",
    "person_name",
    "title",
    "first_name",
    "middle_name",
    "last_name",
    "role",
    "category",
    "confidence",
    "confidence_score",
    "context",
]


def load_rescrape_progress(state_dir):
    """Load rescrape-specific progress (separate from main progress)."""
    path = state_dir / RESCRAPE_PROGRESS_FILE
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"discovered": {}, "downloaded": {}}


def save_rescrape_progress(state_dir, progress):
    """Save rescrape-specific progress."""
    path = state_dir / RESCRAPE_PROGRESS_FILE
    with open(path, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2)


def load_existing_urls(main_progress, slug):
    """Get set of PDF URLs already downloaded for this church."""
    downloaded = main_progress.get("downloaded", {}).get(slug, {})
    return {f["url"] for f in downloaded.get("files", [])}


def load_existing_names(csv_path, slug):
    """Get set of (lowercased) person names already extracted for this church."""
    names = set()
    if csv_path.exists():
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if (row.get("church_slug") or "") == slug:
                    name = (row.get("person_name") or "").lower().strip()
                    if name:
                        names.add(name)
    return names


def build_slug_to_city(state_dir):
    """Build slug -> city mapping from church_details.jsonl."""
    slug_to_city = {}
    jsonl_path = state_dir / "church_details.jsonl"
    if jsonl_path.exists():
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    church = rec.get("church", {})
                    s = church.get("slug", "")
                    c = church.get("city", "")
                    if s and c:
                        slug_to_city[s] = c
                except (json.JSONDecodeError, KeyError):
                    pass
    return slug_to_city


def rediscover_church(slug, bulletin_page_url, disc_info):
    """Re-discover all PDFs for a church with the cap removed.

    Returns full list of PDF URLs (newest-first, NO cap).
    """
    rbs._pdf_cap_override = 0
    try:
        # Try the bulletin page directly
        soup = rbs.safe_get_html(bulletin_page_url)
        if soup:
            pdfs = rbs.extract_pdf_links(soup, bulletin_page_url)
            if pdfs:
                return pdfs
            # Try WordPress subpages for archived bulletins
            wp_pdfs = rbs.extract_pdfs_from_subpages(soup, bulletin_page_url, max_subpages=50)
            if wp_pdfs:
                return wp_pdfs

        # Fallback: try LPi widget if we have a parish ID
        lpi_parish_id = disc_info.get("lpi_parish_id")
        if lpi_parish_id:
            widget_pdfs = rbs.extract_lpi_pdfs_from_widget(lpi_parish_id)
            if widget_pdfs:
                return widget_pdfs

        return []
    finally:
        rbs._pdf_cap_override = None


def extract_names_from_new_pdfs(
    new_files, church_name, church_url, slug, slug_to_city, text_dir, existing_names
):
    """Extract names from newly downloaded PDFs, deduping against existing names.

    Returns list of CSV-row dicts for new unique names.
    """
    new_names = []

    for file_info in new_files:
        pdf_path = Path(file_info["local_path"])
        if not pdf_path.exists():
            continue

        pdf_url = file_info.get("url", "")

        # Extract date from URL/filename
        pdf_date = ""
        date_score = rbs.extract_date_score(pdf_url, pdf_path.name)
        if date_score > 0:
            ds = str(date_score)
            if len(ds) == 8:
                pdf_date = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"

        text = rbs.extract_text_from_pdf(pdf_path)
        if not text:
            continue

        # Save extracted text
        text_path = text_dir / (pdf_path.stem + ".txt")
        text_path.write_text(text, encoding="utf-8")

        names = rbs.extract_names_from_text(text, church_name)
        if not names:
            continue

        for name_info in names:
            person_name = name_info["name"]
            name_key = person_name.lower().strip()

            # Dedup against existing + already seen in this batch
            if name_key in existing_names:
                continue
            existing_names.add(name_key)

            cat = name_info["category"]
            n_title = name_info.get("title", "")
            n_role = name_info.get("role", "")
            conf_score = rbs.score_name_confidence(
                person_name, category=cat, role=n_role, title=n_title
            )

            new_names.append(
                {
                    "church_name": church_name,
                    "church_slug": slug,
                    "city": slug_to_city.get(slug, ""),
                    "church_url": church_url,
                    "pdf_file": pdf_path.name,
                    "pdf_url": pdf_url,
                    "pdf_date": pdf_date,
                    "person_name": person_name,
                    "title": n_title,
                    "first_name": name_info.get("first_name", ""),
                    "middle_name": name_info.get("middle_name", ""),
                    "last_name": name_info.get("last_name", ""),
                    "role": n_role,
                    "category": cat,
                    "confidence": rbs.confidence_label(conf_score),
                    "confidence_score": f"{conf_score:.2f}",
                    "context": name_info["context"][:100],
                }
            )

    return new_names


def process_state(state_name, state_dir, max_total, resume, limit, dry_run, discover_only):
    """Process all capped churches for one state."""
    capped_path = state_dir / "capped_churches.json"
    if not capped_path.exists():
        logger.info(f"[{state_name}] No capped_churches.json, skipping")
        return {"state": state_name, "churches": 0, "new_names": 0, "new_pdfs": 0}

    with open(capped_path, encoding="utf-8") as f:
        capped_churches = json.load(f)

    if not capped_churches:
        logger.info(f"[{state_name}] No capped churches, skipping")
        return {"state": state_name, "churches": 0, "new_names": 0, "new_pdfs": 0}

    if limit:
        capped_churches = capped_churches[:limit]

    logger.info(f"\n{'=' * 60}")
    logger.info(f"RESCRAPE {state_name.upper()}: {len(capped_churches)} capped churches")
    logger.info(f"Max total PDFs per church: {max_total if max_total else 'unlimited'}")
    logger.info(f"{'=' * 60}")

    # Load main progress to get bulletin page URLs and existing downloads
    main_progress = rbs.load_progress(state_dir)
    discovered_main = main_progress.get("discovered", {})

    # Load rescrape-specific progress
    rs_progress = load_rescrape_progress(state_dir)

    slug_to_city = build_slug_to_city(state_dir)

    new_names_all = []
    updated_capped = []
    bulletin_dir = state_dir / "bulletins"
    text_dir = state_dir / "bulletin_texts"
    csv_path = state_dir / "bulletin_names.csv"
    total_new_pdfs = 0

    for i, capped in enumerate(capped_churches):
        slug = capped["slug"]
        church_name = capped["church_name"]

        # Resume: skip if already rescrapped
        if resume and slug in rs_progress.get("downloaded", {}):
            logger.info(
                f"[{i + 1}/{len(capped_churches)}] {church_name}: already rescrapped, skipping"
            )
            continue

        logger.info(f"\n[{i + 1}/{len(capped_churches)}] {church_name} ({slug})")

        # Get bulletin page URL from original discovery
        disc_info = discovered_main.get(slug, {})
        bulletin_page = disc_info.get("bulletin_page")
        church_url = disc_info.get("church_url", "")

        if not bulletin_page:
            logger.warning("  No bulletin page URL in discovery data, skipping")
            continue

        # Phase 1: Re-discover without cap
        all_pdf_urls = rediscover_church(slug, bulletin_page, disc_info)
        true_total = len(all_pdf_urls)
        logger.info(f"  True total PDFs: {true_total}")

        # Track updated totals for capped_churches.json
        updated_capped.append(
            {
                "slug": slug,
                "church_name": church_name,
                "total_pdfs_available": true_total,
                "pdfs_downloaded": capped["pdfs_downloaded"],
                "remaining_pdfs": max(0, true_total - capped["pdfs_downloaded"]),
            }
        )

        rs_progress.setdefault("discovered", {})[slug] = {
            "true_total": true_total,
            "bulletin_page": bulletin_page,
        }

        if discover_only or dry_run:
            if true_total > rbs.MAX_PDFS_PER_CHURCH:
                extra = true_total - rbs.MAX_PDFS_PER_CHURCH
                would_grab = min(
                    extra, (max_total - rbs.MAX_PDFS_PER_CHURCH) if max_total else extra
                )
                logger.info(f"  Would download {would_grab} additional PDFs")
            else:
                logger.info(f"  Only {true_total} total, nothing new to grab")
            continue

        if true_total <= rbs.MAX_PDFS_PER_CHURCH:
            logger.info(f"  Only {true_total} PDFs total (at or below original cap), skipping")
            rs_progress.setdefault("downloaded", {})[slug] = {
                "new_files": [],
                "skipped": True,
            }
            continue

        # Phase 2: Determine which PDFs to download
        existing_urls = load_existing_urls(main_progress, slug)
        new_urls = [url for url in all_pdf_urls if url not in existing_urls]

        # Apply max_total cap
        if max_total and max_total > 0:
            slots_remaining = max(0, max_total - len(existing_urls))
            new_urls = new_urls[:slots_remaining]

        if not new_urls:
            logger.info(f"  No new PDFs to download (all {true_total} already in hand or capped)")
            rs_progress.setdefault("downloaded", {})[slug] = {
                "new_files": [],
                "skipped": True,
            }
            continue

        logger.info(
            f"  Downloading {len(new_urls)} new PDFs "
            f"(had {len(existing_urls)}, true total {true_total})"
        )

        # Download new PDFs
        bulletin_dir.mkdir(parents=True, exist_ok=True)
        slug_safe = re.sub(r"[^a-zA-Z0-9_-]", "_", slug)
        new_files = []

        for pdf_url in new_urls:
            path = rbs.download_bulletin_pdf(pdf_url, bulletin_dir, slug_safe)
            if path:
                new_files.append(
                    {
                        "url": pdf_url,
                        "local_path": str(path),
                        "size_bytes": path.stat().st_size,
                    }
                )

        logger.info(f"  Downloaded {len(new_files)} new PDFs")
        total_new_pdfs += len(new_files)

        rs_progress.setdefault("downloaded", {})[slug] = {
            "church_name": church_name,
            "new_files": [{"url": f["url"], "local_path": f["local_path"]} for f in new_files],
            "true_total": true_total,
            "previously_had": len(existing_urls),
        }

        # Phase 3: Extract names from new PDFs
        existing_names = load_existing_names(csv_path, slug)
        text_dir.mkdir(parents=True, exist_ok=True)

        church_new_names = extract_names_from_new_pdfs(
            new_files, church_name, church_url, slug, slug_to_city, text_dir, existing_names
        )

        if church_new_names:
            logger.info(f"  Extracted {len(church_new_names)} new unique names")
            new_names_all.extend(church_new_names)

        # Save rescrape progress periodically
        if (i + 1) % 5 == 0:
            save_rescrape_progress(state_dir, rs_progress)

    # Save final rescrape progress
    save_rescrape_progress(state_dir, rs_progress)

    # Update capped_churches.json with true totals
    if updated_capped:
        with open(capped_path, "w", encoding="utf-8") as f:
            json.dump(updated_capped, f, indent=2)
        logger.info("\nUpdated capped_churches.json with true PDF totals")

    # Merge new names into existing CSV (append)
    if new_names_all:
        file_exists = csv_path.exists()
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerows(new_names_all)
        logger.info(f"\n[OK] Appended {len(new_names_all)} new names to {csv_path.name}")

    # Update main progress.json so weekly batch knows about new files
    if not dry_run and not discover_only:
        main_progress = rbs.load_progress(state_dir)
        for slug, rs_dl in rs_progress.get("downloaded", {}).items():
            rs_files = rs_dl.get("new_files", [])
            if not rs_files:
                continue
            if slug in main_progress.get("downloaded", {}):
                existing_files = main_progress["downloaded"][slug].get("files", [])
                existing_file_urls = {f["url"] for f in existing_files}
                for nf in rs_files:
                    if nf["url"] not in existing_file_urls:
                        existing_files.append(nf)
                main_progress["downloaded"][slug]["files"] = existing_files
                main_progress["downloaded"][slug]["capped"] = False
                main_progress["downloaded"][slug]["total_available"] = rs_dl.get("true_total", 0)
        rbs.save_progress(state_dir, main_progress)

    result = {
        "state": state_name,
        "churches": len(capped_churches),
        "new_pdfs": total_new_pdfs,
        "new_names": len(new_names_all),
    }

    logger.info(f"\n=== {state_name} RESCRAPE COMPLETE ===")
    logger.info(f"  Churches processed: {result['churches']}")
    logger.info(f"  New PDFs downloaded: {result['new_pdfs']}")
    logger.info(f"  New names extracted: {result['new_names']}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Re-scrape churches that hit the 100-PDF cap")
    parser.add_argument("states", nargs="+", help="State names or 'all'")
    parser.add_argument(
        "--max-total",
        type=int,
        default=200,
        help="Max total PDFs per church including already-downloaded. 0=unlimited. Default: 200",
    )
    parser.add_argument("--resume", action="store_true", help="Skip already-rescrapped churches")
    parser.add_argument(
        "--limit", type=int, default=0, help="Limit capped churches per state (for testing)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Show plan without downloading")
    parser.add_argument(
        "--discover-only",
        action="store_true",
        help="Only discover true PDF totals, no downloads",
    )
    args = parser.parse_args()

    # Resolve states
    if "all" in [s.lower() for s in args.states]:
        state_dirs = sorted(
            [
                d
                for d in OUTPUT_DIR.iterdir()
                if d.is_dir() and (d / "capped_churches.json").exists()
            ]
        )
    else:
        state_dirs = []
        for name in args.states:
            resolved = rbs.resolve_state(name)
            if not resolved:
                logger.error(f"[ERR] Unknown state: {name}")
                continue
            state_dir = OUTPUT_DIR / resolved[1]
            if state_dir.exists():
                state_dirs.append(state_dir)
            else:
                logger.error(f"[ERR] No output directory for {name}")

    if not state_dirs:
        logger.error("[ERR] No valid states to process")
        sys.exit(1)

    if args.dry_run:
        logger.info("=== DRY RUN -- no files will be modified ===\n")
    if args.discover_only:
        logger.info("=== DISCOVER ONLY -- just finding true PDF totals ===\n")

    logger.info(f"Max total PDFs per church: {args.max_total if args.max_total else 'unlimited'}")
    logger.info(f"States to process: {len(state_dirs)}\n")

    results = []
    for state_dir in state_dirs:
        result = process_state(
            state_dir.name,
            state_dir,
            max_total=args.max_total,
            resume=args.resume,
            limit=args.limit,
            dry_run=args.dry_run,
            discover_only=args.discover_only,
        )
        results.append(result)

    # Print summary
    logger.info(f"\n{'=' * 60}")
    logger.info("RESCRAPE SUMMARY")
    logger.info(f"{'=' * 60}")
    logger.info(f"{'State':<20} {'Churches':>10} {'New PDFs':>10} {'New Names':>10}")
    logger.info("-" * 50)

    grand_churches = 0
    grand_pdfs = 0
    grand_names = 0
    for r in results:
        logger.info(
            f"{r['state']:<20} {r['churches']:>10,} {r['new_pdfs']:>10,} {r['new_names']:>10,}"
        )
        grand_churches += r["churches"]
        grand_pdfs += r["new_pdfs"]
        grand_names += r["new_names"]

    logger.info("-" * 50)
    logger.info(f"{'TOTAL':<20} {grand_churches:>10,} {grand_pdfs:>10,} {grand_names:>10,}")

    rbs._close_playwright_browser()
    logger.info("\n[OK] All done.")


if __name__ == "__main__":
    main()
