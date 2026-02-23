"""
run_bulletin_scraper.py

Scrape church bulletins (PDFs) from parish websites, extract text, and pull out
all people's names mentioned in them.

PIPELINE:
  Phase 1 — Discover: Find bulletin pages on church websites
  Phase 2 — Download: Download bulletin PDFs
  Phase 3 — Extract: Extract text from PDFs and identify names via pattern matching

HOW TO RUN:
    python run_bulletin_scraper.py discover arizona             # Phase 1: find bulletin pages
    python run_bulletin_scraper.py download arizona             # Phase 2: download PDFs
    python run_bulletin_scraper.py extract arizona              # Phase 3: extract text + names
    python run_bulletin_scraper.py all arizona                  # Run all phases
    python run_bulletin_scraper.py all arizona georgia          # Multiple states
    python run_bulletin_scraper.py all arizona --limit 10       # Test with first 10 churches
    python run_bulletin_scraper.py all arizona --resume         # Resume interrupted run

OUTPUT:
    data/output/{state}/bulletin_discovery.json     — bulletin page URLs per church
    data/output/{state}/bulletins/                   — downloaded PDF files
    data/output/{state}/bulletin_texts/              — extracted text files
    data/output/{state}/bulletin_names.csv           — extracted names per church/bulletin
    data/output/{state}/bulletin_progress.json       — progress tracking for resume

APPROACH:
    1. For each church with a website URL, try common bulletin page paths
    2. On the bulletin page, find all PDF links
    3. Download the most recent bulletin PDF(s)
    4. Extract text using pdfplumber
    5. Use regex patterns to find people's names (Mass intentions, prayer lists,
       staff listings, ministry leaders, etc.)
"""

import sys
import json
import time
import re
import csv
import argparse
import hashlib
import os
import traceback
import requests
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, parse_qs
from bs4 import BeautifulSoup

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False
    print("WARNING: pdfplumber not installed. PDF extraction will be skipped.")
    print("Install with: pip install pdfplumber")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import OUTPUT_DIR
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

REQUEST_DELAY = 1.0          # seconds between requests to same domain
REQUEST_TIMEOUT = 15         # seconds
MAX_PDFS_PER_CHURCH = 100    # download all available bulletins (most churches have 20-52 weeks)
MAX_PDF_SIZE_MB = 25         # skip PDFs larger than 25MB
PROGRESS_SAVE_INTERVAL = 10  # save progress every N churches

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Common bulletin page URL paths to try (most common first)
BULLETIN_PATHS = [
    "/bulletin",
    "/bulletins",
    "/bulletin/",
    "/bulletins/",
    "/bulletins2",
    "/weekly-bulletin",
    "/parish-bulletin",
    "/sunday-bulletin",
    "/home/downloads",
    "/home/downloads/",
    "/news/bulletins",
    "/resources/bulletin",
    "/media/bulletin",
    "/publications/bulletin",
    "/about/bulletin",
]

# Keywords that indicate a bulletin page (case-insensitive)
BULLETIN_PAGE_KEYWORDS = [
    "bulletin", "weekly bulletin", "parish bulletin",
    "sunday bulletin", "church bulletin", "current bulletin",
    "this week", "latest bulletin", "download bulletin",
]

# Patterns to find bulletin links on church homepages
BULLETIN_LINK_PATTERNS = [
    re.compile(r'bulletin', re.IGNORECASE),
    re.compile(r'weekly\s*(news|update|publication)', re.IGNORECASE),
]

# State aliases (same as run_resolve_urls.py)
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


def resolve_state(name: str):
    key = name.lower().replace(" ", "_").replace("-", "_")
    return STATE_ALIASES.get(key)


# ── HTTP Helpers ───────────────────────────────────────────────────────────────

_last_request_time = 0.0
_session = requests.Session()
_session.headers.update(HEADERS)


def _rate_limited_get(url: str, timeout: int = REQUEST_TIMEOUT, allow_redirects=True):
    """Make a rate-limited GET request. Returns Response or None."""
    global _last_request_time

    elapsed = time.time() - _last_request_time
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)
    _last_request_time = time.time()

    try:
        resp = _session.get(url, timeout=timeout, allow_redirects=allow_redirects)
        return resp
    except requests.exceptions.RequestException as e:
        logger.debug(f"Request failed for {url}: {e}")
        return None


def safe_get_html(url: str):
    """Fetch a page and return BeautifulSoup, or None on failure."""
    resp = _rate_limited_get(url)
    if resp is None or resp.status_code != 200:
        return None
    try:
        return BeautifulSoup(resp.text, "lxml")
    except Exception:
        return BeautifulSoup(resp.text, "html.parser")


# ── Phase 1: Bulletin Discovery ───────────────────────────────────────────────

def find_bulletin_page(base_url: str):
    """
    Try to find the bulletin page for a church website.

    Strategy:
    1. Check common paths (/bulletin, /bulletins, etc.)
    2. Fetch homepage and search for bulletin links in nav/content
    3. Check for LPi/ParishesOnline embeds

    Returns dict with:
        bulletin_page_url: str | None
        pdf_urls: list[str]  — direct PDF links found
        source: str — how we found it ('direct_path', 'homepage_link', 'lpi_embed', etc.)
    """
    parsed = urlparse(base_url)
    if not parsed.scheme:
        base_url = "https://" + base_url
        parsed = urlparse(base_url)

    base_origin = f"{parsed.scheme}://{parsed.netloc}"

    result = {
        "bulletin_page_url": None,
        "pdf_urls": [],
        "source": None,
        "lpi_parish_id": None,
    }

    # Strategy 1: Try common bulletin paths
    for path in BULLETIN_PATHS:
        try_url = base_origin + path
        resp = _rate_limited_get(try_url)
        if resp and resp.status_code == 200:
            text_lower = resp.text.lower()
            # Verify this actually looks like a bulletin page
            if any(kw in text_lower for kw in BULLETIN_PAGE_KEYWORDS):
                result["bulletin_page_url"] = resp.url  # may have redirected
                result["source"] = "direct_path"
                # Extract PDFs from this page
                soup = BeautifulSoup(resp.text, "lxml")
                result["pdf_urls"] = extract_pdf_links(soup, resp.url)
                # Check for LPi
                lpi_id = find_lpi_parish_id(soup, resp.text)
                if lpi_id:
                    result["lpi_parish_id"] = lpi_id
                if result["pdf_urls"] or lpi_id:
                    return result
        # After trying 3 paths without success, move to homepage scan
        if path == BULLETIN_PATHS[2] and not result["bulletin_page_url"]:
            break

    # Strategy 2: Fetch homepage and look for bulletin link
    soup = safe_get_html(base_url)
    if soup:
        # Find links with "bulletin" in text or href
        for link in soup.find_all("a", href=True):
            link_text = (link.get_text() or "").strip().lower()
            link_href = link["href"].lower()
            if "bulletin" in link_text or "bulletin" in link_href:
                full_url = urljoin(base_url, link["href"])
                # Skip if it's just the same page
                if full_url.rstrip("/") == base_url.rstrip("/"):
                    continue
                # Check if it's a direct PDF link
                if full_url.lower().endswith(".pdf"):
                    result["pdf_urls"].append(full_url)
                    result["bulletin_page_url"] = base_url
                    result["source"] = "homepage_pdf_link"
                    return result
                # Check if it links to parishesonline.com
                if "parishesonline.com" in full_url or "4lpi.com" in full_url:
                    result["bulletin_page_url"] = full_url
                    result["source"] = "lpi_link"
                    # Try to extract PDF URLs from the LPi page
                    lpi_pdfs = extract_lpi_pdfs(full_url)
                    result["pdf_urls"] = lpi_pdfs
                    return result
                # Follow the link to the bulletin page
                bulletin_soup = safe_get_html(full_url)
                if bulletin_soup:
                    result["bulletin_page_url"] = full_url
                    result["source"] = "homepage_link"
                    result["pdf_urls"] = extract_pdf_links(bulletin_soup, full_url)
                    lpi_id = find_lpi_parish_id(bulletin_soup, str(bulletin_soup))
                    if lpi_id:
                        result["lpi_parish_id"] = lpi_id
                    return result

        # Strategy 3: Check for LPi embed on homepage
        lpi_id = find_lpi_parish_id(soup, str(soup))
        if lpi_id:
            result["lpi_parish_id"] = lpi_id
            result["source"] = "lpi_embed_homepage"
            return result

        # Strategy 4: Check remaining direct paths
        for path in BULLETIN_PATHS[3:]:
            try_url = base_origin + path
            resp = _rate_limited_get(try_url)
            if resp and resp.status_code == 200:
                text_lower = resp.text.lower()
                if any(kw in text_lower for kw in BULLETIN_PAGE_KEYWORDS):
                    page_soup = BeautifulSoup(resp.text, "lxml")
                    pdfs = extract_pdf_links(page_soup, resp.url)
                    if pdfs:
                        result["bulletin_page_url"] = resp.url
                        result["source"] = "direct_path_extended"
                        result["pdf_urls"] = pdfs
                        return result

    return result


