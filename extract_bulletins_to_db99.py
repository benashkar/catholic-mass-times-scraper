"""
extract_bulletins_to_db99.py — Extract names from church bulletins directly to db99.

Stateless script designed for Render cron jobs (no local files needed).
Reads churches from db99, discovers bulletin PDFs, downloads to memory,
extracts text, identifies names, runs NER veto gate, and UPSERTs directly
back to db99.

Progress tracked via db99 tables (not local files):
  - bulletin_source: discovery progress (church has been checked)
  - bulletin_pdf.text_extracted: extraction progress (PDF has been processed)
  - Resume = SELECT * FROM bulletin_pdf WHERE text_extracted = 0

Usage:
    python extract_bulletins_to_db99.py                    # All states
    python extract_bulletins_to_db99.py --state OH         # One state
    python extract_bulletins_to_db99.py --limit 5          # First 5 churches
    python extract_bulletins_to_db99.py --days-fresh 7     # Skip churches checked within 7 days
    python extract_bulletins_to_db99.py --skip-discovery   # Only extract from already-discovered PDFs
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pymysql
import requests

# Import reusable functions from the bulletin scraper
from run_bulletin_scraper import (
    confidence_label,
    detect_couple,
    extract_names_from_text,
    extract_text_from_pdf,
    find_bulletin_page,
    ner_veto_batch,
    parse_name_parts,
    score_name_confidence,
)
from src.parsers.fallback_parsers import (
    parse_category_from_context,
    parse_first_last_from_person_name,
    parse_role_from_context,
    parse_title_from_person_name,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Optional rotating residential proxy (shared PROXY_URL convention). When set,
# PDF downloads route through it; when unset, requests go direct.
PROXY_URL = os.environ.get("PROXY_URL", "").strip() or None
PROXIES = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

MAX_PDF_SIZE_MB = 25
MAX_PDFS_PER_CHURCH = 100


# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------


def get_connection():
    """Connect to db99."""
    try:
        import boto3

        client = boto3.client("secretsmanager", region_name="us-east-1")
        resp = client.get_secret_value(SecretId="/ben/ai-tool/db99")
        secret = json.loads(resp["SecretString"])
        host = os.getenv("DB_HOST") or secret.get("DB_HOST") or "10.10.0.8"
        user = secret["DB_USER"]
        password = secret["DB_PASSWORD"]
    except Exception:
        host = os.getenv("DB_HOST", "10.10.0.8")
        user = os.getenv("DB_USER", "")
        password = os.getenv("DB_PASSWORD", "")

    return pymysql.connect(
        host=host,
        port=3306,
        user=user,
        password=password,
        database="church_scrapes",
        connect_timeout=30,
        read_timeout=300,
        write_timeout=300,
        autocommit=True,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


# ---------------------------------------------------------------------------
# Schema migration (idempotent)
# ---------------------------------------------------------------------------


def ensure_schema(cur):
    """Add columns that our code needs but may not exist on db99 yet."""
    cur.execute("""
        SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'church_scrapes'
          AND TABLE_NAME = 'bulletin_name'
          AND COLUMN_NAME = 'role'
    """)
    if not cur.fetchone():
        cur.execute("ALTER TABLE bulletin_name ADD COLUMN role VARCHAR(100) DEFAULT ''")
        print("  [OK] Added column bulletin_name.role")


# ---------------------------------------------------------------------------
# Bulletin extraction pipeline
# ---------------------------------------------------------------------------


def download_pdf_to_memory(url, timeout=30):
    """Download a PDF into memory (no disk). Returns bytes or None."""
    try:
        resp = requests.get(
            url, timeout=timeout, headers={"User-Agent": USER_AGENT}, proxies=PROXIES
        )
        if resp.status_code != 200:
            return None
        if len(resp.content) > MAX_PDF_SIZE_MB * 1024 * 1024:
            return None
        # Basic PDF check
        if not resp.content[:5].startswith(b"%PDF"):
            return None
        return resp.content
    except Exception:
        return None


def url_hash(url):
    """Stable hash for deduplicating PDF URLs."""
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def process_church(cur, church):
    """Discover, download, extract, and insert names for one church.

    Returns dict of stats: pdfs_found, pdfs_extracted, names_inserted.
    """
    church_id = church["church_id"]
    website_url = church["website_url"]
    church_name = church["name"] or ""
    stats = {"pdfs_found": 0, "pdfs_extracted": 0, "names_inserted": 0}

    # Phase 1: Discover bulletin page
    result = find_bulletin_page(website_url)
    pdf_urls = result.get("pdf_urls") or []
    bulletin_page_url = result.get("bulletin_page_url") or website_url
    source_type = result.get("source") or "not_found"

    # UPSERT bulletin_source
    cur.execute(
        """
        INSERT INTO bulletin_source (church_id, discovery_source, bulletin_page_url, discovered_at)
        VALUES (%s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
            discovery_source = VALUES(discovery_source),
            bulletin_page_url = VALUES(bulletin_page_url),
            discovered_at = NOW()
    """,
        (church_id, source_type[:30], bulletin_page_url[:2048]),
    )

    if not pdf_urls:
        return stats

    # Get bulletin_source_id
    cur.execute(
        "SELECT bulletin_source_id FROM bulletin_source WHERE church_id = %s",
        (church_id,),
    )
    source_row = cur.fetchone()
    if not source_row:
        return stats
    bulletin_source_id = source_row["bulletin_source_id"]

    # Phase 2: Process each PDF
    for pdf_url in pdf_urls[:MAX_PDFS_PER_CHURCH]:
        # Check if already extracted
        cur.execute(
            "SELECT bulletin_pdf_id, text_extracted FROM bulletin_pdf "
            "WHERE bulletin_source_id = %s AND pdf_url = %s LIMIT 1",
            (bulletin_source_id, pdf_url[:2048]),
        )
        existing_pdf = cur.fetchone()
        if existing_pdf and existing_pdf.get("text_extracted"):
            continue

        stats["pdfs_found"] += 1

        # Download PDF to memory
        pdf_bytes = download_pdf_to_memory(pdf_url)
        if not pdf_bytes:
            continue

        # UPSERT bulletin_pdf
        if existing_pdf:
            bulletin_pdf_id = existing_pdf["bulletin_pdf_id"]
        else:
            cur.execute(
                """
                INSERT INTO bulletin_pdf (bulletin_source_id, pdf_url, downloaded_at)
                VALUES (%s, %s, NOW())
            """,
                (bulletin_source_id, pdf_url[:2048]),
            )
            bulletin_pdf_id = cur.lastrowid

        if not bulletin_pdf_id:
            continue

        # Phase 3: Extract text (from bytes, not file)
        full_text, column_texts = extract_text_from_pdf(pdf_bytes)
        if not full_text:
            cur.execute(
                "UPDATE bulletin_pdf SET text_extracted = 1 WHERE bulletin_pdf_id = %s",
                (bulletin_pdf_id,),
            )
            continue

        # Phase 4: Extract names from each column
        all_names = []
        for col_text in column_texts:
            names = extract_names_from_text(col_text, church_name)
            all_names.extend(names)

        # Deduplicate
        seen = set()
        unique_names = []
        for n in all_names:
            key = n["name"].lower()
            if key not in seen:
                seen.add(key)
                unique_names.append(n)

        # Phase 5: NER veto gate (batch for efficiency)
        if unique_names:
            name_strings = [n["name"] for n in unique_names]
            contexts = [n.get("context") or "" for n in unique_names]
            ner_results = ner_veto_batch(name_strings, contexts)
        else:
            ner_results = []

        # Phase 6: Couple detection + scoring + insert
        names_inserted = 0
        for name_dict, ner_pass in zip(unique_names, ner_results):
            person_name = name_dict["name"]

            # Couple detection — split "John & Mary Smith" into two records
            individuals = detect_couple(person_name)

            for individual_name, split_type in individuals:
                # Parse with nameparser
                parts = parse_name_parts(individual_name)
                first_name = parts.get("first_name") or ""
                last_name = parts.get("last_name") or ""

                # Layer 1 fallback: try harder if primary parsing left blanks
                if not first_name or not last_name:
                    fb = parse_first_last_from_person_name(individual_name)
                    first_name = first_name or fb.get("first_name", "")
                    last_name = last_name or fb.get("last_name", "")

                category = name_dict.get("category") or ""
                if not category:
                    category = parse_category_from_context(name_dict.get("context") or "")

                role = name_dict.get("role") or ""
                if not role:
                    role = parse_role_from_context(name_dict.get("context") or "")

                title = parts.get("title") or ""
                if not title:
                    title = parse_title_from_person_name(individual_name)

                # Score confidence
                raw_score = score_name_confidence(
                    individual_name,
                    category=category,
                    role=role,
                    title=title,
                )

                # NER veto: downgrade if NER doesn't confirm PERSON
                is_suspect = 0
                if not ner_pass:
                    raw_score = min(raw_score, 0.35)
                    is_suspect = 1

                conf = confidence_label(raw_score)

                # INSERT into bulletin_name (check for dups first)
                cur.execute(
                    "SELECT 1 FROM bulletin_name "
                    "WHERE bulletin_pdf_id = %s AND person_name = %s LIMIT 1",
                    (bulletin_pdf_id, individual_name[:100]),
                )
                if cur.fetchone():
                    continue

                cur.execute(
                    """
                    INSERT INTO bulletin_name
                        (bulletin_pdf_id, person_name, first_name, last_name,
                         title, middle_name,
                         confidence, is_suspect, role, context, category)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                    (
                        bulletin_pdf_id,
                        individual_name[:100],
                        first_name[:50],
                        last_name[:50],
                        title[:20],
                        (parts.get("middle_name") or "")[:50],
                        conf,
                        is_suspect,
                        role[:100],
                        (name_dict.get("context") or "")[:500],
                        category[:30],
                    ),
                )
                names_inserted += cur.rowcount

        stats["names_inserted"] += names_inserted
        stats["pdfs_extracted"] += 1

        # Mark PDF as extracted
        cur.execute(
            "UPDATE bulletin_pdf SET text_extracted = 1 WHERE bulletin_pdf_id = %s",
            (bulletin_pdf_id,),
        )

    return stats


