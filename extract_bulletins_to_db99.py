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
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pymysql
import requests

# Import reusable functions from the bulletin scraper
from run_bulletin_scraper import (
    confidence_label,
    detect_couple,
    extract_date_score,
    extract_names_from_text,
    extract_text_from_pdf,
    find_bulletin_page,
    ner_veto_batch,
    parse_name_parts,
    prewarm_shared_state,
    score_name_confidence,
)
from src.utils import host_policy
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

# Proxy use is PER HOST, never global. Setting PROXY_URL alone no longer tunnels
# anything: a host must be labelled needs_proxy in scrape_host_policy, which is
# only awarded when the proxy was measured to actually fix that host. See
# src/utils/host_policy.py — hosts that fail direct AND through the proxy are
# 'blocked', and sending those through it would spend metered GB for the same 403.
PROXY_URL = os.environ.get("PROXY_URL", "").strip() or None

# Tunable because memory, not bandwidth, is the binding constraint: a 25MB PDF
# can expand to hundreds of MB inside pdfplumber, and several workers doing that
# at once on a 2GB instance is what OOMs a shard.
MAX_PDF_SIZE_MB = int(os.environ.get("MAX_PDF_SIZE_MB", "25"))
# How deep into a parish's archive to go. Raised from 100 because at least one
# church hit that ceiling exactly, meaning its back-editions were truncated at
# an unknown depth — and back-editions are the point of the recovery sweep.
MAX_PDFS_PER_CHURCH = int(os.environ.get("MAX_PDFS_PER_CHURCH", "400"))


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

    # Rotation watermark: stamped on EVERY bulletin attempt, hit or miss, so the
    # weekly run resumes where the last one stopped instead of restarting at AK.
    cur.execute("""
        SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'church_scrapes'
          AND TABLE_NAME = 'church'
          AND COLUMN_NAME = 'bulletin_checked_at'
    """)
    if not cur.fetchone():
        cur.execute("ALTER TABLE church ADD COLUMN bulletin_checked_at DATETIME NULL")
        print("  [OK] Added column church.bulletin_checked_at")

    cur.execute("""
        SELECT INDEX_NAME FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = 'church_scrapes'
          AND TABLE_NAME = 'church'
          AND INDEX_NAME = 'idx_church_bulletin_checked_at'
    """)
    if not cur.fetchone():
        cur.execute(
            "ALTER TABLE church ADD INDEX idx_church_bulletin_checked_at (bulletin_checked_at)"
        )
        print("  [OK] Added index idx_church_bulletin_checked_at")


# ---------------------------------------------------------------------------
# Bulletin extraction pipeline
# ---------------------------------------------------------------------------


def download_pdf_to_memory(url, timeout=30):
    """Download a PDF into memory (no disk). Returns bytes or None."""
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            proxies=host_policy.proxies_for(url),
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


_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}


def _valid(y, m, d):
    """ISO date string if this is a real, plausible bulletin date, else None."""
    try:
        dt = datetime(y, m, d, tzinfo=UTC)
    except ValueError:
        return None  # e.g. Feb 30 — the digits were not a date
    # Bulletins predating the web, or dated in the future, are parse errors.
    if not (datetime(1995, 1, 1, tzinfo=UTC) <= dt <= datetime.now(UTC)):
        return None
    return dt.strftime("%Y-%m-%d")