def extract_pdf_links(soup, page_url: str):
    """Extract all PDF links from a page, sorted by likely recency."""
    pdf_links = []
    seen = set()

    for link in soup.find_all("a", href=True):
        href = link["href"]
        full_url = urljoin(page_url, href)

        # Check if it's a PDF
        if not (full_url.lower().endswith(".pdf") or ".pdf?" in full_url.lower()):
            continue

        # Skip duplicate URLs
        if full_url in seen:
            continue
        seen.add(full_url)

        link_text = (link.get_text() or "").strip()

        # Try to extract a date from the filename or link text
        date_score = extract_date_score(full_url, link_text)

        pdf_links.append({
            "url": full_url,
            "text": link_text[:100],
            "date_score": date_score,
        })

    # Also check for ParishesOnline.com links in iframes/embeds
    for iframe in soup.find_all("iframe", src=True):
        src = iframe["src"]
        if "parishesonline.com" in src or "4lpi.com" in src:
            # Extract PDF URL from LPi embed
            parsed = urlparse(src)
            params = parse_qs(parsed.query)
            if "selectedPublication" in params:
                pdf_url = params["selectedPublication"][0]
                if pdf_url not in seen:
                    seen.add(pdf_url)
                    pdf_links.append({
                        "url": pdf_url,
                        "text": "LPi Bulletin",
                        "date_score": 99999999,  # assume current
                    })

    # Sort by date (most recent first)
    pdf_links.sort(key=lambda x: x["date_score"], reverse=True)

    return [p["url"] for p in pdf_links[:MAX_PDFS_PER_CHURCH]]


def extract_date_score(url: str, text: str):
    """
    Try to extract a date from URL or link text.
    Returns an int YYYYMMDD for sorting (higher = more recent).
    Returns 0 if no date found.
    """
    combined = url + " " + text

    # Pattern: YYYYMMDD
    m = re.search(r'(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])', combined)
    if m:
        return int(m.group(1) + m.group(2) + m.group(3))

    # Pattern: MM-DD-YYYY or MM/DD/YYYY
    m = re.search(r'(0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])[-/](20\d{2})', combined)
    if m:
        return int(f"{m.group(3)}{int(m.group(1)):02d}{int(m.group(2)):02d}")

    # Pattern: YYYY/MM/ in path (WordPress uploads)
    m = re.search(r'/(20\d{2})/(0[1-9]|1[0-2])/', url)
    if m:
        return int(m.group(1) + m.group(2) + "01")

    # Pattern: Month Day, Year in text
    months = {
        'january': '01', 'february': '02', 'march': '03', 'april': '04',
        'may': '05', 'june': '06', 'july': '07', 'august': '08',
        'september': '09', 'october': '10', 'november': '11', 'december': '12',
        'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04',
        'jun': '06', 'jul': '07', 'aug': '08', 'sep': '09',
        'oct': '10', 'nov': '11', 'dec': '12',
    }
    m = re.search(
        r'(' + '|'.join(months.keys()) + r')[\s._-]+(\d{1,2})[\s,._-]*(20\d{2})',
        combined.lower()
    )
    if m:
        month = months[m.group(1)]
        day = f"{int(m.group(2)):02d}"
        year = m.group(3)
        return int(year + month + day)

    return 0


def find_lpi_parish_id(soup, raw_html: str):
    """Check if page has LPi/ParishesOnline.com bulletin widget."""
    # Look for parishesonline.com URLs
    m = re.search(r'parishesonline\.com/[^"\']*?(\d{4,6})', raw_html)
    if m:
        return m.group(1)

    # Look for LPi embed patterns
    m = re.search(r'container\.parishesonline\.com/bulletins/\d+/(\d+)/', raw_html)
    if m:
        return m.group(1)

    return None


def extract_lpi_pdfs(lpi_url: str):
    """Extract PDF URLs from an LPi/ParishesOnline page."""
    pdfs = []

    # If URL already contains the PDF reference
    parsed = urlparse(lpi_url)
    params = parse_qs(parsed.query)
    if "selectedPublication" in params:
        pdfs.append(params["selectedPublication"][0])

    # Fetch the page and look for more PDFs
    soup = safe_get_html(lpi_url)
    if soup:
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if "container.parishesonline.com" in href and href.endswith(".pdf"):
                full_url = urljoin(lpi_url, href)
                if full_url not in pdfs:
                    pdfs.append(full_url)

    return pdfs[:MAX_PDFS_PER_CHURCH]


# ── Phase 2: PDF Download ─────────────────────────────────────────────────────

def download_bulletin_pdf(pdf_url: str, save_dir: Path, church_slug: str):
    """
    Download a bulletin PDF. Returns the local file path or None.
    """
    # Create a filename from church slug + date hash
    url_hash = hashlib.md5(pdf_url.encode()).hexdigest()[:8]

    # Try to extract date from URL
    date_str = ""
    m = re.search(r'(20\d{6})', pdf_url)
    if m:
        date_str = m.group(1) + "_"
    else:
        m = re.search(r'(20\d{2})[-/](0[1-9]|1[0-2])[-/](\d{2})', pdf_url)
        if m:
            date_str = m.group(1) + m.group(2) + m.group(3) + "_"

    filename = f"{church_slug}_{date_str}{url_hash}.pdf"
    save_path = save_dir / filename

    # Skip if already downloaded
    if save_path.exists() and save_path.stat().st_size > 0:
        logger.debug(f"Already downloaded: {filename}")
        return save_path

    resp = _rate_limited_get(pdf_url)
    if resp is None:
        return None

    if resp.status_code != 200:
        logger.debug(f"PDF download failed ({resp.status_code}): {pdf_url}")
        return None

    # Check content type
    content_type = resp.headers.get("Content-Type", "")
    if "pdf" not in content_type.lower() and "octet" not in content_type.lower():
        # Might be an HTML page, not a PDF
        if "html" in content_type.lower():
            logger.debug(f"Got HTML instead of PDF: {pdf_url}")
            return None

    # Check size
    content_length = len(resp.content)
    if content_length > MAX_PDF_SIZE_MB * 1024 * 1024:
        logger.debug(f"PDF too large ({content_length / 1024 / 1024:.1f}MB): {pdf_url}")
        return None

    if content_length < 1000:
        logger.debug(f"PDF too small ({content_length} bytes), likely error page: {pdf_url}")
        return None

    save_dir.mkdir(parents=True, exist_ok=True)
    save_path.write_bytes(resp.content)
    logger.debug(f"Downloaded: {filename} ({content_length / 1024:.0f}KB)")
    return save_path