def process_unextracted_pdfs(cur):
    """Process PDFs that were downloaded but not yet extracted.

    Returns dict of stats.
    """
    cur.execute("""
        SELECT bp.bulletin_pdf_id, bp.pdf_url, bp.bulletin_source_id,
               bs.church_id, c.name AS church_name
        FROM bulletin_pdf bp
        JOIN bulletin_source bs ON bs.bulletin_source_id = bp.bulletin_source_id
        JOIN church c ON c.church_id = bs.church_id
        WHERE bp.text_extracted = 0
        ORDER BY bp.downloaded_at DESC
        LIMIT 500
    """)
    rows = cur.fetchall()
    if not rows:
        print("  No unextracted PDFs found.")
        return {"pdfs_extracted": 0, "names_inserted": 0}

    print(f"  Found {len(rows)} unextracted PDFs to process...")
    stats = {"pdfs_extracted": 0, "names_inserted": 0}

    for row in rows:
        bulletin_pdf_id = row["bulletin_pdf_id"]
        pdf_url = row["pdf_url"]
        church_name = row["church_name"] or ""

        pdf_bytes = download_pdf_to_memory(pdf_url)
        if not pdf_bytes:
            cur.execute(
                "UPDATE bulletin_pdf SET text_extracted = 1 WHERE bulletin_pdf_id = %s",
                (bulletin_pdf_id,),
            )
            continue

        full_text, column_texts = extract_text_from_pdf(pdf_bytes)
        if not full_text:
            cur.execute(
                "UPDATE bulletin_pdf SET text_extracted = 1 WHERE bulletin_pdf_id = %s",
                (bulletin_pdf_id,),
            )
            continue

        all_names = []
        for col_text in column_texts:
            names = extract_names_from_text(col_text, church_name)
            all_names.extend(names)

        seen = set()
        unique_names = []
        for n in all_names:
            key = n["name"].lower()
            if key not in seen:
                seen.add(key)
                unique_names.append(n)

        if unique_names:
            name_strings = [n["name"] for n in unique_names]
            contexts = [n.get("context") or "" for n in unique_names]
            ner_results = ner_veto_batch(name_strings, contexts)
        else:
            ner_results = []

        names_inserted = 0
        for name_dict, ner_pass in zip(unique_names, ner_results):
            individuals = detect_couple(name_dict["name"])
            for individual_name, _ in individuals:
                parts = parse_name_parts(individual_name)
                first_name = parts.get("first_name") or ""
                last_name = parts.get("last_name") or ""

                # Layer 1 fallback: try harder if primary parsing left blanks
                if not first_name or not last_name:
                    fb = parse_first_last_from_person_name(individual_name)
                    first_name = first_name or fb.get("first_name", "")
                    last_name = last_name or fb.get("last_name", "")

                category = name_dict.get("category") or ""
                if not category:
                    category = parse_category_from_context(name_dict.get("context") or "")

                role = name_dict.get("role") or ""
                if not role:
                    role = parse_role_from_context(name_dict.get("context") or "")

                title = parts.get("title") or ""
                if not title:
                    title = parse_title_from_person_name(individual_name)

                raw_score = score_name_confidence(
                    individual_name,
                    category=category,
                    role=role,
                    title=title,
                )

                is_suspect = 0
                if not ner_pass:
                    raw_score = min(raw_score, 0.35)
                    is_suspect = 1

                conf = confidence_label(raw_score)

                cur.execute(
                    "SELECT 1 FROM bulletin_name "
                    "WHERE bulletin_pdf_id = %s AND person_name = %s LIMIT 1",
                    (bulletin_pdf_id, individual_name[:100]),
                )
                if cur.fetchone():
                    continue

                cur.execute(
                    """
                    INSERT INTO bulletin_name
                        (bulletin_pdf_id, person_name, first_name, last_name,
                         title, middle_name,
                         confidence, is_suspect, role, context, category)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                    (
                        bulletin_pdf_id,
                        individual_name[:100],
                        first_name[:50],
                        last_name[:50],
                        title[:20],
                        (parts.get("middle_name") or "")[:50],
                        conf,
                        is_suspect,
                        role[:100],
                        (name_dict.get("context") or "")[:500],
                        category[:30],
                    ),
                )
                names_inserted += cur.rowcount

        stats["names_inserted"] += names_inserted
        stats["pdfs_extracted"] += 1

        cur.execute(
            "UPDATE bulletin_pdf SET text_extracted = 1 WHERE bulletin_pdf_id = %s",
            (bulletin_pdf_id,),
        )

    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Extract bulletin names directly to db99")
    parser.add_argument("--state", type=str, help="One state code (e.g. OH)")
    parser.add_argument("--limit", type=int, default=0, help="Max churches per state (0=all)")
    parser.add_argument(
        "--batch-size", type=int, default=5, help="Churches between progress prints"
    )
    parser.add_argument(
        "--days-fresh",
        type=int,
        default=7,
        help="Skip churches discovered within N days (0=process all)",
    )
    parser.add_argument(
        "--skip-discovery",
        action="store_true",
        help="Only extract from already-discovered PDFs (no new web crawling)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Extract Bulletin Names -> db99 (Direct)")
    print("=" * 60)

    conn = get_connection()
    cur = conn.cursor()
    ensure_schema(cur)

    # If skip-discovery, just process unextracted PDFs and exit
    if args.skip_discovery:
        print("\n  Mode: extract-only (no new discovery)")
        stats = process_unextracted_pdfs(cur)
        print(f"\n  PDFs extracted: {stats['pdfs_extracted']}")
        print(f"  Names inserted: {stats['names_inserted']}")
        print("[OK] Done!")
        conn.close()
        return 0

    # Get churches with website URLs from db99
    where_clauses = ["website_url IS NOT NULL", "website_url != ''"]
    params = []

    if args.state:
        where_clauses.append("state_code = %s")
        params.append(args.state.upper())

    # Skip Facebook pages and diocese-level sites
    where_clauses.append("website_url NOT LIKE '%%facebook.com%%'")
    where_clauses.append("website_url NOT LIKE '%%diocese%%'")
    where_clauses.append("website_url NOT LIKE '%%archdiocese%%'")

    where = " AND ".join(where_clauses)
    cur.execute(
        f"SELECT church_id, slug, name, city, state_code, website_url "
        f"FROM church WHERE {where} ORDER BY state_code, slug",
        params,
    )
    churches = cur.fetchall()
    if args.limit:
        churches = churches[: args.limit]

    print(f"Churches to process: {len(churches)}")

    start = time.time()
    totals = {
        "discovered": 0,
        "pdfs_found": 0,
        "pdfs_extracted": 0,
        "names_inserted": 0,
        "skipped": 0,
        "errors": 0,
    }

    for i, church in enumerate(churches):
        church_id = church["church_id"]
        slug = church["slug"]

        # Check if recently processed (skip fresh churches)
        if args.days_fresh > 0:
            cur.execute(
                "SELECT discovered_at FROM bulletin_source "
                "WHERE church_id = %s ORDER BY discovered_at DESC LIMIT 1",
                (church_id,),
            )
            existing = cur.fetchone()
            if existing and existing.get("discovered_at"):
                scraped_at = existing["discovered_at"]
                if hasattr(scraped_at, "replace"):
                    scraped_at = scraped_at.replace(tzinfo=UTC)
                days_ago = (datetime.now(UTC) - scraped_at).days
                if days_ago < args.days_fresh:
                    totals["skipped"] += 1
                    continue

        try:
            stats = process_church(cur, church)
            totals["discovered"] += 1
            totals["pdfs_found"] += stats["pdfs_found"]
            totals["pdfs_extracted"] += stats["pdfs_extracted"]
            totals["names_inserted"] += stats["names_inserted"]
        except Exception as e:
            totals["errors"] += 1
            if totals["errors"] <= 10:
                print(f"  [ERR] {slug}: {e}")

        # Progress
        if (i + 1) % args.batch_size == 0:
            elapsed = time.time() - start
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            remaining = (len(churches) - i - 1) / rate if rate > 0 else 0
            print(
                f"  [{i + 1}/{len(churches)}] {church.get('state_code', '')} "
                f"discovered={totals['discovered']} pdfs={totals['pdfs_extracted']} "
                f"names={totals['names_inserted']} skip={totals['skipped']} "
                f"err={totals['errors']} "
                f"({rate:.1f}/s, ~{remaining / 60:.0f}min left)"
            )

        # Small delay between churches to avoid rate limiting
        time.sleep(0.5)

    elapsed = time.time() - start

    # Log the run
    cur.execute(
        """
        INSERT INTO scrape_log (
            scrape_type, completed_at, status,
            communities_scraped, churches_scraped, services_upserted,
            errors, notes
        ) VALUES (%s, NOW(), %s, %s, %s, %s, %s, %s)
    """,
        (
            "bulletin_extraction",
            "completed" if totals["errors"] == 0 else "partial",
            len(set(c["state_code"] for c in churches)),
            totals["discovered"],
            totals["names_inserted"],
            str(totals["errors"]) if totals["errors"] else None,
            f"discovered={totals['discovered']} pdfs={totals['pdfs_extracted']} "
            f"names={totals['names_inserted']} skip={totals['skipped']} "
            f"err={totals['errors']} in {elapsed:.0f}s",
        ),
    )

    print(f"\n{'=' * 60}")
    print("  BULLETIN EXTRACTION SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Churches discovered: {totals['discovered']}")
    print(f"  PDFs found: {totals['pdfs_found']}")
    print(f"  PDFs extracted: {totals['pdfs_extracted']}")
    print(f"  Names inserted: {totals['names_inserted']}")
    print(f"  Skipped (fresh): {totals['skipped']}")
    print(f"  Errors: {totals['errors']}")
    print(f"  Time: {elapsed:.0f}s ({elapsed / 60:.1f} min)")
    print("[OK] Done!")

    conn.close()
    return 0 if totals["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