def pdf_date_from_url(pdf_url):
    """Edition date parsed out of a bulletin PDF URL, or None.

    Bulletin filenames almost always carry the edition date, in one of a
    handful of shapes:
        .../bulletins/20230521_t-1684641512000.pdf      YYYYMMDD
        .../Bulletin September 22 2024.pdf              month name
        .../4338_Cecilia_Bos_2_8_2026.pdf               M_D_YYYY
        .../Bulletin 8-3-25.pdf                         M-D-YY
        .../1eef/05/30/24/173737-....pdf                MM/DD/YY in path
        .../wp-content/uploads/2026/07/bulletin.pdf     YYYY/MM (day unknown)

    That is the REAL publish date; the download timestamp is not. Anything
    ambiguous stays None — an undated edition must not claim to be this
    week's. Patterns are tried most-specific first so a full year beats a
    two-digit one.
    """
    if not pdf_url:
        return None

    # Drop the query string first — cache-buster epochs (?t=1768507880000) are
    # not dates. Split the filename off while slashes are still percent-encoded,
    # so an encoded separator (6%2F15%2F2025.pdf) stays part of the filename
    # instead of being read as directories, THEN decode.
    raw_path = pdf_url.split("?", 1)[0].split("#", 1)[0]
    path = unquote(raw_path)
    # Underscores and plusses stand in for spaces in most parish CMS filenames;
    # a decoded slash inside the filename is just another date separator.
    fname = re.sub(r"[_+]", " ", unquote(raw_path.rsplit("/", 1)[-1])).replace("/", ".")

    # Patterns run against the FILENAME only. Matching across the whole URL lets
    # a UUID tail glue onto the filename (".../74fc...f3ab07/07-06-25-x.pdf"
    # once parsed as 2006-07-07 instead of 2025-07-06), so "/" is deliberately
    # not a separator here.
    sep = r"[-. ]"

    # 1. YYYYMMDD — unambiguous, and the most common shape.
    m = re.search(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)", fname)
    if m:
        got = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if got:
            return got

    # 1b. YYYY-MM-DD with separators ("bulletin 2026 01 26-02-01" — a date
    #     range, whose START is the edition date). Must precede the two-digit
    #     year rule, which would otherwise read "01 26-02" as Jan 26 2002.
    m = re.search(rf"(?<!\d)(20\d{{2}}){sep}(0[1-9]|1[0-2]){sep}(0[1-9]|[12]\d|3[01])(?!\d)", fname)
    if m:
        got = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if got:
            return got

    # 2. Month name + day + 4-digit year ("September 22 2024", "january 11 2026").
    m = re.search(
        r"(?<![a-z])(" + "|".join(_MONTHS) + r")[ .,-]*(\d{1,2})(?:st|nd|rd|th)?[ .,-]+(20\d{2})",
        fname, re.IGNORECASE,
    )
    if m:
        got = _valid(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
        if got:
            return got

    # 3. M-D-YYYY / M.D.YYYY / M D YYYY.
    m = re.search(rf"(?<!\d)(\d{{1,2}}){sep}(\d{{1,2}}){sep}(20\d{{2}})(?!\d)", fname)
    if m:
        got = _valid(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        if got:
            return got

    # 4. Two-digit year (M-D-YY). Ambiguous, so it runs last of the filename
    #    patterns and still has to survive _valid().
    m = re.search(rf"(?<!\d)(0?[1-9]|1[0-2]){sep}(0?[1-9]|[12]\d|3[01]){sep}(\d{{2}})(?!\d)", fname)
    if m:
        got = _valid(2000 + int(m.group(3)), int(m.group(1)), int(m.group(2)))
        if got:
            return got

    # 5. Date carried by the directory structure instead of the filename.
    #    Anchored between slashes so it cannot straddle a path boundary.
    m = re.search(r"/(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/(\d{2})/", path)  # /MM/DD/YY/
    if m:
        got = _valid(2000 + int(m.group(3)), int(m.group(1)), int(m.group(2)))
        if got:
            return got

    m = re.search(r"/(20\d{2})/(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/", path)  # /YYYY/MM/DD/
    if m:
        got = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if got:
            return got

    # 6. /YYYY/MM/ upload path. Least specific, tried last.
    m = re.search(r"/(20\d{2})/(0[1-9]|1[0-2])/", path)
    if m:
        year, month = int(m.group(1)), int(m.group(2))

        # A bare 6-digit run in the filename is usually YYMMDD but could just as
        # easily be an id, so it is only trusted when its year and month agree
        # with the upload path ("/2023/11/31353_231126.pdf" -> 2023-11-26).
        # That agreement is what makes it safe; alone it would be a guess.
        for cand in re.findall(r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)", fname):
            cy, cm, cd = 2000 + int(cand[0]), int(cand[1]), int(cand[2])
            if cy == year and cm == month:
                got = _valid(cy, cm, cd)
                if got:
                    return got

        # Otherwise the day is genuinely unknown. Anchor to the 1st — the month
        # is right even though the day (and so the ISO week) may not be.
        got = _valid(year, month, 1)
        if got:
            return got

    return None


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

    # UPSERT bulletin_source.
    #
    # A failed discovery must NOT clobber a known-good record. Discovery fails
    # for transient reasons — a slow site, a timeout, a worker killed mid-crawl —
    # and the old unconditional UPSERT then overwrote a real bulletin_page_url
    # with the bare homepage and marked it 'not_found'. That is not hypothetical:
    # it cost 1,441 sources their page URL during this recovery, including
    # parishes holding 3,800 stored PDFs, whose pages plainly do exist.
    #
    # So on failure only touch discovered_at (the church WAS checked, which is
    # what the rotation needs); leave the previous discovery in place.
    if source_type == "not_found":
        cur.execute(
            """
            INSERT INTO bulletin_source (church_id, discovery_source, bulletin_page_url, discovered_at)
            VALUES (%s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE discovered_at = NOW()
        """,
            (church_id, source_type[:30], bulletin_page_url[:2048]),
        )
    else:
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
        edition_date = pdf_date_from_url(pdf_url)

        if existing_pdf:
            bulletin_pdf_id = existing_pdf["bulletin_pdf_id"]
            # Fill in the edition date on rows inserted before we parsed it.
            if edition_date:
                cur.execute(
                    "UPDATE bulletin_pdf SET pdf_date = %s "
                    "WHERE bulletin_pdf_id = %s AND pdf_date IS NULL",
                    (edition_date, bulletin_pdf_id),
                )
        else:
            cur.execute(
                """
                INSERT INTO bulletin_pdf (bulletin_source_id, pdf_url, pdf_date, downloaded_at)
                VALUES (%s, %s, %s, NOW())
            """,
                (bulletin_source_id, pdf_url[:2048], edition_date),
            )
            bulletin_pdf_id = cur.lastrowid

        if not bulletin_pdf_id:
            continue

        # Phase 3: Extract text (from bytes, not file)
        full_text, column_texts = extract_text_from_pdf(pdf_bytes)
        if not full_text:
            cur.execute(
                "UPDATE bulletin_pdf SET text_extracted = 1 " "WHERE bulletin_pdf_id = %s",
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
            "UPDATE bulletin_pdf SET text_extracted = 1 " "WHERE bulletin_pdf_id = %s",
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
                "UPDATE bulletin_pdf SET text_extracted = 1 " "WHERE bulletin_pdf_id = %s",
                (bulletin_pdf_id,),
            )
            continue

        full_text, column_texts = extract_text_from_pdf(pdf_bytes)
        if not full_text:
            cur.execute(
                "UPDATE bulletin_pdf SET text_extracted = 1 " "WHERE bulletin_pdf_id = %s",
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
            "UPDATE bulletin_pdf SET text_extracted = 1 " "WHERE bulletin_pdf_id = %s",
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
    parser.add_argument(
        "--max-runtime-minutes",
        type=int,
        default=0,
        help="Exit cleanly after N minutes so the rotation watermark is kept (0=no cap)",
    )
    parser.add_argument(
        "--known-sources-only",
        action="store_true",
        help="Only refresh churches that already have a bulletin page (the name producers)",
    )
    parser.add_argument(
        "--discovery-only",
        action="store_true",
        help="Only crawl churches with no bulletin page yet (hunt for new sources)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("BULLETIN_WORKERS", "12")),
        help="Concurrent churches. Rate limiting is per-host, so parallel "
        "workers hit different parishes rather than the same server.",
    )
    parser.add_argument(
        "--shards", type=int, default=1, help="Split the queue across N containers"
    )
    parser.add_argument(
        "--shard", type=int, default=0, help="Which shard this process handles (0-based)"
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

    # Skip churches checked within N days, in SQL — the old per-row lookup on
    # bulletin_source.discovered_at never skipped churches that have no bulletin
    # page at all, so every run re-crawled the same ~13k dead ends and timed out.
    if args.days_fresh > 0:
        where_clauses.append(
            "(bulletin_checked_at IS NULL OR bulletin_checked_at < NOW() - INTERVAL %s DAY)"
        )
        params.append(args.days_fresh)

    where = " AND ".join(where_clauses)

    # Priority, then staleness:
    #   1. churches with a known bulletin page  — these publish a NEW bulletin
    #      every week, so they are the only ones that yield fresh names, and a
    #      capped run must reach all of them before spending time anywhere else;
    #   2. churches that have never yielded one — re-crawled for discovery with
    #      whatever window is left (hit rate is ~1%, so they must not go first).
    # Within each tier, least-recently-checked first, so each capped run resumes
    # the frontier the previous one left off at instead of restarting at AK.
    if args.discovery_only:
        where += " AND NOT EXISTS (SELECT 1 FROM bulletin_source bs WHERE bs.church_id = c.church_id)"
    elif args.known_sources_only:
        where += " AND EXISTS (SELECT 1 FROM bulletin_source bs WHERE bs.church_id = c.church_id)"

    # Sharding splits the queue across several CONTAINERS. Threads only overlap
    # network waits — PDF text extraction is CPU-bound and the GIL serialises it,
    # so one 1-vCPU instance plateaus regardless of --workers. Disjoint shards
    # give real parallelism, and because bulletin_pdf has no unique index the
    # shards MUST NOT overlap or concurrent processes would duplicate rows.
    if args.shards > 1:
        where += " AND MOD(c.church_id, %s) = %s"
        params.extend([args.shards, args.shard])

    cur.execute(
        f"SELECT c.church_id, c.slug, c.name, c.city, c.state_code, c.website_url "
        f"FROM church c WHERE {where} "
        f"ORDER BY EXISTS (SELECT 1 FROM bulletin_source bs "
        f"                 WHERE bs.church_id = c.church_id) DESC, "
        f"         c.bulletin_checked_at IS NOT NULL, "
        f"         c.bulletin_checked_at ASC, c.church_id",
        params,
    )
    churches = cur.fetchall()
    if args.limit:
        churches = churches[: args.limit]

    if args.shards > 1:
        print(f"Shard {args.shard}/{args.shards}")
    print(f"Churches to process: {len(churches)}")
    if args.max_runtime_minutes:
        print(f"Runtime cap: {args.max_runtime_minutes} min (exits cleanly, progress kept)")

    start = time.time()
    totals = {
        "discovered": 0,
        "pdfs_found": 0,
        "pdfs_extracted": 0,
        "names_inserted": 0,
        "skipped": 0,
        "errors": 0,
        "stopped_early": False,
    }

    max_runtime_s = args.max_runtime_minutes * 60 if args.max_runtime_minutes else 0

    # Warm every lazily-cached global (spaCy model, SSA/Census reference data)
    # before the pool starts, so workers do not race to load them.
    prewarm_shared_state()

    stop = threading.Event()
    lock = threading.Lock()
    counter = {"done": 0}
    # Each worker needs its OWN connection: a pymysql connection is not safe to
    # share across threads.
    local = threading.local()

    def worker(idx_church):
        i, church = idx_church
        if stop.is_set():
            return
        slug = church["slug"]

        conn_t = getattr(local, "conn", None)
        if conn_t is None:
            conn_t = get_connection()
            conn_t.autocommit(True)
            local.conn = conn_t
        cur_t = conn_t.cursor()

        # CLAIM the church before processing it, not after.
        #
        # Stamping only on success made a church that kills its worker immortal:
        # it stays at the head of a queue ordered oldest-checked-first, so every
        # relaunched shard picks it up again, dies on it again, and never gets
        # past it. That is what stalled the sweep with 2,477 churches left —
        # parishes holding 3,000-3,900 PDFs, last stamped in May, blocking the
        # line on every single run.
        #
        # Claiming first turns an infinite retry into one attempt. The cost is
        # that a church whose worker dies mid-way waits for the next cycle
        # instead of being retried immediately, which is the right trade: one
        # deferred church beats a permanently blocked queue.
        try:
            cur_t.execute(
                "UPDATE church SET bulletin_checked_at = NOW() WHERE church_id = %s",
                (church["church_id"],),
            )
        except Exception as e:
            print(f"  [ERR] claim {slug}: {e}", flush=True)

        try:
            stats = process_church(cur_t, church)
            with lock:
                totals["discovered"] += 1
                totals["pdfs_found"] += stats["pdfs_found"]
                totals["pdfs_extracted"] += stats["pdfs_extracted"]
                totals["names_inserted"] += stats["names_inserted"]
        except Exception as e:
            with lock:
                totals["errors"] += 1
                if totals["errors"] <= 10:
                    print(f"  [ERR] {slug}: {e}", flush=True)

        # Already claimed above; nothing to stamp here.
        cur_t.close()

        with lock:
            counter["done"] += 1
            n = counter["done"]
            if n % args.batch_size == 0:
                el = time.time() - start
                rate = n / el if el > 0 else 0
                left = (len(churches) - n) / rate if rate > 0 else 0
                print(
                    f"  [{n}/{len(churches)}] {church.get('state_code', '')} "
                    f"discovered={totals['discovered']} pdfs={totals['pdfs_extracted']} "
                    f"names={totals['names_inserted']} err={totals['errors']} "
                    f"({rate*60:.1f}/min, ~{left/60:.0f}min left)",
                    flush=True,
                )

    print(f"Workers: {args.workers}")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(worker, (i, c)) for i, c in enumerate(churches)]
        try:
            for _ in as_completed(futures):
                # Stop cleanly before the cron kills us, so watermarks survive
                # and the next run resumes from the frontier.
                if max_runtime_s and (time.time() - start) > max_runtime_s:
                    if not stop.is_set():
                        totals["stopped_early"] = True
                        print(
                            f"\n  [STOP] Runtime cap hit after {counter['done']}"
                            f"/{len(churches)} churches. Progress saved.",
                            flush=True,
                        )
                        stop.set()
                        for f in futures:
                            f.cancel()
        except KeyboardInterrupt:
            stop.set()
            raise

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
            f"err={totals['errors']} in {elapsed:.0f}s"
            + (" [runtime-cap]" if totals["stopped_early"] else " [queue-drained]"),
        ),
    )

    print(f"\n{'='*60}")
    print("  BULLETIN EXTRACTION SUMMARY")
    print(f"{'='*60}")
    print(f"  Churches discovered: {totals['discovered']}")
    print(f"  PDFs found: {totals['pdfs_found']}")
    print(f"  PDFs extracted: {totals['pdfs_extracted']}")
    print(f"  Names inserted: {totals['names_inserted']}")
    print(f"  Skipped (fresh): {totals['skipped']}")
    print(f"  Errors: {totals['errors']}")
    print(f"  Time: {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print("[OK] Done!")

    conn.close()
    return 0 if totals["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