# ── Phase 3: Text Extraction + Name Recognition ──────────────────────────────

def extract_text_from_pdf(pdf_path: Path):
    """Extract all text from a PDF using pdfplumber."""
    if not HAS_PDFPLUMBER:
        return ""

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            texts = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    texts.append(text)
            return "\n\n".join(texts)
    except Exception as e:
        logger.debug(f"PDF extraction failed for {pdf_path.name}: {e}")
        return ""


def parse_name_parts(full_name: str) -> dict:
    """
    Split a full name into structured parts: title, first, middle, last.

    The 'title' field captures HONORIFIC prefixes only (Fr., Rev., Dr., etc.).
    Positional roles (Pastor, Chairman, etc.) are captured separately via the
    'role' field in extract_names_from_text() — NOT here.

    Examples:
        "John Smith"        -> {title:"", first:"John", middle:"", last:"Smith"}
        "Mary Jane Wilson"  -> {title:"", first:"Mary", middle:"Jane", last:"Wilson"}
        "Dr. Robert Lee"    -> {title:"Dr.", first:"Robert", middle:"", last:"Lee"}
        "Fr. John M. Smith" -> {title:"Fr.", first:"John", middle:"M.", last:"Smith"}
    """
    result = {"title": "", "first_name": "", "middle_name": "", "last_name": ""}

    if not full_name:
        return result

    parts = full_name.strip().split()
    if not parts:
        return result

    # Check if first token is a title/prefix (honorifics only)
    title_patterns = {
        'fr.', 'father', 'rev.', 'reverend', 'msgr.', 'monsignor',
        'dcn.', 'deacon', 'sr.', 'sister', 'br.', 'brother',
        'dr.', 'doctor', 'mr.', 'mrs.', 'ms.', 'miss',
        'prof.', 'professor', 'bishop', 'archbishop',
    }

    if parts[0].lower().rstrip('.') + '.' in title_patterns or parts[0].lower() in title_patterns:
        result["title"] = parts[0]
        parts = parts[1:]

    if not parts:
        return result

    if len(parts) == 1:
        result["first_name"] = parts[0]
    elif len(parts) == 2:
        result["first_name"] = parts[0]
        result["last_name"] = parts[1]
    elif len(parts) == 3:
        result["first_name"] = parts[0]
        result["middle_name"] = parts[1]
        result["last_name"] = parts[2]
    else:
        # 4+ parts: first, middle initial(s), last
        result["first_name"] = parts[0]
        result["middle_name"] = " ".join(parts[1:-1])
        result["last_name"] = parts[-1]

    return result


def extract_names_from_text(text: str, church_name: str = ""):
    """
    Extract people's names from bulletin text using pattern matching.

    Looks for names in these common bulletin contexts:
    - Staff/clergy listings (with positional roles like Pastor, Business Manager)
    - Section-header roles (DEACONS, PASTORAL COUNCIL headings apply to names below)
    - Ministry contact listings ("Role, Person Name Phone#")
    - Mass intention lists
    - Prayer lists (sick, deceased, military)
    - Ministry schedules (names near ministry keywords)

    Returns list of dicts with:
        name, title, first_name, middle_name, last_name,
        role (positional role like Pastor, Chairman — separate from honorific title),
        context, category

    ROLE vs TITLE:
        - 'title' = honorific prefix: Fr., Rev., Dr., Msgr., etc.
        - 'role'  = positional job/role: Pastor, Business Manager, Chairman, Deacon, etc.
        These are separate fields. A person can have BOTH: role="Pastor", title="Fr."
        e.g. "Pastor Fr. Michael Martinez" -> role="Pastor", title="Fr."
    """
    names = []
    seen_names = set()

    if not text:
        return names

    # Normalize text — fix curly quotes, normalize whitespace
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')

    # ─────────────────────────────────────────────────────────────────────────
    # Pattern 1: Staff/Clergy role-name pairs on the SAME LINE
    # ─────────────────────────────────────────────────────────────────────────
    # Matches structured listings like:
    #   "Pastor        Fr. Michael Martinez"
    #   "Business Manager  Teresa Mullen"
    #   "Chairman      Patrick Ledger"
    #   "Pastor: Fr. John Smith"
    #   "Music Director - Jane Doe"
    #
    # The role is on the left, the name is on the right, separated by
    # whitespace, colon, dash, or en-dash.
    #
    # NOTE: We capture the ROLE (positional title) separately from the
    # honorific TITLE (Fr., Rev., etc.). Both are stored in the output.
    # ─────────────────────────────────────────────────────────────────────────

    # Comprehensive list of positional roles found in bulletin staff sections
    STAFF_ROLES = (
        # Clergy roles
        r'Pastor|Associate Pastor|Parochial Vicar|Parochial Administrator'
        r'|Administrator|Priest|Rector|Chaplain|Celebrant'
        # Deacon roles
        r'|Permanent Deacon|Transitional Deacon'
        # Parish staff
        r'|Business Manager|Business Mgr\.|Office Manager|Parish Manager'
        r'|Parish Secretary|Parish Administrator|Administrative Assistant'
        r'|Bookkeeper|Assistant Bookkeeper|Receptionist'
        r'|Compliance(?:/Acct\.\s*Asst\.)?|Compliance Officer'
        # Education
        r'|Director of Religious (?:Education|Ed)|Religious Ed(?:ucation)?'
        r'|Director of Faith Formation|Faith Formation Director'
        r'|School Principal|School Secretary|Principal'
        r'|Director of Youth Ministry|Youth Minister|Youth Director'
        r'|Director of Music|Music Director|Music Minister'
        r'|Liturgy Director|Liturgist|Worship Director'
        # Facilities
        r'|Maintenance|Custodian|Facilities Manager|Facilities Director'
        r'|Maintenance Tech|Groundskeeper|Sexton'
        # Ministry roles
        r'|Director|Coordinator|Minister|Moderator'
        r'|RCIA Director|RCIA Coordinator'
        r'|Sacristan|Organist|Cantor|Choir Director'
        r'|Stewardship Director|Communications Director'
        r'|Hispanic Ministry|Spanish Ministry'
        # Council/board roles
        r'|Chairman|Co-Chairman|Chairperson|Co-Chair'
        r'|Vice Chairman|Vice Chairperson'
        r'|President|Vice President'
        r'|Secretary|Treasurer'
        r'|Grand Knight|Deputy Grand Knight'
        r'|Financial Secretary|Membership Director'
        r'|ASCS Principal'
    )

    # Name pattern: optional honorific + first [middle] last
    # STRICT version: only 2-4 capitalized words after optional honorific.
    # Does NOT greedily consume trailing text from adjacent PDF columns.
    NAME_PATTERN = (
        r'(?:(?:Fr\.|Father|Rev\.|Reverend|Msgr\.|Monsignor|Dcn\.|Deacon'
        r'|Sr\.|Sister|Br\.|Brother|Dr\.|Bishop|Archbishop)\s+)?'
        r'[A-Z][a-z]{1,15}'                # First name
        r'(?:\s+[A-Z]\.)?'                 # Optional middle initial
        r'(?:\s+(?:De\s+La\s+)?[A-Z][a-z]{1,20}){1,2}'  # Last name (1-2 parts, allows "De La Rosa")
    )

    # Match: Role [separator] Name
    staff_pattern = re.compile(
        rf'({STAFF_ROLES})\s*[:\-–—]?\s+({NAME_PATTERN})',
        re.IGNORECASE
    )

    for m in staff_pattern.finditer(text):
        role_raw = m.group(1).strip()
        name_raw = m.group(2).strip()
        name_raw = re.sub(r'\s+', ' ', name_raw)

        # Clean up trailing noise: phone numbers, email, punctuation
        name_raw = re.sub(r'\s*\d{3}[\-\.]\d{3,4}.*$', '', name_raw)  # phone
        name_raw = re.sub(r'\s*\(?\d{3}\)?.*$', '', name_raw)          # area code
        # Trim trailing words that are clearly NOT part of a name.
        # PDF columns frequently bleed together: "Patrick Ledger Saturday Vigil"
        # We strip everything from the first non-name word onwards.
        # This list includes common bulletin text that gets appended to names.
        name_raw = re.sub(
            r'\s+(?:Saturday|Sunday|Monday|Tuesday|Wednesday|Thursday|Friday'
            r'|Vigil|Mass|Vacant|Open|Position|By|Appointment|Please|Parish'
            r'|Secretary|Vice|www\b|http|Sick|Marriage|Baptism|Members?'
            r'|Business|School|Religious|Compliance|Office|Church|classes'
            r'|First|Anointing|Maintenance|DEACONS?|STAFF|COUNCIL|BOARD'
            r'|for|the|or|and|at|of|in|to|with|on|not|out|reach'
            r'|You|Are|Dcn\b|Dir\b).*$',
            '', name_raw, flags=re.IGNORECASE
        )
        name_raw = name_raw.strip()

        # Skip "Position Open", "Vacant", "TBD", etc.
        if re.match(r'^(?:Position\s+Open|Vacant|TBD|None|Open|N/?A)$', name_raw, re.IGNORECASE):
            continue
        # Skip if it looks like a phrase, not a name
        if re.match(r'^(?:religious|classes|grades|brother|sister|priest|Tech|I\b)', name_raw, re.IGNORECASE):
            continue

        # Validate the name
        name_parts = parse_name_parts(name_raw)
        # Reconstruct the "clean" name (without title) for validation
        clean_name = " ".join(
            p for p in [name_parts["first_name"], name_parts["middle_name"], name_parts["last_name"]] if p
        )

        if not clean_name or len(clean_name) < 4:
            continue

        # Relaxed validation for staff — we trust the role-name structure
        parts = clean_name.split()
        if len(parts) < 2 or len(parts) > 5:
            continue
        if clean_name in FALSE_POSITIVE_NAMES:
            continue

        if clean_name not in seen_names:
            seen_names.add(clean_name)
            names.append({
                "name": clean_name,
                **name_parts,
                "role": role_raw.strip().rstrip(':').rstrip('-').strip(),
                "context": text[max(0, m.start()-20):m.end()+30].strip(),
                "category": "clergy_staff"
            })

    # ─────────────────────────────────────────────────────────────────────────
    # Pattern 1b: Honorific-only clergy (no explicit role keyword on line)
    # ─────────────────────────────────────────────────────────────────────────
    # Catches: "Fr. Michael Martinez" or "Dcn. Reynaldo Romo" appearing
    # anywhere (not just after a role keyword).
    # The title itself implies a role (priest, deacon, etc.)
    # Strict name pattern for honorific matches: FirstName [MiddleInitial] LastName only
    # (max 2-3 capitalized words, no trailing column bleed)
    honorific_pattern = re.compile(
        r'((?:Fr\.|Father|Rev\.|Reverend|Msgr\.|Monsignor|Dcn\.|Deacon'
        r'|Sr\.|Sister|Br\.|Brother|Bishop|Archbishop)\s+'
        r'[A-Z][a-z]{1,15}(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]{1,20})'
    )
    for m in honorific_pattern.finditer(text):
        full_match = m.group(1).strip()
        name_parts = parse_name_parts(full_match)
        # Reconstruct clean name without title
        clean_name = " ".join(
            p for p in [name_parts["first_name"], name_parts["middle_name"], name_parts["last_name"]] if p
        )
        if not clean_name or len(clean_name) < 4:
            continue
        if is_valid_name(clean_name) and clean_name not in seen_names:
            seen_names.add(clean_name)
            # Infer role from honorific
            honorific = full_match.split()[0].lower().rstrip('.')
            implied_role = {
                'fr': 'Priest', 'father': 'Priest', 'rev': 'Priest',
                'reverend': 'Priest', 'msgr': 'Monsignor', 'monsignor': 'Monsignor',
                'dcn': 'Deacon', 'deacon': 'Deacon',
                'sr': 'Sister', 'sister': 'Sister',
                'br': 'Brother', 'brother': 'Brother',
                'bishop': 'Bishop', 'archbishop': 'Archbishop',
            }.get(honorific, "")
            names.append({
                "name": clean_name,
                **name_parts,
                "role": implied_role,
                "context": text[max(0, m.start()-30):m.end()+30].strip(),
                "category": "clergy_staff"
            })

    # ─────────────────────────────────────────────────────────────────────────
    # Pattern 2: Section-header roles (DEACONS, PASTORAL COUNCIL, etc.)
    # ─────────────────────────────────────────────────────────────────────────
    # Some bulletins have centered/bold section headers like:
    #   DEACONS
    #   Reynaldo Romo, Gene Tackett & Kurt Carlson
    #
    #   PASTORAL COUNCIL
    #   President    Barry Gaston
    #
    # The section header implies a role for all names that follow,
    # UNLESS those names already have their own explicit role from Pattern 1.
    # ─────────────────────────────────────────────────────────────────────────

    # Section headers that imply a role for names listed underneath
    section_headers = {
        r'\bDEACONS?\b': 'Deacon',
        r'\bPASTORAL COUNCIL\b': 'Pastoral Council Member',
        r'\bFINANCE COUNCIL\b': 'Finance Council Member',
        r'\bPARISH COUNCIL\b': 'Parish Council Member',
        r'\bBOARD OF DIRECTORS\b': 'Board of Directors Member',
        r'\bSTAFF\b': '',           # Staff section — individual roles are per-line
        r'\bPARISH STAFF\b': '',    # Same
        r'\bMINISTRY CONTACTS?\b': '',
        r'\bMINISTRIES AND CONTACTS\b': '',
    }

    for header_pattern, section_role in section_headers.items():
        for header_match in re.finditer(header_pattern, text):
            # Get the text after this header, up to the next ALL-CAPS header or 500 chars
            start = header_match.end()
            remaining = text[start:start+500]

            # Stop at the next ALL-CAPS section header (line that is mostly uppercase)
            lines = remaining.split('\n')
            section_text_lines = []
            for line in lines[1:]:  # skip the first line (might be part of header)
                stripped = line.strip()
                if not stripped:
                    continue
                # Stop at next section header: all-caps line with 2+ words
                if (stripped.isupper() and len(stripped.split()) >= 2
                        and not re.match(r'^\d', stripped)):
                    break
                section_text_lines.append(stripped)

            section_text = '\n'.join(section_text_lines)

            # Extract names from this section.
            # Names can be: comma-separated, &-separated, or one-per-line.
            # Lines that start with a known role keyword (e.g. "President Barry Gaston")
            # are handled by Pattern 1. Here we only grab bare names that DIDN'T match
            # Pattern 1 — these inherit the section header role.
            name_candidates = re.split(r'[,&\n]+', section_text)
            for candidate in name_candidates:
                candidate = candidate.strip()
                if not candidate:
                    continue
                # Skip lines that are clearly non-names
                if re.match(r'^\d', candidate):     # starts with digit
                    continue
                if len(candidate) < 5:
                    continue
                # Skip lines that start with a known role keyword
                # (those are already handled by Pattern 1 with their specific role)
                if re.match(rf'^(?:{STAFF_ROLES})\b', candidate, re.IGNORECASE):
                    continue

                # Find name patterns: optional honorific + FirstName [Middle] LastName
                for name_match in re.finditer(
                    r'(?:(?:Fr\.|Rev\.|Msgr\.|Dcn\.|Sr\.|Br\.)\s+)?'
                    r'([A-Z][a-z]{1,15}(?:\s+[A-Z]\.?)?\s+(?:De\s+La\s+)?[A-Z][a-z]{1,20}'
                    r'(?:\s+[A-Z][a-z]{1,20})?)',
                    candidate
                ):
                    name = name_match.group(0).strip()
                    name = re.sub(r'\s+', ' ', name)
                    # Remove trailing phone numbers
                    name = re.sub(r'\s*\d{3}[\-\.]\d{3,4}.*$', '', name)
                    name = re.sub(r'\s*\(?\d{3}\)?.*$', '', name)
                    name = name.strip()

                    name_parts = parse_name_parts(name)
                    clean_name = " ".join(
                        p for p in [name_parts["first_name"], name_parts["middle_name"], name_parts["last_name"]] if p
                    )
                    if clean_name and is_valid_name(clean_name) and clean_name not in seen_names:
                        seen_names.add(clean_name)
                        names.append({
                            "name": clean_name,
                            **name_parts,
                            "role": section_role,
                            "context": header_match.group(0).strip(),
                            "category": "clergy_staff"
                        })

    # ─────────────────────────────────────────────────────────────────────────
    # Pattern 3: Ministry contact listings
    # ─────────────────────────────────────────────────────────────────────────
    # Matches lines like:
    #   "Altar Servers, Aeneas Anderson 249-9820"
    #   "Eucharistic Ministers, Troy Lopes 209-678-1485"
    #   "Gift Shop, Tonné Myers 508-7873"
    #   "Lectors, Paul Angelo 803-9608"
    #
    # Pattern: MinistryRole comma/colon Name [Phone]
    # ─────────────────────────────────────────────────────────────────────────

    MINISTRY_ROLES = (
        r'Altar Servers?|Eucharistic Ministers?|Lectors?|Readers?'
        r'|Ushers?(?:/Greeters?)?|Greeters?|Sacristans?'
        r'|Gift Shop|Hospital Euch(?:aristic)?\.?\s*Ministers?'
        r'|Jail Ministry|Knights? of Columbus|Ladies\s+Guild'
        r'|Linens|Marriage Preparation|Money Counters?'
        r'|Prayer Garden|Music Ministry|Choir'
        r'|Baptism Class|Hispanic Spiritual Dir(?:ector)?'
        r'|(?:You Are )?Not Alone|St\.?\s*Vincent de Paul'
        r'|Religious Education|RCIA|Compliance Officer'
        r'|Homebound Euch(?:aristic)?\.?\s*Ministers?'
    )

    # Match: MinistryRole [comma/colon] PersonName [PhoneNumber]
    # The name part requires at least 2 capitalized words with reasonable length
    ministry_contact_pattern = re.compile(
        rf'({MINISTRY_ROLES})[,:\s]+\s*'
        rf'((?:(?:Fr\.|Rev\.|Msgr\.|Dcn\.|Sr\.|Br\.)\s+)?'
        rf'[A-Z][a-z]{{2,15}}(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]{{2,20}}(?:\s+[A-Z][a-z]{{2,20}})?)'
        rf'(?:\s+\d{{3}}[\-\.]\d{{3,4}})?',
        re.IGNORECASE
    )

    for m in ministry_contact_pattern.finditer(text):
        ministry_role = m.group(1).strip()
        name_raw = m.group(2).strip()
        name_raw = re.sub(r'\s+', ' ', name_raw)

        name_parts = parse_name_parts(name_raw)
        clean_name = " ".join(
            p for p in [name_parts["first_name"], name_parts["middle_name"], name_parts["last_name"]] if p
        )
        if clean_name and is_valid_name(clean_name) and clean_name not in seen_names:
            seen_names.add(clean_name)
            names.append({
                "name": clean_name,
                **name_parts,
                "role": ministry_role.strip(),
                "context": text[max(0, m.start()-10):m.end()+20].strip(),
                "category": "clergy_staff"
            })

    # ─────────────────────────────────────────────────────────────────────────
    # Pattern 4: Mass Intentions
    # ─────────────────────────────────────────────────────────────────────────
    # "For the repose of the soul of John Smith"
    # "Special intentions of Mary Jones"
    # "+John Smith" (deceased marker)
    # "requested by Jane Doe"
    intention_patterns = [
        r'(?:repose of (?:the soul of )?|intention[s]? of |in memory of |for the (?:healing|health|recovery) of )([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)',
        r'(?:requested by |offered by |from )([A-Z][a-z]+(?:\s+(?:&|and)\s+)?[A-Z]?[a-z]*\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)',
        r'(?:†|✝|\+)\s*([A-Z][a-z]+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)',
    ]

    for pattern in intention_patterns:
        for m in re.finditer(pattern, text):
            name = m.group(1).strip()
            name = re.sub(r'\s+', ' ', name)
            # Remove trailing conjunction fragments
            name = re.sub(r'\s+(?:and|&)\s*$', '', name)
            if is_valid_name(name) and name not in seen_names:
                seen_names.add(name)
                name_parts = parse_name_parts(name)
                names.append({
                    "name": name,
                    **name_parts,
                    "role": "",
                    "context": text[max(0, m.start()-20):m.end()+20].strip(),
                    "category": "mass_intention"
                })

    # ─────────────────────────────────────────────────────────────────────────
    # Pattern 5: Prayer / Sick Lists
    # ─────────────────────────────────────────────────────────────────────────
    # Comma-separated lists following headers like "Please pray for:"
    prayer_sections = re.finditer(
        r'(?:(?:pray(?:er)?\s*(?:list|request)?|sick\s*list|(?:those who are )?(?:sick|ill|homebound)|remember in prayer)[:\s]*)((?:[A-Z][a-z]+\s+[A-Z][a-z]+(?:\s*,\s*)?)+)',
        text, re.IGNORECASE
    )
    for section in prayer_sections:
        name_list = section.group(1)
        # Split on commas, semicolons, and newlines
        for name_candidate in re.split(r'[,;\n]+', name_list):
            name = name_candidate.strip()
            # Should be FirstName LastName format
            if re.match(r'^[A-Z][a-z]+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?$', name):
                if is_valid_name(name) and name not in seen_names:
                    seen_names.add(name)
                    name_parts = parse_name_parts(name)
                    names.append({
                        "name": name,
                        **name_parts,
                        "role": "",
                        "context": "prayer list",
                        "category": "prayer_list"
                    })

    # ─────────────────────────────────────────────────────────────────────────
    # Pattern 6: Generic contextual names (capitalized first+last near keywords)
    # ─────────────────────────────────────────────────────────────────────────
    # This is the broadest/loosest pattern. It finds "FirstName LastName"
    # patterns near ministry keywords. Confidence is MEDIUM.
    context_keywords = [
        'lector', 'reader', 'usher', 'eucharistic minister', 'altar server',
        'music director', 'choir', 'organist', 'cantor',
        'knight', 'ladies', 'guild', 'council', 'committee',
        'contact', 'volunteer', 'coordinator', 'chair',
        'baptism', 'wedding', 'funeral', 'deceased',
        'recently deceased', 'eternal rest', 'rest in peace',
        'newly registered', 'welcome',
    ]

    for kw in context_keywords:
        # Find the keyword in text and look for names nearby
        for m in re.finditer(re.escape(kw), text, re.IGNORECASE):
            nearby = text[max(0, m.start()-100):m.end()+200]
            # Find capitalized name patterns in the nearby text
            for name_match in re.finditer(
                r'([A-Z][a-z]{1,15}\s+[A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20})?)',
                nearby
            ):
                name = name_match.group(1).strip()
                if is_valid_name(name) and name not in seen_names:
                    seen_names.add(name)
                    name_parts = parse_name_parts(name)
                    names.append({
                        "name": name,
                        **name_parts,
                        "role": "",
                        "context": kw,
                        "category": "ministry_contextual"
                    })

    return names


# Common words that look like names but aren't
FALSE_POSITIVE_NAMES = {
    "Holy Spirit", "Holy Family", "Holy Cross", "Holy Rosary", "Holy Trinity",
    "Sacred Heart", "Blessed Sacrament", "Blessed Mother", "Blessed Virgin",
    "Our Lady", "Our Father", "Jesus Christ", "Holy Name", "Good Shepherd",
    "Holy Communion", "First Communion", "Daily Mass", "Sunday Mass",
    "Mass Times", "Mass Intentions", "Divine Mercy", "Eternal Rest",
    "Saint Joseph", "Saint Patrick", "Saint Mary", "Saint Peter", "Saint Paul",
    "Saint Michael", "Saint Francis", "Saint Thomas", "Saint Elizabeth",
    "Palm Sunday", "Good Friday", "Easter Sunday", "Ash Wednesday",
    "Office Hours", "Parish Office", "Faith Formation", "Religious Education",
    "Social Media", "Weekly Bulletin", "Parish Life", "Parish Council",
    "Ministry Schedule", "Altar Society", "Church Bulletin",
    "North America", "South America", "New York", "New Jersey", "New Mexico",
    "Al Smith",  # very common false positive
    "Catholic Church", "United States", "Pope Francis",
    "Dear Parishioners", "Dear Friends", "For More",
    "Please Contact", "High School", "Middle School",
    "Sign Up", "Last Week", "Next Week", "This Week",
    "Thank You", "God Bless",
    "Weekday Masses", "Daily Mass", "Sunday Mass",
    "Altar Servers", "Eucharistic Ministers", "Music Director",
    "Prayer Tree", "Table Rentals", "Holy Hour",
    "Vincent De Paul", "De Paul", "Corpus Christi",
    "Stations Cross", "Bible Study", "Choir Practice",
    "Food Bank", "Soup Kitchen", "Thrift Store",
    "Office Manager", "Business Manager", "Facilities Manager",
    "Religious Ed", "Youth Minister", "Choir Director",
    "Maintenance Director", "Athletic Director",
    "Pro Life", "Right Life",
    # Bulletin structural/calendar phrases that look like names
    "Ordinary Time", "An Invitation", "By Appointment",
    "Job Opportunity", "Assembly Mtg", "Council Mtg",
    "Degree Exemplification", "Money Counters",
    "Compliance Officer", "Mercy Chaplet",
    "Del Tiempo", "Domingo Del",
    "Consejo Matrimonial",
    "Grand Knight", "Deputy Grand",
    "Hospital Euch", "Hispanic Spiritual",
    "Tech I", "Tech II",
    # Common truncated/merged column artifacts from PDF extraction
    "Are Not", "You Are", "Are Not Alone",
    "Anderson Gift", "Business Mgr",
    "The Romo", "Of Jensen",
}


def is_valid_name(name: str) -> bool:
    """
    Check if a string looks like a real person's name.

    Validates that the string has 2-4 properly capitalized words,
    is not a known false positive, and doesn't contain common
    non-name words (church terminology, days, months, etc.).

    Also rejects truncated/fragmented names from PDF column bleed
    (e.g. "Teresa Mu", "Carlos Ze", "Kathleen Shils").
    """
    if not name or len(name) < 4:
        return False

    # Must have at least a first and last name
    parts = name.split()
    if len(parts) < 2 or len(parts) > 4:
        return False

    # Each part should be capitalized
    for part in parts:
        if not part[0].isupper():
            return False
        # Reject all-caps (except 1-2 letter abbreviations like initials)
        if part.isupper() and len(part) > 2:
            return False

    # Check against false positives blocklist
    if name in FALSE_POSITIVE_NAMES:
        return False

    # Reject truncated names: last word should be at least 3 chars
    # (catches PDF column bleed like "Teresa Mu", "Carlos Ze", "Reynaldo Ro")
    # Exception: middle initials are OK (single letter + optional period)
    if len(parts[-1]) < 3 and not re.match(r'^[A-Z]\.?$', parts[-1]):
        return False

    # Reject if first name is too short (catches "Fr Mike" without the period)
    if len(parts[0]) < 2:
        return False

    # Reject if any part is a common non-name word.
    # NOTE: Comparison is case-INSENSITIVE — we lowercase both sides.
    # All entries should be stored lowercase in this set.
    non_name_words = {
        'the', 'and', 'for', 'from', 'with', 'that', 'this', 'are', 'was',
        'will', 'has', 'have', 'had', 'been', 'being', 'their', 'there',
        'where', 'when', 'what', 'which', 'also', 'than', 'them',
        'not', 'out', 'who', 'how', 'its', 'may', 'can', 'you', 'your',
        'church', 'parish', 'school', 'center', 'hall', 'room', 'chapel',
        'sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday',
        'january', 'february', 'march', 'april', 'june', 'july', 'august',
        'september', 'october', 'november', 'december',
        'mass', 'masses', 'confession', 'communion', 'lent', 'easter', 'advent', 'christmas',
        'daily', 'weekly', 'monthly', 'annual',
        'weekday', 'weekend', 'morning', 'evening', 'night',
        'table', 'altar', 'prayer', 'music', 'director', 'ministers',
        'eucharistic', 'servers', 'rentals', 'maintenance', 'hour',
        'holy', 'blessed', 'sacred', 'saint', 'our',
        'rite', 'christian', 'catholic', 'initiation', 'adults',
        'stations', 'cross', 'rosary', 'adoration', 'benediction',
        'tree', 'garden', 'office', 'building',
        'online', 'giving', 'live', 'stream',
        'please', 'contact', 'call', 'email', 'visit',
        'registration', 'information', 'schedule', 'calendar',
        'baptism', 'confirmation', 'marriage', 'funeral', 'anointing', 'communion',
        'collection', 'offertory', 'budget', 'total',
        'choir', 'band', 'ensemble', 'group',
        'youth', 'adult', 'children', 'family', 'women', 'men',
        'deacon', 'priest', 'bishop', 'pastor', 'vicar',
        # Spanish words that appear in bilingual bulletins
        'domingo', 'tiempo', 'ordinario', 'semana', 'consejo', 'matrimonial',
        # Organizational terms that look like names
        'president', 'vice', 'chairman', 'secretary', 'treasurer', 'members',
        'lectors', 'counters', 'volunteer', 'principal', 'coordinator',
        'gift', 'shop', 'sick', 'opportunity', 'invitation',
        'tech', 'degree', 'exemplification', 'assembly', 'mtg',
        'chaplet', 'spiritual', 'dcn', 'alone',
    }
    if any(p.lower() in non_name_words for p in parts):
        return False

    # Reject if first word is a known non-name starter (case-insensitive)
    non_name_starters = {'the', 'are', 'of', 'or', 'by', 'at', 'in', 'to', 'on', 'an', 'if'}
    if parts[0].lower() in non_name_starters:
        return False

    return True


# ── Main Pipeline ─────────────────────────────────────────────────────────────

def load_churches(state_dir: Path, limit: int = 0):
    """Load church records from JSONL file."""
    jsonl_path = state_dir / "church_details.jsonl"
    if not jsonl_path.exists():
        logger.error(f"File not found: {jsonl_path}")
        return []

    churches = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                church = rec.get("church", {})
                # Get the website URL (resolved or raw)
                url = church.get("website_resolved", "") or ""
                raw_url = church.get("website", "") or ""

                # Skip if no website at all
                if not url and not raw_url:
                    continue

                # Skip facebook pages
                if url and "facebook.com" in url.lower():
                    continue

                # Skip diocese-level pages (not individual church sites)
                if url and any(x in url.lower() for x in ["diocese", "archdiocese", "/parish-finder", "/parishfinder"]):
                    continue

                slug = church.get("slug", "")
                churches.append({
                    "name": church.get("name", "Unknown"),
                    "city": church.get("city", ""),
                    "state": church.get("state", ""),
                    "slug": slug,
                    "url": url,
                    "raw_url": raw_url,
                })
            except json.JSONDecodeError:
                continue

    if limit > 0:
        churches = churches[:limit]

    return churches


def load_progress(state_dir: Path):
    """Load progress tracking file."""
    progress_path = state_dir / "bulletin_progress.json"
    if progress_path.exists():
        with open(progress_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"discovered": {}, "downloaded": {}, "extracted": {}}


def save_progress(state_dir: Path, progress: dict):
    """Save progress tracking file."""
    progress_path = state_dir / "bulletin_progress.json"
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2)


def run_discover(state_name: str, state_dir: Path, churches: list, progress: dict, resume: bool = False):
    """Phase 1: Discover bulletin pages for all churches."""
    logger.info(f"=== Phase 1: DISCOVER bulletin pages for {state_name} ({len(churches)} churches) ===")

    discovered = progress.get("discovered", {})
    found_count = 0
    skipped = 0

    for i, church in enumerate(churches):
        slug = church["slug"]

        # Skip if already discovered (resume mode)
        if resume and slug in discovered:
            skipped += 1
            continue

        url = church["url"]
        if not url:
            discovered[slug] = {"status": "no_url", "bulletin_page": None, "pdfs": []}
            continue

        logger.info(f"[{i+1}/{len(churches)}] {church['name']} ({church['city']}): {url}")

        try:
            result = find_bulletin_page(url)

            if result["pdf_urls"] or result["bulletin_page_url"]:
                found_count += 1
                discovered[slug] = {
                    "status": "found",
                    "bulletin_page": result["bulletin_page_url"],
                    "pdfs": result["pdf_urls"],
                    "source": result["source"],
                    "lpi_parish_id": result.get("lpi_parish_id"),
                    "church_name": church["name"],
                    "church_url": url,
                }
                pdf_count = len(result["pdf_urls"])
                logger.info(f"  [OK] Found bulletin page ({result['source']}), {pdf_count} PDFs")
            else:
                discovered[slug] = {
                    "status": "not_found",
                    "bulletin_page": result.get("bulletin_page_url"),
                    "pdfs": [],
                    "church_name": church["name"],
                    "church_url": url,
                }
                logger.info(f"  [--] No bulletin found")
        except Exception as e:
            discovered[slug] = {"status": "error", "error": str(e), "pdfs": []}
            logger.warning(f"  [ERR] Error: {e}")

        # Save progress periodically
        if (i + 1) % PROGRESS_SAVE_INTERVAL == 0:
            progress["discovered"] = discovered
            save_progress(state_dir, progress)
            active = i + 1 - skipped
            logger.info(f"  Progress saved. {found_count}/{active} churches have bulletins so far.")

    progress["discovered"] = discovered
    save_progress(state_dir, progress)

    # Save discovery results to a separate JSON
    discovery_path = state_dir / "bulletin_discovery.json"
    with open(discovery_path, "w", encoding="utf-8") as f:
        json.dump(discovered, f, indent=2)

    total_active = len(churches) - skipped
    logger.info(f"\n=== Discovery complete: {found_count}/{total_active} churches have bulletins ===")
    logger.info(f"Results saved to {discovery_path}")

    # Log churches with more than MAX_PDFS_PER_CHURCH PDFs (for future full collection)
    over_limit = {slug: info for slug, info in discovered.items()
                  if len(info.get("pdfs", [])) > MAX_PDFS_PER_CHURCH}
    if over_limit:
        logger.info(f"\n[!] {len(over_limit)} churches have >{MAX_PDFS_PER_CHURCH} PDFs (capped at {MAX_PDFS_PER_CHURCH}):")
        for slug, info in sorted(over_limit.items(), key=lambda x: -len(x[1].get("pdfs", []))):
            logger.info(f"  {info.get('church_name', slug)}: {len(info['pdfs'])} PDFs total")

    return discovered


def run_download(state_name: str, state_dir: Path, discovered: dict, progress: dict, resume: bool = False):
    """Phase 2: Download bulletin PDFs."""
    logger.info(f"\n=== Phase 2: DOWNLOAD bulletin PDFs for {state_name} ===")

    bulletin_dir = state_dir / "bulletins"
    bulletin_dir.mkdir(parents=True, exist_ok=True)

    downloaded = progress.get("downloaded", {})
    total_downloaded = 0
    total_pdfs = 0
    capped_churches = []  # Track churches that hit the MAX_PDFS cap

    for slug, info in discovered.items():
        if info.get("status") != "found" or not info.get("pdfs"):
            continue

        if resume and slug in downloaded:
            # Still check if this church was capped
            all_pdfs = info["pdfs"]
            if len(all_pdfs) >= MAX_PDFS_PER_CHURCH:
                capped_churches.append({
                    "slug": slug,
                    "church_name": info.get("church_name", slug),
                    "total_pdfs_available": len(all_pdfs),
                    "pdfs_downloaded": MAX_PDFS_PER_CHURCH,
                    "remaining_pdfs": len(all_pdfs) - MAX_PDFS_PER_CHURCH,
                })
            continue

        all_pdfs = info["pdfs"]
        pdfs = all_pdfs[:MAX_PDFS_PER_CHURCH]
        total_pdfs += len(pdfs)
        church_name = info.get("church_name", slug)

        # Flag if this church hit the cap
        if len(all_pdfs) >= MAX_PDFS_PER_CHURCH:
            capped_churches.append({
                "slug": slug,
                "church_name": church_name,
                "total_pdfs_available": len(all_pdfs),
                "pdfs_downloaded": len(pdfs),
                "remaining_pdfs": len(all_pdfs) - len(pdfs),
            })
            logger.info(f"  [CAP] {church_name}: {len(all_pdfs)} PDFs available, downloading {len(pdfs)} (capped at {MAX_PDFS_PER_CHURCH})")

        slug_safe = re.sub(r'[^a-zA-Z0-9_-]', '_', slug)
        church_downloads = []

        for pdf_url in pdfs:
            path = download_bulletin_pdf(pdf_url, bulletin_dir, slug_safe)
            if path:
                church_downloads.append({
                    "url": pdf_url,
                    "local_path": str(path),
                    "size_bytes": path.stat().st_size,
                })
                total_downloaded += 1

        downloaded[slug] = {
            "church_name": church_name,
            "files": church_downloads,
            "capped": len(all_pdfs) >= MAX_PDFS_PER_CHURCH,
            "total_available": len(all_pdfs),
        }

        if church_downloads:
            logger.info(f"  Downloaded {len(church_downloads)} PDFs for {church_name}")

    progress["downloaded"] = downloaded
    save_progress(state_dir, progress)

    # Save capped churches list for future reference
    if capped_churches:
        capped_path = state_dir / "capped_churches.json"
        with open(capped_path, "w", encoding="utf-8") as f:
            json.dump(capped_churches, f, indent=2)
        logger.info(f"\n[!] {len(capped_churches)} churches hit the {MAX_PDFS_PER_CHURCH}-PDF cap:")
        for c in sorted(capped_churches, key=lambda x: -x["total_pdfs_available"]):
            logger.info(f"  {c['church_name']}: {c['total_pdfs_available']} available, {c['remaining_pdfs']} remaining")
        logger.info(f"Capped churches saved to {capped_path}")

    logger.info(f"\n=== Download complete: {total_downloaded}/{total_pdfs} PDFs downloaded ===")
    logger.info(f"Saved to {bulletin_dir}")

    return downloaded


def run_extract(state_name: str, state_dir: Path, downloaded: dict, progress: dict):
    """Phase 3: Extract text from PDFs and identify names."""
    logger.info(f"\n=== Phase 3: EXTRACT text and names for {state_name} ===")

    if not HAS_PDFPLUMBER:
        logger.error("pdfplumber is required for extraction. Install with: pip install pdfplumber")
        return

    text_dir = state_dir / "bulletin_texts"
    text_dir.mkdir(parents=True, exist_ok=True)

    all_names = []
    total_names = 0
    churches_with_names = 0

    # Build lookup for church URLs from discovery data
    discovery_path = state_dir / "bulletin_discovery.json"
    discovery_data = {}
    if discovery_path.exists():
        with open(discovery_path, "r", encoding="utf-8") as f:
            discovery_data = json.load(f)

    # Confidence mapping: based on whether this is likely a real person connected to the church
    # HIGH = clearly a named individual (staff, parishioner mentioned by name, someone prayed for)
    # MEDIUM = likely a real name but found via looser contextual matching
    # LOW = might be a false positive (generic text near a ministry keyword)
    CONFIDENCE_MAP = {
        "clergy_staff": "high",       # Named staff: Fr. John Smith, Pastor: Jane Doe
        "mass_intention": "high",     # Named in Mass intentions: real person (living or deceased)
        "prayer_list": "high",        # Named on prayer/sick list: real parishioner
        "ministry_contextual": "medium",  # Name found near a ministry keyword: likely real but looser match
    }

    # Track unique names PER CHURCH (not statewide) — each church has its own seen-names set
    # This way a name appearing at 5 different churches shows up 5 times (once per church)
    church_seen_names = {}  # slug -> set of names already output for this church

    for slug, info in downloaded.items():
        files = info.get("files", [])
        if not files:
            continue

        church_name = info.get("church_name", slug)
        church_url = discovery_data.get(slug, {}).get("church_url", "")

        # Initialize per-church dedup set
        if slug not in church_seen_names:
            church_seen_names[slug] = set()

        church_name_count = 0

        for file_info in files:
            pdf_path = Path(file_info["local_path"])
            if not pdf_path.exists():
                continue

            pdf_url = file_info.get("url", "")

            # Try to extract a date from the PDF filename/URL for provenance
            pdf_date = ""
            date_score = extract_date_score(pdf_url, pdf_path.name)
            if date_score > 0:
                ds = str(date_score)
                if len(ds) == 8:
                    pdf_date = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"

            # Extract text
            text = extract_text_from_pdf(pdf_path)
            if not text:
                logger.debug(f"  No text extracted from {pdf_path.name}")
                continue

            # Save extracted text
            text_path = text_dir / (pdf_path.stem + ".txt")
            text_path.write_text(text, encoding="utf-8")

            # Extract names
            names = extract_names_from_text(text, church_name)

            if names:
                for name_info in names:
                    person_name = name_info["name"]

                    # Per-church dedup: only output each name once per church
                    # but keep the FIRST occurrence (has the best provenance — earliest PDF)
                    name_key = person_name.lower().strip()
                    if name_key in church_seen_names[slug]:
                        continue
                    church_seen_names[slug].add(name_key)

                    church_name_count += 1
                    total_names += 1
                    all_names.append({
                        "church_name": church_name,
                        "church_slug": slug,
                        "church_url": church_url,
                        "pdf_file": pdf_path.name,
                        "pdf_url": pdf_url,
                        "pdf_date": pdf_date,
                        "person_name": person_name,
                        "title": name_info.get("title", ""),
                        "first_name": name_info.get("first_name", ""),
                        "middle_name": name_info.get("middle_name", ""),
                        "last_name": name_info.get("last_name", ""),
                        "role": name_info.get("role", ""),
                        "category": name_info["category"],
                        "confidence": CONFIDENCE_MAP.get(name_info["category"], "low"),
                        "context": name_info["context"][:100],
                    })

        if church_name_count > 0:
            churches_with_names += 1
            logger.info(f"  {church_name}: {church_name_count} unique names found")

    # Save names to CSV
    csv_path = state_dir / "bulletin_names.csv"
    if all_names:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "church_name", "church_slug", "church_url",
                "pdf_file", "pdf_url", "pdf_date",
                "person_name", "title", "first_name", "middle_name", "last_name",
                "role", "category", "confidence", "context"
            ])
            writer.writeheader()
            writer.writerows(all_names)

    # Also save as JSON for easier programmatic access
    json_path = state_dir / "bulletin_names.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_names, f, indent=2)

    logger.info(f"\n=== Extraction complete ===")
    logger.info(f"  {total_names} unique names (per-church) from {churches_with_names} churches")
    logger.info(f"  CSV: {csv_path}")
    logger.info(f"  JSON: {json_path}")

    progress["extracted"] = {
        "total_names": total_names,
        "churches_with_names": churches_with_names,
        "csv_path": str(csv_path),
    }
    save_progress(state_dir, progress)


# ── CLI Entry Point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Scrape church bulletins, download PDFs, and extract names"
    )
    parser.add_argument("phase", choices=["discover", "download", "extract", "all"],
                       help="Which phase to run")
    parser.add_argument("states", nargs="+",
                       help="State names or 'all' for all states")
    parser.add_argument("--limit", type=int, default=0,
                       help="Limit number of churches to process (for testing)")
    parser.add_argument("--resume", action="store_true",
                       help="Resume from where we left off")

    args = parser.parse_args()

    # Resolve state names
    if "all" in args.states:
        state_dirs = sorted([
            d for d in OUTPUT_DIR.iterdir()
            if d.is_dir() and (d / "church_details.jsonl").exists()
        ])
    else:
        state_dirs = []
        for name in args.states:
            resolved = resolve_state(name)
            if not resolved:
                logger.error(f"Unknown state: {name}")
                continue
            state_dir = OUTPUT_DIR / resolved[1]
            if not state_dir.exists():
                logger.error(f"No data directory for {name}: {state_dir}")
                continue
            state_dirs.append(state_dir)

    if not state_dirs:
        logger.error("No valid states to process")
        return

    for state_dir in state_dirs:
        state_name = state_dir.name
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing {state_name.upper()}")
        logger.info(f"{'='*60}")

        # Load churches
        churches = load_churches(state_dir, limit=args.limit)
        if not churches:
            logger.warning(f"No churches with websites found for {state_name}")
            continue

        # Filter to only churches with resolved URLs
        with_urls = [c for c in churches if c["url"]]
        logger.info(f"Loaded {len(churches)} churches, {len(with_urls)} with resolved URLs")

        # Load progress
        progress = load_progress(state_dir)

        if args.phase in ("discover", "all"):
            discovered = run_discover(state_name, state_dir, with_urls, progress, resume=args.resume)
        else:
            # Load existing discovery data
            discovery_path = state_dir / "bulletin_discovery.json"
            if discovery_path.exists():
                with open(discovery_path, "r", encoding="utf-8") as f:
                    discovered = json.load(f)
            else:
                logger.error(f"No discovery data found. Run 'discover' phase first.")
                continue

        if args.phase in ("download", "all"):
            downloaded = run_download(state_name, state_dir, discovered, progress, resume=args.resume)
        else:
            downloaded = progress.get("downloaded", {})

        if args.phase in ("extract", "all"):
            run_extract(state_name, state_dir, downloaded, progress)

    logger.info("\n=== ALL DONE ===")


if __name__ == "__main__":
    main()
