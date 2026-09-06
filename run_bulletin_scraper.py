"""
run_bulletin_scraper.py

Scrape church bulletins (PDFs) from parish websites, extract text, and pull out
all people's names mentioned in them.

PIPELINE:
  Phase 1 — Discover: Find bulletin pages on church websites
  Phase 2 — Download: Download bulletin PDFs
  Phase 3 — Extract: Extract text from PDFs and identify names via pattern matching
  Phase 4 — Clean: Re-clean existing extracted names with updated validation rules

HOW TO RUN:
    python run_bulletin_scraper.py discover arizona             # Phase 1: find bulletin pages
    python run_bulletin_scraper.py download arizona             # Phase 2: download PDFs
    python run_bulletin_scraper.py extract arizona              # Phase 3: extract text + names
    python run_bulletin_scraper.py clean arizona                # Phase 4: re-clean names with updated rules
    python run_bulletin_scraper.py all arizona                  # Run phases 1-3
    python run_bulletin_scraper.py all arizona georgia          # Multiple states
    python run_bulletin_scraper.py all arizona --limit 10       # Test with first 10 churches
    python run_bulletin_scraper.py all arizona --resume         # Resume interrupted run
    python run_bulletin_scraper.py all arizona --retry-no-url   # Re-check churches that got new URLs
    python run_bulletin_scraper.py all arizona --retry-no-pdfs  # Re-try churches with 0 PDFs (uses Playwright)

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

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    import pdfplumber

    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False
    print("WARNING: pdfplumber not installed. PDF extraction will be skipped.")
    print("Install with: pip install pdfplumber")

try:
    from playwright.sync_api import sync_playwright

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

try:
    from src.parsers.bulletin_constants import (
        FALSE_POSITIVE_NAMES as _SHARED_FP_NAMES,
    )

    # Imported but not referenced: this whole block is an AVAILABILITY probe that
    # sets _HAS_SHARED_CONSTANTS, and dropping the name would stop it noticing
    # if HONORIFIC_TITLES ever disappeared from bulletin_constants.
    from src.parsers.bulletin_constants import (
        HONORIFIC_TITLES as _SHARED_HONORIFICS,  # noqa: F401
    )
    from src.parsers.bulletin_constants import (
        MINISTRY_ROLES as _SHARED_MINISTRY_ROLES,
    )
    from src.parsers.bulletin_constants import (
        STAFF_ROLES as _SHARED_STAFF_ROLES,
    )

    _HAS_SHARED_CONSTANTS = True
except ImportError:
    _HAS_SHARED_CONSTANTS = False

try:
    from nameparser import HumanName
    from nameparser.config import CONSTANTS as _NP_CONSTANTS

    _NP_CONSTANTS.titles.add("fr", "dcn", "msgr", "sr", "br", "rev")
    HAS_NAMEPARSER = True
except ImportError:
    HAS_NAMEPARSER = False

try:
    import probablepeople as pp

    HAS_PROBABLEPEOPLE = True
except ImportError:
    HAS_PROBABLEPEOPLE = False

# NER veto gate — lazy-loaded spaCy model.
# A spaCy Language object is not safe to call from several threads at once, and
# the lazy load itself would race, so both are serialised on _ner_lock. Callers
# that run a pool should warm this up first (see prewarm_shared_state).
_ner_nlp = None
_ner_tried = False
_ner_lock = threading.RLock()
_browser_lock = threading.RLock()


def prewarm_shared_state():
    """Load every lazily-cached global before any worker threads start.

    Each of these caches is populated on first use behind a plain `if is None`
    check. That is fine single-threaded, but with a pool several workers hit it
    at once and duplicate the (slow) load. Warming up front makes the pool's
    first moments deterministic.
    """
    _get_ner_nlp()
    try:
        _load_reference_data()
    except Exception as e:  # reference data is optional for scoring
        logger.debug(f"prewarm: reference data unavailable: {e}")
    _get_non_name_words()


def _get_ner_nlp():
    """Lazy-load spaCy model for NER veto gate."""
    global _ner_nlp, _ner_tried
    with _ner_lock:
        return _get_ner_nlp_locked()


def _get_ner_nlp_locked():
    global _ner_nlp, _ner_tried
    if _ner_tried:
        return _ner_nlp
    _ner_tried = True
    try:
        import spacy

        # en_core_web_lg is ~800MB resident. On the 2GB cron instance that alone
        # crowds out several PDF-parsing workers and OOMs the job, so allow the
        # small model to be forced with NER_MODEL=en_core_web_sm.
        preferred = os.environ.get("NER_MODEL", "en_core_web_lg")
        try:
            _ner_nlp = spacy.load(preferred)
        except OSError:
            _ner_nlp = spacy.load("en_core_web_sm")
    except Exception:
        _ner_nlp = None
    return _ner_nlp


def ner_veto(name, context=""):
    """Returns True if NER confirms this looks like a person name."""
    nlp = _get_ner_nlp()
    if nlp is None:
        return True  # Pass through if NER unavailable
    text = context if context else name
    with _ner_lock:
        doc = nlp(text)
    name_lower = name.lower().strip()
    for ent in doc.ents:
        if ent.label_ == "PERSON":
            if name_lower in ent.text.lower() or ent.text.lower() in name_lower:
                return True
    return False


def ner_veto_batch(names, contexts=None):
    """Batch NER veto — returns list of bools (True = confirmed person)."""
    nlp = _get_ner_nlp()
    if nlp is None:
        return [True] * len(names)
    contexts = contexts or [""] * len(names)
    texts = [c if c else n for c, n in zip(contexts, names)]
    results = []
    # nlp.pipe() mutates shared model state; one thread through it at a time.
    with _ner_lock:
        docs = list(nlp.pipe(texts, batch_size=50))
    for name, doc in zip(names, docs):
        name_lower = name.lower().strip()
        is_person = False
        for ent in doc.ents:
            if ent.label_ == "PERSON":
                ent_lower = ent.text.lower().strip()
                # Require full-name match (not substring) to reduce
                # false confirmations like "John" in "John 3:16"
                if name_lower == ent_lower or ent_lower == name_lower:
                    is_person = True
                    break
        results.append(is_person)
    return results


def detect_couple(name):
    """Split couple names into two individuals using probablepeople.

    Returns list of (name, split_type) tuples.
    """
    if not HAS_PROBABLEPEOPLE:
        return [(name, "individual")]
    try:
        parsed, name_type = pp.tag(name)
        if name_type == "Household":
            first = parsed.get("GivenName", "")
            second = parsed.get("SecondGivenName", "")
            surname = parsed.get("Surname", "")
            if first and second and surname:
                return [
                    (f"{first} {surname}", "couple_split"),
                    (f"{second} {surname}", "couple_split"),
                ]
    except Exception:
        pass
    return [(name, "individual")]


sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import OUTPUT_DIR  # noqa: E402
from src.utils import host_policy as _host_policy  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

REQUEST_DELAY = 1.0  # seconds between requests to same domain
REQUEST_TIMEOUT = 15  # seconds
MAX_PDFS_PER_CHURCH = 100  # download all available bulletins (most churches have 20-52 weeks)
_pdf_cap_override = None  # Set to 0 for unlimited; None = use MAX_PDFS_PER_CHURCH
MAX_PDF_SIZE_MB = 25  # skip PDFs larger than 25MB
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

# Optional rotating residential proxy (shared PROXY_URL convention). When set,
# all HTTP + headless-browser requests route through it; else they go direct.
PROXY_URL = os.environ.get("PROXY_URL", "").strip() or None
PROXIES = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None


def _playwright_proxy():
    """Parse PROXY_URL into Playwright's launch-proxy dict, or None if unset."""
    if not PROXY_URL:
        return None
    parsed = urlparse(PROXY_URL)
    server = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        server += f":{parsed.port}"
    proxy = {"server": server}
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy


# Strategy 5 (walled hosts, browser through the proxy) is OFF unless asked for.
# Enabling it by default cost the national sweep three shards to OOM in one
# round: every blocked host tried to launch Chromium alongside spaCy and
# pdfplumber on a 2GB box.
WALLED_BROWSER_ENABLED = os.environ.get("WALLED_BROWSER", "0") == "1"
# Hard ceiling on browser launches per process, so a shard full of blocked
# hosts cannot spawn them without limit.
WALLED_BROWSER_BUDGET = int(os.environ.get("WALLED_BROWSER_BUDGET", "40"))
_walled_spent = 0
_walled_budget_lock = threading.Lock()


def _walled_budget_left():
    with _walled_budget_lock:
        return _walled_spent < WALLED_BROWSER_BUDGET


def _walled_budget_spend():
    """Claim one browser launch. False when the budget is exhausted."""
    global _walled_spent
    with _walled_budget_lock:
        if _walled_spent >= WALLED_BROWSER_BUDGET:
            return False
        _walled_spent += 1
        return True


def _effective_pdf_cap():
    """Return the active per-church PDF cap. 0 means unlimited."""
    if _pdf_cap_override is not None:
        return _pdf_cap_override
    return MAX_PDFS_PER_CHURCH


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
    # Newsletter variants (some churches call them newsletters)
    "/newsletter",
    "/newsletters",
    "/newsletter/",
    "/newsletters/",
    "/weekly-newsletter",
    "/parish-newsletter",
    "/news-events/bulletin",
    "/news/bulletin",
    # Protestant vocabulary. The corpus was Catholic-only until ELCA landed, and
    # Lutheran, Methodist and UCC congregations mostly do not use the word
    # "bulletin" for the thing that is a bulletin -- it is a worship folder, an
    # order of worship, or just announcements. A sampled 64% of reachable ELCA
    # sites link one from the homepage, so this vocabulary is the difference
    # between reaching them and recording another 'not_found'.
    #
    # APPENDED, never inserted: BULLETIN_PATHS[2] and BULLETIN_PATHS[3:] are
    # referenced by index further down this file, so anything added at the front
    # silently changes which paths those two call sites mean.
    "/worship-folder",
    "/worship-folders",
    "/order-of-worship",
    "/order-of-service",
    "/worship-bulletin",
    "/worship-bulletins",
    "/service-bulletin",
    "/announcements",
    "/weekly-announcements",
    "/worship/bulletins",
    "/worship/worship-bulletins",
    "/media/bulletins",
    "/resources/bulletins",
]

# Keywords that indicate a bulletin page (case-insensitive)
BULLETIN_PAGE_KEYWORDS = [
    "bulletin",
    "weekly bulletin",
    "parish bulletin",
    "sunday bulletin",
    "church bulletin",
    "current bulletin",
    "this week",
    "latest bulletin",
    "download bulletin",
    # Newsletter variants
    "newsletter",
    "weekly newsletter",
    "parish newsletter",
    "current newsletter",
    "download newsletter",
    # Protestant equivalents — see the note on BULLETIN_PATHS above.
    "worship folder",
    "order of worship",
    "order of service",
    "worship bulletin",
    "service bulletin",
    "weekly update",
    "announcements",
    "weekly announcements",
]

# Patterns to find bulletin links on church homepages
BULLETIN_LINK_PATTERNS = [
    re.compile(r"bulletin", re.IGNORECASE),
    re.compile(r"newsletter", re.IGNORECASE),
    re.compile(r"weekly\s*(news|update|publication)", re.IGNORECASE),
    re.compile(r"worship\s*folder", re.IGNORECASE),
    re.compile(r"order\s*of\s*(worship|service)", re.IGNORECASE),
    re.compile(r"announcements", re.IGNORECASE),
]

# State aliases (same as run_resolve_urls.py)
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


def resolve_state(name: str):
    key = name.lower().replace(" ", "_").replace("-", "_")
    return STATE_ALIASES.get(key)


# ── HTTP Helpers ───────────────────────────────────────────────────────────────

_last_request_time = 0.0

# Rate limiting is PER HOST, not global. Politeness is owed to each parish
# server; two different parishes share nothing, so making them wait on each
# other only throttles us. A single global clock capped the whole process at
# 1/REQUEST_DELAY requests per second no matter how many workers were running.
_domain_last_request = {}
_rate_lock = threading.Lock()

# requests.Session is not documented as thread-safe, so each worker gets one.
_thread_local = threading.local()


def _get_session():
    """The calling thread's HTTP session.

    Deliberately NOT proxied at the session level. A session-wide proxy applies
    to every host the thread touches, which is the "set PROXY_URL and tunnel the
    whole estate" failure this project is trying to avoid. The proxy is chosen
    per request instead, from the per-host policy table.
    """
    sess = getattr(_thread_local, "session", None)
    if sess is None:
        sess = requests.Session()
        sess.headers.update(HEADERS)
        _thread_local.session = sess
    return sess


def _throttle(url: str):
    """Block until this URL's host is allowed another request."""
    host = urlparse(url).netloc.lower()
    while True:
        with _rate_lock:
            now = time.time()
            ready_at = _domain_last_request.get(host, 0.0) + REQUEST_DELAY
            if now >= ready_at:
                # Claim the slot while still holding the lock, so two threads
                # cannot both decide they are clear for the same host.
                _domain_last_request[host] = now
                return
            wait = ready_at - now
        time.sleep(wait)


# Kept as a module-level alias so existing single-threaded callers behave the
# same; the session is per-thread underneath.
class _SessionProxy:
    def __getattr__(self, name):
        return getattr(_get_session(), name)


_session = _SessionProxy()


def _rate_limited_get(url: str, timeout: int = REQUEST_TIMEOUT, allow_redirects=True):
    """Make a per-host rate-limited GET request. Returns Response or None.

    The proxy (if any) is selected per host, so an unlabelled host goes direct.
    """
    _throttle(url)
    try:
        resp = _get_session().get(
            url,
            timeout=timeout,
            allow_redirects=allow_redirects,
            proxies=_host_policy.proxies_for(url),
        )
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


# ── Playwright Headless Browser ────────────────────────────────────────────────

BROWSER_TIMEOUT = 20000  # 20s for JS-rendered pages (vs 15s for HTTP)
_playwright_instance = None
_browser_instance = None


def _get_playwright_browser():
    """Lazy-init a single Playwright Chromium browser instance (reused across churches)."""
    global _playwright_instance, _browser_instance
    if not HAS_PLAYWRIGHT:
        return None
    if _browser_instance is None:
        _playwright_instance = sync_playwright().start()
        launch_kwargs = {"headless": True}
        proxy = _playwright_proxy()
        if proxy:
            launch_kwargs["proxy"] = proxy
        _browser_instance = _playwright_instance.chromium.launch(**launch_kwargs)
    return _browser_instance


def _close_playwright_browser():
    """Clean up Playwright browser at shutdown."""
    global _playwright_instance, _browser_instance
    if _browser_instance:
        try:
            _browser_instance.close()
        except Exception:
            pass
        _browser_instance = None
    if _playwright_instance:
        try:
            _playwright_instance.stop()
        except Exception:
            pass
        _playwright_instance = None


def _extract_pdfs_with_browser(url: str):
    """
    Load a URL in headless Chromium, wait for JS to render, extract all PDF links.

    Returns a list of absolute PDF URLs found in the rendered DOM.

    The browser is created AND destroyed inside the lock, on the calling thread.
    A lock alone is not enough: sync_playwright() binds its greenlet to whichever
    thread started it, so a cached browser reused by a second worker dies with
    "greenlet.error: Cannot switch to a different thread". That stayed hidden
    while the image had no browser binary and every call failed instantly —
    installing chromium turned a fast no-op into a real, thread-unsafe code path
    and OOM-killed two shards on 2026-08-15.

    Building it per call costs ~1s, which is fine: this is a fallback for
    JS-rendered parish sites (LPi widgets, eCatholic), not the common path. It
    also caps memory, since a long-lived Chromium alongside spaCy and pdfplumber
    is what pushed a 4-worker shard past 2GB.
    """
    if not HAS_PLAYWRIGHT:
        return []

    with _browser_lock:
        pw = browser = None
        try:
            pw = sync_playwright().start()
            launch_kwargs = {"headless": True}
            proxy = _playwright_proxy()
            if proxy:
                launch_kwargs["proxy"] = proxy
            browser = pw.chromium.launch(**launch_kwargs)
            return _extract_pdfs_with_browser_locked(url, browser)
        except Exception as e:
            logger.debug(f"Browser extraction unavailable for {url}: {e}")
            return []
        finally:
            for closer in (getattr(browser, "close", None), getattr(pw, "stop", None)):
                if closer:
                    try:
                        closer()
                    except Exception:
                        pass


def _extract_pdfs_with_browser_locked(url: str, browser=None):
    if browser is None:
        browser = _get_playwright_browser()
    if not browser:
        return []

    # Rate limit browser requests the same as HTTP
    _throttle(url)

    pdfs = []
    page = None
    try:
        page = browser.new_page(user_agent=USER_AGENT)
        page.set_default_timeout(BROWSER_TIMEOUT)
        page.goto(url, wait_until="networkidle", timeout=BROWSER_TIMEOUT)

        # Wait a bit for any lazy-loaded content
        page.wait_for_timeout(2000)

        # Extract all links from rendered DOM
        links = page.eval_on_selector_all(
            "a[href]", "elements => elements.map(e => ({href: e.href, text: e.textContent || ''}))"
        )

        seen = set()
        for link in links:
            href = link.get("href", "")
            if not href:
                continue
            # Direct PDF links
            if href.lower().endswith(".pdf") or ".pdf?" in href.lower():
                if href not in seen:
                    seen.add(href)
                    pdfs.append(href)
            # LPi publication-page links with selectedPublication=<pdf_url>
            elif "selectedPublication=" in href:
                parsed_link = urlparse(href)
                link_params = parse_qs(parsed_link.query)
                if "selectedPublication" in link_params:
                    pdf_url = link_params["selectedPublication"][0]
                    if pdf_url not in seen:
                        seen.add(pdf_url)
                        pdfs.append(pdf_url)
            # DiscoverMass download links
            elif "discovermass.com/download.php" in href:
                if href not in seen:
                    seen.add(href)
                    pdfs.append(href)

        # Also check for iframes that might contain PDFs (Google Docs viewer, etc.)
        iframes = page.eval_on_selector_all("iframe[src]", "elements => elements.map(e => e.src)")
        for iframe_src in iframes:
            # Google Docs Viewer: docs.google.com/gview?url=<pdf_url>
            if "docs.google.com/gview" in iframe_src:
                parsed = urlparse(iframe_src)
                params = parse_qs(parsed.query)
                if "url" in params:
                    pdf_url = params["url"][0]
                    if pdf_url not in seen:
                        seen.add(pdf_url)
                        pdfs.append(pdf_url)
            # Google Drive viewer
            elif "drive.google.com/file" in iframe_src:
                drive_m = re.search(r"/file/d/([^/]+)", iframe_src)
                if drive_m:
                    dl_url = f"https://drive.google.com/uc?export=download&id={drive_m.group(1)}"
                    if dl_url not in seen:
                        seen.add(dl_url)
                        pdfs.append(dl_url)

        logger.debug(f"  Browser extracted {len(pdfs)} PDFs from {url}")

    except Exception as e:
        logger.debug(f"  Browser failed for {url}: {e}")
    finally:
        if page:
            try:
                page.close()
            except Exception:
                pass

    cap = _effective_pdf_cap()
    return pdfs[:cap] if cap else pdfs


# ── Phase 1: Bulletin Discovery ───────────────────────────────────────────────


def find_bulletin_page(base_url: str):
    """
    Try to find the bulletin page for a church website.

    Strategy:
    1. Check common paths (/bulletin, /bulletins, /newsletter, etc.)
       1b. WordPress blog-style: follow sub-page links one level deeper
       1c. LPi widget: extract PDFs from parishesonline.com widget API
    2. Fetch homepage and search for bulletin/newsletter links in nav/content
       2b. Also check for direct PDF links with "newsletter" text
    3. Check for LPi/ParishesOnline embed on homepage
    4. Check remaining direct paths (extended list)

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

    # Strategy 1: Try common bulletin/newsletter paths
    for path in BULLETIN_PATHS:
        try_url = base_origin + path
        resp = _rate_limited_get(try_url)
        if resp and resp.status_code == 200:
            text_lower = resp.text.lower()
            # Verify this actually looks like a bulletin/newsletter page
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
                if result["pdf_urls"]:
                    return result

                # Strategy 1c: LPi widget — try extracting PDFs via widget API
                if lpi_id:
                    widget_pdfs = extract_lpi_pdfs_any(lpi_id)
                    if widget_pdfs:
                        result["pdf_urls"] = widget_pdfs
                        result["source"] = "direct_path_lpi_widget"
                        return result
                    # An LPi id that yields no PDFs is NOT a result. It is often a
                    # bare org slug (find_lpi_parish_id pattern 3) that the widget
                    # API cannot resolve. Returning here used to abandon the church
                    # with 0 PDFs and skip strategies 1b/1d/2/3/4 entirely.

                # Strategy 1b: WordPress blog-style archive — no direct PDFs,
                # but sub-page links (e.g. /bulletins/first-sunday-of-lent/)
                # may each contain a PDF download link one level deeper
                wp_pdfs = extract_pdfs_from_subpages(soup, resp.url)
                if wp_pdfs:
                    result["pdf_urls"] = wp_pdfs
                    result["source"] = "direct_path_wordpress"
                    return result

                # Strategy 1d: eCatholic / JS-heavy page — use Playwright
                if HAS_PLAYWRIGHT and ("ecatholic" in text_lower or "myparish" in text_lower):
                    browser_pdfs = _extract_pdfs_with_browser(resp.url)
                    if browser_pdfs:
                        result["pdf_urls"] = browser_pdfs
                        result["source"] = "direct_path_browser"
                        return result
        # After trying first 3 paths without success, move to homepage scan
        if path == BULLETIN_PATHS[2] and not result["bulletin_page_url"]:
            break

    # Strategy 2: Fetch homepage and look for bulletin/newsletter links
    soup = safe_get_html(base_url)
    if soup:
        # Following every "bulletin"-ish link with a headless browser is the most
        # expensive thing this function can do (a browser is built per call), and
        # now that we no longer return on the first candidate a link-heavy nav
        # could trigger it dozens of times. Spend it on the first few only.
        browser_attempts = 0
        MAX_BROWSER_ATTEMPTS = 2
        candidate_links = 0
        MAX_CANDIDATE_LINKS = 8
        # Find links with "bulletin" or "newsletter" in text or href
        for link in soup.find_all("a", href=True):
            link_text = (link.get_text() or "").strip().lower()
            link_href = link["href"].lower()
            if any(kw in link_text or kw in link_href for kw in ["bulletin", "newsletter"]):
                full_url = urljoin(base_url, link["href"])
                # Skip if it's just the same page
                if full_url.rstrip("/") == base_url.rstrip("/"):
                    continue
                candidate_links += 1
                if candidate_links > MAX_CANDIDATE_LINKS:
                    break
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
                    lpi_pdfs = extract_lpi_pdfs(full_url)
                    if not lpi_pdfs:
                        # Scraping the LPi page yields nothing when the reader is
                        # client-side. Pull the handle out of the URL itself and
                        # go at the JSON API instead of giving up.
                        handle = find_lpi_parish_id(None, full_url)
                        if handle:
                            result["lpi_parish_id"] = handle
                            lpi_pdfs = extract_lpi_pdfs_any(handle)
                    if lpi_pdfs:
                        result["pdf_urls"] = lpi_pdfs
                        return result
                # Check if it links to discovermass.com
                if "discovermass.com" in full_url:
                    result["bulletin_page_url"] = full_url
                    result["source"] = "discovermass_link"
                    # Extract bulletin PDFs from DiscoverMass page
                    dm_pdfs = extract_discovermass_pdfs(full_url)
                    if dm_pdfs:
                        result["pdf_urls"] = dm_pdfs
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
                    # If we found the page but no PDFs, try LPi widget
                    if not result["pdf_urls"] and lpi_id:
                        widget_pdfs = extract_lpi_pdfs_any(lpi_id)
                        if widget_pdfs:
                            result["pdf_urls"] = widget_pdfs
                            result["source"] = "homepage_link_lpi_widget"
                    # If still no PDFs, try WordPress subpages
                    if not result["pdf_urls"]:
                        wp_pdfs = extract_pdfs_from_subpages(bulletin_soup, full_url)
                        if wp_pdfs:
                            result["pdf_urls"] = wp_pdfs
                            result["source"] = "homepage_link_wordpress"
                    # If still no PDFs, try Playwright for JS-heavy pages
                    if (
                        not result["pdf_urls"]
                        and HAS_PLAYWRIGHT
                        and browser_attempts < MAX_BROWSER_ATTEMPTS
                    ):
                        page_html = str(bulletin_soup).lower()
                        if (
                            "ecatholic" in page_html
                            or "myparish" in page_html
                            or not result["lpi_parish_id"]
                        ):
                            browser_attempts += 1
                            browser_pdfs = _extract_pdfs_with_browser(full_url)
                            if browser_pdfs:
                                result["pdf_urls"] = browser_pdfs
                                result["source"] = "homepage_link_browser"
                    if result["pdf_urls"]:
                        return result
                    # No PDFs behind this link. Keep the page as a best-effort
                    # breadcrumb but keep looking: the first link matching
                    # "bulletin"/"newsletter" is frequently a submission form, a
                    # staff login or an advertiser page. Returning here used to
                    # strand the church with 0 PDFs and skip strategies 3/3b/4.

        # Strategy 3: Check for LPi embed on homepage
        lpi_id = find_lpi_parish_id(soup, str(soup))
        if lpi_id:
            result["lpi_parish_id"] = lpi_id
            result["source"] = "lpi_embed_homepage"
            # Try to get PDFs from the widget, then from the organization page
            widget_pdfs = extract_lpi_pdfs_any(lpi_id)
            if widget_pdfs:
                result["pdf_urls"] = widget_pdfs
                return result
            # Otherwise keep going — 3b and 4 below still have a real chance.

        # Strategy 3b: Check for direct PDF links on homepage with
        # bulletin/newsletter in the link text (catches "Open Latest Newsletter (PDF)")
        homepage_pdfs = extract_pdf_links(soup, base_url)
        if homepage_pdfs:
            # Only keep PDFs whose link text mentions bulletin/newsletter
            for link in soup.find_all("a", href=True):
                href = link["href"]
                text = (link.get_text() or "").lower()
                if href.lower().endswith(".pdf") or ".pdf?" in href.lower():
                    if any(kw in text for kw in ["bulletin", "newsletter"]):
                        full_url = urljoin(base_url, href)
                        result["pdf_urls"].append(full_url)
            if result["pdf_urls"]:
                result["bulletin_page_url"] = base_url
                result["source"] = "homepage_pdf_newsletter"
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
                    # Check for LPi on extended paths too
                    lpi_id = find_lpi_parish_id(page_soup, resp.text)
                    if lpi_id:
                        result["lpi_parish_id"] = lpi_id
                        result["bulletin_page_url"] = resp.url
                        result["source"] = "direct_path_extended_lpi"
                        widget_pdfs = extract_lpi_pdfs_any(lpi_id)
                        if widget_pdfs:
                            result["pdf_urls"] = widget_pdfs
                        return result
                    # WordPress subpages on extended paths
                    wp_pdfs = extract_pdfs_from_subpages(page_soup, resp.url)
                    if wp_pdfs:
                        result["bulletin_page_url"] = resp.url
                        result["source"] = "direct_path_extended_wordpress"
                        result["pdf_urls"] = wp_pdfs
                        return result

    # Strategy 5: the host is WALLED — go at it with a browser THROUGH the proxy.
    #
    # Measured 2026-08-17 across two states — five Maine hosts and three
    # Wisconsin ones — every combination:
    #
    #     requests          403
    #     requests + proxy  403
    #     browser           403
    #     browser + proxy   200   <- 8 of 8, with real content
    #
    # The WAF scores both signals and wants both. Neither a residential address
    # nor a real browser passes alone, which is why every earlier probe recorded
    # these hosts as 'blocked' — and 'blocked' means go direct WITHOUT a proxy,
    # i.e. precisely the one combination that cannot work. 5,457 hosts carry
    # that verdict, gating a large share of the 14,997 churches that have a
    # bulletin source but no PDF.
    #
    # Gated to policy-blocked hosts: a browser is built per call and costs ~1s
    # plus memory, which must not be spent on churches that answer a plain GET.
    #
    # AND GATED OFF BY DEFAULT. On its first outing this cost the national sweep
    # three shards: blocked hosts are common, this tried up to four candidates
    # each, and a Chromium per candidate alongside spaCy and pdfplumber does not
    # fit in 2GB — the same ceiling that forced --workers from 4 to 2. A better
    # classifier is not worth destabilising the sweep that is actually
    # recovering churches. Run it as its own low-concurrency pass:
    #
    #     WALLED_BROWSER=1 python extract_bulletins_to_db99.py --church-ids ... --workers 1
    #
    # Two candidates, not four, and a per-process budget, so one pathological
    # run cannot spawn browsers without limit.
    if (
        not result["pdf_urls"]
        and WALLED_BROWSER_ENABLED
        and _walled_budget_left()
        and HAS_PLAYWRIGHT
        and _playwright_proxy()
    ):
        try:
            walled = _host_policy.policy_for(base_url) == "blocked"
        except Exception:
            walled = False
        if walled:
            # Bulletin PATHS first, homepage last. Trying the homepage first
            # harvested whatever PDFs happened to be lying on it — St John
            # Vianney returned a cemetery fee schedule and a stewardship
            # worksheet, which would then be mined for names and produce
            # confident junk. A bulletin page yields bulletins; a homepage
            # yields documents.
            for candidate in (
                base_origin + "/bulletin",
                base_origin + "/bulletins",
                base_url,
            ):
                if not _walled_budget_spend():
                    break
                browser_pdfs = _extract_pdfs_with_browser(candidate)
                if not browser_pdfs:
                    continue
                # On the homepage fall-through, keep only links that actually
                # look like bulletins, rather than every PDF on the page.
                if candidate == base_url:
                    browser_pdfs = [
                        u
                        for u in browser_pdfs
                        if any(k in u.lower() for k in ("bulletin", "/bulletins/", "weekly"))
                    ]
                    if not browser_pdfs:
                        continue
                result["pdf_urls"] = browser_pdfs
                result["bulletin_page_url"] = candidate
                result["source"] = "walled_browser_proxy"
                return result

    return result


def try_discovermass_fallback(church_name: str, city: str, state_code: str):
    """
    Strategy 5 fallback: Try to find bulletins on DiscoverMass.com using
    the predictable slug format: church-name-city-state (lowercase, hyphenated).

    DiscoverMass has many churches with downloadable bulletin PDFs that
    churches may not link to from their own websites.
    """
    if not church_name or not city:
        return None

    # Generate the slug: lowercase, remove special chars, hyphenate
    slug_parts = []
    for word in re.split(r"[\s]+", church_name):
        # Remove parenthetical suffixes like "(FSSP)" or "(Maronite)"
        word = re.sub(r"\(.*?\)", "", word).strip()
        # Remove punctuation except hyphens
        word = re.sub(r"[^a-zA-Z0-9-]", "", word)
        if word:
            slug_parts.append(word.lower())

    city_clean = re.sub(r"[^a-zA-Z0-9]", "-", city.lower()).strip("-")
    state_clean = state_code.lower() if state_code else ""

    slug = "-".join(slug_parts) + "-" + city_clean + "-" + state_clean
    slug = re.sub(r"-+", "-", slug)  # collapse multiple hyphens

    dm_url = f"https://discovermass.com/church/{slug}/"
    resp = _rate_limited_get(dm_url)
    if not resp or resp.status_code != 200:
        return None

    # Check if this page has bulletin PDFs
    dm_pdfs = re.findall(
        r'https?://bulletins\.discovermass\.com/download\.php\?bulletin=[^\s"\'<>]+', resp.text
    )
    if not dm_pdfs:
        return None

    # Deduplicate
    seen = set()
    unique_pdfs = []
    for url in dm_pdfs:
        if url not in seen:
            seen.add(url)
            unique_pdfs.append(url)

    logger.debug(f"  DiscoverMass fallback hit for {slug}: {len(unique_pdfs)} bulletins")

    return {
        "bulletin_page_url": dm_url,
        "pdf_urls": unique_pdfs[: _effective_pdf_cap()] if _effective_pdf_cap() else unique_pdfs,
        "source": "discovermass_fallback",
        "lpi_parish_id": None,
    }


def extract_pdfs_from_subpages(soup, page_url: str, max_subpages: int = 10):
    """
    WordPress/blog-style bulletin archives: the bulletin page lists blog post
    links (e.g. /bulletins/first-sunday-of-lent/) that each contain the actual
    PDF download link one level deeper.

    This function finds sub-page links under the same path prefix and follows
    the most recent ones to extract PDFs.
    """
    parsed_page = urlparse(page_url)
    page_path = parsed_page.path.rstrip("/")
    # Collect candidate sub-page links (same domain, under the bulletin path)
    subpage_urls = []
    seen = set()
    for link in soup.find_all("a", href=True):
        href = link["href"]
        full_url = urljoin(page_url, href)
        parsed_link = urlparse(full_url)

        # Must be same domain
        if parsed_link.netloc != parsed_page.netloc:
            continue
        # Must be a sub-path of the bulletin page (e.g. /bulletins/some-post/)
        link_path = parsed_link.path.rstrip("/")
        if not link_path.startswith(page_path + "/") or link_path == page_path:
            continue
        # Skip PDFs (handled by extract_pdf_links already)
        if link_path.lower().endswith(".pdf"):
            continue
        # Skip pagination links (/page/2/, ?page=2, etc.)
        if re.search(r"/page/\d+", link_path) or re.search(r"[?&]page=", full_url):
            continue
        # Deduplicate
        canonical = full_url.split("?")[0].rstrip("/")
        if canonical in seen:
            continue
        seen.add(canonical)

        link_text = (link.get_text() or "").strip()
        date_score = extract_date_score(full_url, link_text)
        subpage_urls.append((full_url, date_score, link_text))

    if not subpage_urls:
        return []

    # Sort by date score (most recent first) and limit
    subpage_urls.sort(key=lambda x: x[1], reverse=True)
    subpage_urls = subpage_urls[:max_subpages]

    # Follow each sub-page and extract PDF links
    all_pdfs = []
    for sub_url, _, _ in subpage_urls:
        sub_soup = safe_get_html(sub_url)
        if sub_soup:
            pdfs = extract_pdf_links(sub_soup, sub_url)
            all_pdfs.extend(pdfs)

    logger.debug(
        f"  WordPress subpage scan: checked {len(subpage_urls)} posts, found {len(all_pdfs)} PDFs"
    )
    return all_pdfs


def extract_pdf_links(soup, page_url: str):
    """Extract all PDF links from a page, sorted by likely recency.

    Also extracts PDFs embedded via:
      - Google Docs Viewer (docs.google.com/gview?url=<encoded_pdf_url>)
      - Google Drive viewer (drive.google.com/viewerng/viewer?url=...)
      - LPi/ParishesOnline iframes
    """
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

        pdf_links.append(
            {
                "url": full_url,
                "text": link_text[:100],
                "date_score": date_score,
            }
        )

    # Check iframes for embedded content
    for iframe in soup.find_all("iframe", src=True):
        src = iframe["src"]

        # LPi/ParishesOnline embeds
        if "parishesonline.com" in src or "4lpi.com" in src:
            parsed = urlparse(src)
            params = parse_qs(parsed.query)
            if "selectedPublication" in params:
                pdf_url = params["selectedPublication"][0]
                if pdf_url not in seen:
                    seen.add(pdf_url)
                    pdf_links.append(
                        {
                            "url": pdf_url,
                            "text": "LPi Bulletin",
                            "date_score": 99999999,
                        }
                    )

        # Google Docs Viewer: docs.google.com/gview?url=<encoded_pdf>
        if "docs.google.com/gview" in src or "docs.google.com/viewer" in src:
            parsed = urlparse(src)
            params = parse_qs(parsed.query)
            if "url" in params:
                pdf_url = params["url"][0]
                if pdf_url not in seen:
                    seen.add(pdf_url)
                    date_score = extract_date_score(pdf_url, "")
                    pdf_links.append(
                        {
                            "url": pdf_url,
                            "text": "Google Viewer Bulletin",
                            "date_score": date_score if date_score else 99999999,
                        }
                    )

        # Google Drive viewer
        if "drive.google.com/viewerng" in src:
            parsed = urlparse(src)
            params = parse_qs(parsed.query)
            if "url" in params:
                pdf_url = params["url"][0]
                if pdf_url not in seen:
                    seen.add(pdf_url)
                    pdf_links.append(
                        {
                            "url": pdf_url,
                            "text": "Google Drive Bulletin",
                            "date_score": 99999999,
                        }
                    )

    # Check for Google Docs viewer links in <a> tags too
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "docs.google.com/gview" in href or "docs.google.com/viewer" in href:
            parsed = urlparse(href)
            params = parse_qs(parsed.query)
            if "url" in params:
                pdf_url = params["url"][0]
                if pdf_url not in seen:
                    seen.add(pdf_url)
                    date_score = extract_date_score(pdf_url, "")
                    pdf_links.append(
                        {
                            "url": pdf_url,
                            "text": "Google Viewer Bulletin",
                            "date_score": date_score if date_score else 99999999,
                        }
                    )

    # Check for DiscoverMass bulletin download links in page source
    raw_html = str(soup)
    dm_downloads = re.findall(
        r'https?://bulletins\.discovermass\.com/download\.php\?bulletin=[^\s"\'<>]+', raw_html
    )
    for dm_url in dm_downloads:
        if dm_url not in seen:
            seen.add(dm_url)
            pdf_links.append(
                {
                    "url": dm_url,
                    "text": "DiscoverMass Bulletin",
                    "date_score": 99999999,
                }
            )

    # Sort by date (most recent first)
    pdf_links.sort(key=lambda x: x["date_score"], reverse=True)

    cap = _effective_pdf_cap()
    urls = [p["url"] for p in pdf_links]
    return urls[:cap] if cap else urls


def extract_date_score(url: str, text: str):
    """
    Try to extract a date from URL or link text.
    Returns an int YYYYMMDD for sorting (higher = more recent).
    Returns 0 if no date found.
    """
    combined = url + " " + text

    # Pattern: YYYYMMDD
    m = re.search(r"(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])", combined)
    if m:
        return int(m.group(1) + m.group(2) + m.group(3))

    # Pattern: MM-DD-YYYY or MM/DD/YYYY
    m = re.search(r"(0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])[-/](20\d{2})", combined)
    if m:
        return int(f"{m.group(3)}{int(m.group(1)):02d}{int(m.group(2)):02d}")

    # Pattern: YYYY/MM/ in path (WordPress uploads)
    m = re.search(r"/(20\d{2})/(0[1-9]|1[0-2])/", url)
    if m:
        return int(m.group(1) + m.group(2) + "01")

    # Pattern: Month Day, Year in text
    months = {
        "january": "01",
        "february": "02",
        "march": "03",
        "april": "04",
        "may": "05",
        "june": "06",
        "july": "07",
        "august": "08",
        "september": "09",
        "october": "10",
        "november": "11",
        "december": "12",
        "jan": "01",
        "feb": "02",
        "mar": "03",
        "apr": "04",
        "jun": "06",
        "jul": "07",
        "aug": "08",
        "sep": "09",
        "oct": "10",
        "nov": "11",
        "dec": "12",
    }
    m = re.search(
        r"(" + "|".join(months.keys()) + r")[\s._-]+(\d{1,2})[\s,._-]*(20\d{2})", combined.lower()
    )
    if m:
        month = months[m.group(1)]
        day = f"{int(m.group(2)):02d}"
        year = m.group(3)
        return int(year + month + day)

    return 0


def find_lpi_parish_id(soup, raw_html: str):
    """Check if page has LPi/ParishesOnline.com bulletin widget.

    Detects multiple LPi/ParishesOnline patterns:
      - publicationWidget iframe: ?type=bulletin&id=0018000000Qbz0UAAR
      - publications page: /publications?id=d45874de...
      - container embed: /bulletins/123/456/
      - organization page: /organization/parish-name-12345
      - find page: /find/parish-name-12345
    """
    # Pattern 1: publicationWidget iframe with Salesforce-style ID
    # e.g., parishesonline.com/publicationWidget?type=bulletin&id=0018000000Qbz0UAAR
    m = re.search(
        r'parishesonline\.com/publicationWidget\?[^"\']*?id=([0-9a-zA-Z]+)', raw_html, re.IGNORECASE
    )
    if m:
        return m.group(1)

    # Pattern 2: publications page with hash ID
    # e.g., parishesonline.com/publications?id=d45874de570a2a3aa1ec99e23f8b1fc5209aa77f
    m = re.search(r"parishesonline\.com/publications\?id=([0-9a-fA-F]+)", raw_html)
    if m:
        return m.group(1)

    # Pattern 3: organization or find page
    # e.g., parishesonline.com/organization/parish-name-12345
    # e.g., parishesonline.com/find/parish-name-12345
    m = re.search(r'parishesonline\.com/(?:organization|find)/([^"\'<>\s]+)', raw_html)
    if m:
        return m.group(1)

    # Pattern 4: numeric ID in URL
    m = re.search(r'parishesonline\.com/[^"\']*?(\d{4,6})', raw_html)
    if m:
        return m.group(1)

    # Pattern 5: container embed
    m = re.search(r"container\.parishesonline\.com/bulletins/\d+/(\d+)/", raw_html)
    if m:
        return m.group(1)

    return None


LPI_API_BASE = "https://api.parishesonline.com"


def extract_lpi_pdfs_from_api(lpi_id: str):
    """Resolve an LPi handle to direct bulletin PDF URLs via ParishesOnline's API.

    ParishesOnline renders bulletins through a client-side widget, so the PDF
    never appears as an <a href> in the served HTML — which is why the great
    majority of these parishes looked bulletin-less. The site's own public,
    unauthenticated JSON API is far more reliable than scraping that widget:

        GET /organizations/slug/<slug>            -> data.salesforce_id
        GET /organizations/<salesforce_id>/publications?type=bulletin
                                                  -> data[].fileUrl

    fileUrl is the direct container.parishesonline.com PDF, so nothing needs
    unwrapping downstream. Note the CDN mislabels these as
    application/octet-stream, so never trust Content-Type alone.
    """
    if not lpi_id:
        return []

    salesforce_id = lpi_id
    # Anything that is not already a Salesforce-style id is treated as a slug.
    if not re.fullmatch(r"001[A-Za-z0-9]{12,15}", lpi_id):
        resp = _rate_limited_get(f"{LPI_API_BASE}/organizations/slug/{lpi_id}")
        if not resp or resp.status_code != 200:
            return []
        try:
            salesforce_id = (resp.json().get("data") or {}).get("salesforce_id")
        except Exception:
            return []
        if not salesforce_id:
            return []

    resp = _rate_limited_get(
        f"{LPI_API_BASE}/organizations/{salesforce_id}/publications?type=bulletin"
    )
    if not resp or resp.status_code != 200:
        return []
    try:
        pubs = resp.json().get("data") or []
    except Exception:
        return []
    return [p["fileUrl"] for p in pubs if isinstance(p, dict) and p.get("fileUrl")]


def extract_lpi_pdfs_any(lpi_id: str):
    """Resolve an LPi/ParishesOnline handle to bulletin PDFs, whichever form it is.

    find_lpi_parish_id returns several different things depending on which
    pattern matched: a Salesforce-style widget id (0018000000Qc07PAAR), a hex
    publications hash, a numeric container id, or — pattern 3 — a bare
    organization SLUG such as "st-therese-of-lisieux-church". Only the first
    kinds work against the widget API; a slug silently yields nothing, which
    previously read as "this parish has no bulletins".

    Try the JSON API first, then the widget, then the organization page.
    """
    if not lpi_id:
        return []
    try:
        api_pdfs = extract_lpi_pdfs_from_api(lpi_id)
    except Exception as exc:
        logger.debug(f"LPi API lookup failed for {lpi_id}: {exc}")
        api_pdfs = []
    if api_pdfs:
        return api_pdfs
    widget_pdfs = extract_lpi_pdfs_from_widget(lpi_id)
    if widget_pdfs:
        return widget_pdfs
    # A slug (or anything the widget rejected) may still resolve as an org page.
    if not re.fullmatch(r"[0-9a-fA-F]+", lpi_id):
        for tmpl in (
            "https://www.parishesonline.com/organization/{}",
            "https://www.parishesonline.com/find/{}",
        ):
            try:
                org_pdfs = extract_lpi_pdfs(tmpl.format(lpi_id))
            except Exception:
                org_pdfs = []
            if org_pdfs:
                return org_pdfs
    return []


def extract_discovermass_pdfs(dm_url: str):
    """
    Extract bulletin PDF download URLs from a DiscoverMass church page.

    DiscoverMass embeds bulletin PDFs as:
      bulletins.discovermass.com/download.php?bulletin=<encoded_id>
    These are direct PDF downloads (Content-Type: application/pdf).
    """
    pdfs = []
    resp = _rate_limited_get(dm_url)
    if not resp or resp.status_code != 200:
        return pdfs

    # Find all download URLs
    download_urls = re.findall(
        r'https?://bulletins\.discovermass\.com/download\.php\?bulletin=[^\s"\'<>]+', resp.text
    )
    # Deduplicate while preserving order
    seen = set()
    for url in download_urls:
        if url not in seen:
            seen.add(url)
            pdfs.append(url)

    logger.debug(f"  DiscoverMass scan: found {len(pdfs)} bulletin PDFs")
    cap = _effective_pdf_cap()
    return pdfs[:cap] if cap else pdfs


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
            if href.lower().endswith(".pdf"):
                full_url = urljoin(lpi_url, href)
                if full_url not in pdfs:
                    pdfs.append(full_url)

    # Fallback: use Playwright to render the JS-heavy LPi page
    if not pdfs and HAS_PLAYWRIGHT:
        logger.debug(f"  LPi page: trying Playwright for {lpi_url}")
        browser_pdfs = _extract_pdfs_with_browser(lpi_url)
        if browser_pdfs:
            pdfs.extend(browser_pdfs)

    cap = _effective_pdf_cap()
    return pdfs[:cap] if cap else pdfs


def extract_lpi_pdfs_from_widget(parish_id: str):
    """
    Extract PDF URLs from an LPi/ParishesOnline publicationWidget.

    Many churches embed bulletins via an iframe pointing to:
      parishesonline.com/publicationWidget?type=bulletin&id=<ID>

    This function tries multiple LPi URL patterns to find downloadable PDFs.
    """
    pdfs = []

    # Try the publications page directly
    urls_to_try = []

    # If it looks like a Salesforce ID (starts with 001, alphanumeric)
    if re.match(r"^001[0-9a-zA-Z]+$", parish_id, re.IGNORECASE):
        urls_to_try.append(
            f"https://parishesonline.com/publicationWidget?type=bulletin&id={parish_id}"
        )
        urls_to_try.append(
            f"https://www.parishesonline.com/publicationWidget?type=bulletin&id={parish_id}"
        )

    # If it's a hex hash
    elif re.match(r"^[0-9a-fA-F]{20,}$", parish_id):
        urls_to_try.append(f"https://parishesonline.com/publications?id={parish_id}")
        urls_to_try.append(f"https://www.parishesonline.com/publications?id={parish_id}")

    # If it's an organization slug (like "our-lady-of-grace-church-85239")
    elif not parish_id.isdigit():
        urls_to_try.append(f"https://parishesonline.com/organization/{parish_id}")
        urls_to_try.append(f"https://www.parishesonline.com/find/{parish_id}")

    # Numeric ID
    else:
        urls_to_try.append(f"https://parishesonline.com/publications?id={parish_id}")

    for url in urls_to_try:
        resp = _rate_limited_get(url)
        if resp and resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "lxml")
            # Look for PDF links
            for link in soup.find_all("a", href=True):
                href = link["href"]
                full_url = urljoin(url, href)
                if full_url.lower().endswith(".pdf") or ".pdf?" in full_url.lower():
                    if full_url not in pdfs:
                        pdfs.append(full_url)
            # Look for links containing "download" or "view" near bulletin
            for link in soup.find_all("a", href=True):
                href = link["href"]
                text = (link.get_text() or "").lower()
                if ("download" in text or "view" in text) and "bulletin" in text:
                    full_url = urljoin(url, href)
                    if full_url not in pdfs and full_url.lower().endswith(".pdf"):
                        pdfs.append(full_url)
            if pdfs:
                break

    if pdfs:
        logger.debug(f"  LPi widget HTTP scan for {parish_id}: found {len(pdfs)} PDFs")
        cap = _effective_pdf_cap()
        return pdfs[:cap] if cap else pdfs

    # Fallback: use Playwright headless browser to render the JS widget
    if HAS_PLAYWRIGHT:
        widget_url = (
            f"https://www.parishesonline.com/publicationWidget?type=bulletin&id={parish_id}"
        )
        logger.debug(f"  LPi widget: trying Playwright for {parish_id}")
        browser_pdfs = _extract_pdfs_with_browser(widget_url)
        if browser_pdfs:
            logger.debug(f"  LPi widget Playwright for {parish_id}: found {len(browser_pdfs)} PDFs")
            cap = _effective_pdf_cap()
            return browser_pdfs[:cap] if cap else browser_pdfs

    logger.debug(f"  LPi widget scan for {parish_id}: found 0 PDFs")
    return []


def _extract_pdfs_with_browser_from_page(page_url: str):
    """
    Use Playwright to render a church's bulletin page and extract PDF links.

    Used for eCatholic and other JS-heavy sites where HTTP returns no PDF links.
    """
    if not HAS_PLAYWRIGHT:
        return []
    return _extract_pdfs_with_browser(page_url)


# ── Phase 2: PDF Download ─────────────────────────────────────────────────────


def unwrap_pdf_url(pdf_url: str) -> str:
    """Return the directly-downloadable PDF behind a viewer/wrapper URL.

    LPi hands back reader links shaped like

        parishesonline.com/publication-page/<slug>?selectedPublication=<REAL PDF>

    Fetching that wrapper returns an HTML reader page, so the download step saw
    Content-Type: text/html and discarded it — a church could discover a dozen
    bulletins and still record 0 downloaded. Google Drive viewer links have the
    same problem and are rewritten to their direct-download form.
    """
    if not pdf_url:
        return pdf_url

    try:
        qs = parse_qs(urlparse(pdf_url).query)
    except Exception:
        return pdf_url
    for key in ("selectedPublication", "publication", "file", "url", "src"):
        for candidate in qs.get(key, []):
            candidate = unquote(candidate)
            if candidate.lower().startswith("http") and ".pdf" in candidate.lower():
                return candidate

    # Google Drive viewer -> direct download
    m = re.search(r"drive\.google\.com/file/d/([A-Za-z0-9_-]+)", pdf_url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    m = re.search(r"drive\.google\.com/open\?id=([A-Za-z0-9_-]+)", pdf_url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"

    return pdf_url


def download_bulletin_pdf(pdf_url: str, save_dir: Path, church_slug: str):
    """
    Download a bulletin PDF. Returns the local file path or None.
    """
    # Name from the ORIGINAL url so re-runs dedupe against existing files, but
    # fetch the unwrapped one.
    fetch_url = unwrap_pdf_url(pdf_url)
    url_hash = hashlib.md5(pdf_url.encode()).hexdigest()[:8]

    # Try to extract date from URL (the unwrapped one carries the real date)
    date_str = ""
    m = re.search(r"(20\d{6})", fetch_url)
    if m:
        date_str = m.group(1) + "_"
    else:
        m = re.search(r"(20\d{2})[-/](0[1-9]|1[0-2])[-/](\d{2})", fetch_url)
        if m:
            date_str = m.group(1) + m.group(2) + m.group(3) + "_"

    filename = f"{church_slug}_{date_str}{url_hash}.pdf"
    save_path = save_dir / filename

    # Skip if already downloaded
    if save_path.exists() and save_path.stat().st_size > 0:
        logger.debug(f"Already downloaded: {filename}")
        return save_path

    resp = _rate_limited_get(fetch_url)
    if resp is None:
        return None

    if resp.status_code != 200:
        logger.debug(f"PDF download failed ({resp.status_code}): {fetch_url}")
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


def extract_text_from_pdf(pdf_path_or_bytes):
    """Extract all text from a PDF using column-aware extraction.

    Uses column detection to prevent cross-column name merging in multi-column
    bulletin layouts. Each column's text is kept separate for name extraction.

    Args:
        pdf_path_or_bytes: Path to PDF file, or bytes/BytesIO of PDF content.
            Accepts Path, str, bytes, or io.BytesIO.

    Returns:
        Tuple of (full_text, column_texts) where:
        - full_text: all text concatenated (for saving to .txt)
        - column_texts: list of per-column strings (for name extraction)
    """
    if not HAS_PDFPLUMBER:
        return "", []

    try:
        import io

        from src.utils.pdf_columns import extract_columns_from_page

        # Accept bytes, BytesIO, Path, or str
        if isinstance(pdf_path_or_bytes, bytes):
            pdf_source = io.BytesIO(pdf_path_or_bytes)
        elif isinstance(pdf_path_or_bytes, io.BytesIO):
            pdf_source = pdf_path_or_bytes
        else:
            pdf_source = str(pdf_path_or_bytes)

        with pdfplumber.open(pdf_source) as pdf:
            all_column_texts = []
            page_texts = []
            for page in pdf.pages:
                columns = extract_columns_from_page(page)
                if columns:
                    all_column_texts.extend(columns)
                    # Join columns with separator for the saved text file
                    page_texts.append("\n\n".join(columns))

            full_text = "\n\n".join(page_texts)
            return full_text, all_column_texts
    except Exception as e:
        name = getattr(pdf_path_or_bytes, "name", str(pdf_path_or_bytes)[:80])
        logger.debug(f"PDF extraction failed for {name}: {e}")
        return "", []


def parse_name_parts(full_name: str) -> dict:
    """
    Split a full name into structured parts: title, first, middle, last.

    Uses the nameparser library when available for robust parsing of complex
    names (handles "Fr. John M. Smith Jr." correctly). Falls back to manual
    splitting when nameparser is not installed.

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

    # Use nameparser library if available (handles complex names better)
    if HAS_NAMEPARSER:
        hn = HumanName(full_name)
        result["title"] = hn.title
        result["first_name"] = hn.first
        result["middle_name"] = hn.middle
        result["last_name"] = hn.last
        return result

    # Fallback: manual parsing
    parts = full_name.strip().split()
    if not parts:
        return result

    # Check if first token is a title/prefix (honorifics only)
    title_patterns = {
        "fr.",
        "father",
        "rev.",
        "reverend",
        "msgr.",
        "monsignor",
        "dcn.",
        "deacon",
        "sr.",
        "sister",
        "br.",
        "brother",
        "dr.",
        "doctor",
        "mr.",
        "mrs.",
        "ms.",
        "miss",
        "prof.",
        "professor",
        "bishop",
        "archbishop",
    }

    if parts[0].lower().rstrip(".") + "." in title_patterns or parts[0].lower() in title_patterns:
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
        _SHARED_STAFF_ROLES
        if _HAS_SHARED_CONSTANTS
        else (
            r"Pastor|Associate Pastor|Parochial Vicar|Parochial Administrator"
            r"|Administrator|Priest|Rector|Chaplain|Celebrant"
            r"|Permanent Deacon|Transitional Deacon"
            r"|Business Manager|Business Mgr\.|Office Manager|Parish Manager"
            r"|Parish Secretary|Parish Administrator|Administrative Assistant"
            r"|Bookkeeper|Assistant Bookkeeper|Receptionist"
            r"|Compliance(?:/Acct\.\s*Asst\.)?|Compliance Officer"
            r"|Director of Religious (?:Education|Ed)|Religious Ed(?:ucation)?"
            r"|Director of Faith Formation|Faith Formation Director"
            r"|School Principal|School Secretary|Principal"
            r"|Director of Youth Ministry|Youth Minister|Youth Director"
            r"|Director of Music|Music Director|Music Minister"
            r"|Liturgy Director|Liturgist|Worship Director"
            r"|Maintenance|Custodian|Facilities Manager|Facilities Director"
            r"|Maintenance Tech|Groundskeeper|Sexton"
            r"|Director|Coordinator|Minister|Moderator"
            r"|RCIA Director|RCIA Coordinator"
            r"|Sacristan|Organist|Cantor|Choir Director"
            r"|Stewardship Director|Communications Director"
            r"|Hispanic Ministry|Spanish Ministry"
            r"|Chairman|Co-Chairman|Chairperson|Co-Chair"
            r"|Vice Chairman|Vice Chairperson"
            r"|President|Vice President"
            r"|Secretary|Treasurer"
            r"|Grand Knight|Deputy Grand Knight"
            r"|Financial Secretary|Membership Director"
            r"|ASCS Principal"
        )
    )

    # Name pattern: optional honorific + first [middle] last
    # STRICT version: only 2-4 capitalized words after optional honorific.
    # Does NOT greedily consume trailing text from adjacent PDF columns.
    NAME_PATTERN = (
        r"(?:(?:Fr\.|Father|Rev\.|Reverend|Msgr\.|Monsignor|Dcn\.|Deacon"
        r"|Sr\.|Sister|Br\.|Brother|Dr\.|Bishop|Archbishop)\s+)?"
        r"[A-Z][a-z]{1,15}"  # First name
        r"(?:\s+[A-Z]\.)?"  # Optional middle initial
        r"(?:\s+(?:De\s+La\s+)?[A-Z][a-z]{1,20}){1,2}"  # Last name (1-2 parts, allows "De La Rosa")
    )

    # Match: Role [separator] Name
    staff_pattern = re.compile(rf"({STAFF_ROLES})\s*[:\-–—]?\s+({NAME_PATTERN})", re.IGNORECASE)

    for m in staff_pattern.finditer(text):
        role_raw = m.group(1).strip()
        name_raw = m.group(2).strip()
        name_raw = re.sub(r"\s+", " ", name_raw)

        # Clean up trailing noise: phone numbers, email, punctuation
        name_raw = re.sub(r"\s*\d{3}[\-\.]\d{3,4}.*$", "", name_raw)  # phone
        name_raw = re.sub(r"\s*\(?\d{3}\)?.*$", "", name_raw)  # area code
        # Trim trailing words that are clearly NOT part of a name.
        # PDF columns frequently bleed together: "Patrick Ledger Saturday Vigil"
        # We strip everything from the first non-name word onwards.
        # This list includes common bulletin text that gets appended to names.
        name_raw = re.sub(
            r"\s+(?:Saturday|Sunday|Monday|Tuesday|Wednesday|Thursday|Friday"
            r"|Vigil|Mass|Vacant|Open|Position|By|Appointment|Please|Parish"
            r"|Secretary|Vice|www\b|http|Sick|Marriage|Baptism|Members?"
            r"|Business|School|Religious|Compliance|Office|Church|classes"
            r"|First|Anointing|Maintenance|DEACONS?|STAFF|COUNCIL|BOARD"
            r"|for|the|or|and|at|of|in|to|with|on|not|out|reach"
            r"|You|Are|Dcn\b|Dir\b).*$",
            "",
            name_raw,
            flags=re.IGNORECASE,
        )
        name_raw = name_raw.strip()

        # Skip "Position Open", "Vacant", "TBD", etc.
        if re.match(r"^(?:Position\s+Open|Vacant|TBD|None|Open|N/?A)$", name_raw, re.IGNORECASE):
            continue
        # Skip if it looks like a phrase, not a name
        if re.match(
            r"^(?:religious|classes|grades|brother|sister|priest|Tech|I\b)", name_raw, re.IGNORECASE
        ):
            continue

        # Validate the name
        name_raw = clean_extracted_name(name_raw)
        name_parts = parse_name_parts(name_raw)
        # Reconstruct the "clean" name (without title) for validation
        clean_name = " ".join(
            p
            for p in [name_parts["first_name"], name_parts["middle_name"], name_parts["last_name"]]
            if p
        )
        clean_name = clean_extracted_name(clean_name)

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
            names.append(
                {
                    "name": clean_name,
                    **name_parts,
                    "role": role_raw.strip().rstrip(":").rstrip("-").strip(),
                    "context": text[max(0, m.start() - 20) : m.end() + 30].strip(),
                    "category": "clergy_staff",
                }
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Pattern 1b: Honorific-only clergy (no explicit role keyword on line)
    # ─────────────────────────────────────────────────────────────────────────
    # Catches: "Fr. Michael Martinez" or "Dcn. Reynaldo Romo" appearing
    # anywhere (not just after a role keyword).
    # The title itself implies a role (priest, deacon, etc.)
    # Strict name pattern for honorific matches: FirstName [MiddleInitial] LastName only
    # (max 2-3 capitalized words, no trailing column bleed)
    honorific_pattern = re.compile(
        r"((?:Fr\.|Father|Rev\.|Reverend|Msgr\.|Monsignor|Dcn\.|Deacon"
        r"|Sr\.|Sister|Br\.|Brother|Bishop|Archbishop)\s+"
        r"[A-Z][a-z]{1,15}(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]{1,20})"
    )
    for m in honorific_pattern.finditer(text):
        full_match = m.group(1).strip()
        name_parts = parse_name_parts(full_match)
        # Reconstruct clean name without title
        clean_name = " ".join(
            p
            for p in [name_parts["first_name"], name_parts["middle_name"], name_parts["last_name"]]
            if p
        )
        clean_name = clean_extracted_name(clean_name)
        if not clean_name or len(clean_name) < 4:
            continue
        if is_valid_name(clean_name) and clean_name not in seen_names:
            seen_names.add(clean_name)
            # Infer role from honorific
            honorific = full_match.split()[0].lower().rstrip(".")
            implied_role = {
                "fr": "Priest",
                "father": "Priest",
                "rev": "Priest",
                "reverend": "Priest",
                "msgr": "Monsignor",
                "monsignor": "Monsignor",
                "dcn": "Deacon",
                "deacon": "Deacon",
                "sr": "Sister",
                "sister": "Sister",
                "br": "Brother",
                "brother": "Brother",
                "bishop": "Bishop",
                "archbishop": "Archbishop",
            }.get(honorific, "")
            names.append(
                {
                    "name": clean_name,
                    **name_parts,
                    "role": implied_role,
                    "context": text[max(0, m.start() - 30) : m.end() + 30].strip(),
                    "category": "clergy_staff",
                }
            )

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
        r"\bDEACONS?\b": "Deacon",
        r"\bPASTORAL COUNCIL\b": "Pastoral Council Member",
        r"\bFINANCE COUNCIL\b": "Finance Council Member",
        r"\bPARISH COUNCIL\b": "Parish Council Member",
        r"\bBOARD OF DIRECTORS\b": "Board of Directors Member",
        r"\bSTAFF\b": "",  # Staff section — individual roles are per-line
        r"\bPARISH STAFF\b": "",  # Same
        r"\bMINISTRY CONTACTS?\b": "",
        r"\bMINISTRIES AND CONTACTS\b": "",
    }

    for header_pattern, section_role in section_headers.items():
        for header_match in re.finditer(header_pattern, text):
            # Get the text after this header, up to the next ALL-CAPS header or 500 chars
            start = header_match.end()
            remaining = text[start : start + 500]

            # Stop at the next ALL-CAPS section header (line that is mostly uppercase)
            lines = remaining.split("\n")
            section_text_lines = []
            for line in lines[1:]:  # skip the first line (might be part of header)
                stripped = line.strip()
                if not stripped:
                    continue
                # Stop at next section header: all-caps line with 2+ words
                if (
                    stripped.isupper()
                    and len(stripped.split()) >= 2
                    and not re.match(r"^\d", stripped)
                ):
                    break
                section_text_lines.append(stripped)

            section_text = "\n".join(section_text_lines)

            # Extract names from this section.
            # Names can be: comma-separated, &-separated, or one-per-line.
            # Lines that start with a known role keyword (e.g. "President Barry Gaston")
            # are handled by Pattern 1. Here we only grab bare names that DIDN'T match
            # Pattern 1 — these inherit the section header role.
            name_candidates = re.split(r"[,&\n]+", section_text)
            for candidate in name_candidates:
                candidate = candidate.strip()
                if not candidate:
                    continue
                # Skip lines that are clearly non-names
                if re.match(r"^\d", candidate):  # starts with digit
                    continue
                if len(candidate) < 5:
                    continue
                # Skip lines that start with a known role keyword
                # (those are already handled by Pattern 1 with their specific role)
                if re.match(rf"^(?:{STAFF_ROLES})\b", candidate, re.IGNORECASE):
                    continue

                # Find name patterns: optional honorific + FirstName [Middle] LastName
                for name_match in re.finditer(
                    r"(?:(?:Fr\.|Rev\.|Msgr\.|Dcn\.|Sr\.|Br\.)\s+)?"
                    r"([A-Z][a-z]{1,15}(?:\s+[A-Z]\.?)?\s+(?:De\s+La\s+)?[A-Z][a-z]{1,20}"
                    r"(?:\s+[A-Z][a-z]{1,20})?)",
                    candidate,
                ):
                    name = name_match.group(0).strip()
                    name = re.sub(r"\s+", " ", name)
                    # Remove trailing phone numbers
                    name = re.sub(r"\s*\d{3}[\-\.]\d{3,4}.*$", "", name)
                    name = re.sub(r"\s*\(?\d{3}\)?.*$", "", name)
                    name = name.strip()

                    name = clean_extracted_name(name)
                    name_parts = parse_name_parts(name)
                    clean_name = " ".join(
                        p
                        for p in [
                            name_parts["first_name"],
                            name_parts["middle_name"],
                            name_parts["last_name"],
                        ]
                        if p
                    )
                    clean_name = clean_extracted_name(clean_name)
                    if clean_name and is_valid_name(clean_name) and clean_name not in seen_names:
                        seen_names.add(clean_name)
                        names.append(
                            {
                                "name": clean_name,
                                **name_parts,
                                "role": section_role,
                                "context": header_match.group(0).strip(),
                                "category": "clergy_staff",
                            }
                        )

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
        _SHARED_MINISTRY_ROLES
        if _HAS_SHARED_CONSTANTS
        else (
            r"Altar Servers?|Eucharistic Ministers?|Lectors?|Readers?"
            r"|Ushers?(?:/Greeters?)?|Greeters?|Sacristans?"
            r"|Gift Shop|Hospital Euch(?:aristic)?\.?\s*Ministers?"
            r"|Jail Ministry|Knights? of Columbus|Ladies\s+Guild"
            r"|Linens|Marriage Preparation|Money Counters?"
            r"|Prayer Garden|Music Ministry|Choir"
            r"|Baptism Class|Hispanic Spiritual Dir(?:ector)?"
            r"|(?:You Are )?Not Alone|St\.?\s*Vincent de Paul"
            r"|Religious Education|RCIA|Compliance Officer"
            r"|Homebound Euch(?:aristic)?\.?\s*Ministers?"
        )
    )

    # Match: MinistryRole [comma/colon] PersonName [PhoneNumber]
    # The name part requires at least 2 capitalized words with reasonable length
    ministry_contact_pattern = re.compile(
        rf"({MINISTRY_ROLES})[,:\s]+\s*"
        rf"((?:(?:Fr\.|Rev\.|Msgr\.|Dcn\.|Sr\.|Br\.)\s+)?"
        rf"[A-Z][a-z]{{2,15}}(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]{{2,20}}(?:\s+[A-Z][a-z]{{2,20}})?)"
        rf"(?:\s+\d{{3}}[\-\.]\d{{3,4}})?",
        re.IGNORECASE,
    )

    for m in ministry_contact_pattern.finditer(text):
        ministry_role = m.group(1).strip()
        name_raw = m.group(2).strip()
        name_raw = re.sub(r"\s+", " ", name_raw)

        name_raw = clean_extracted_name(name_raw)
        name_parts = parse_name_parts(name_raw)
        clean_name = " ".join(
            p
            for p in [name_parts["first_name"], name_parts["middle_name"], name_parts["last_name"]]
            if p
        )
        clean_name = clean_extracted_name(clean_name)
        if clean_name and is_valid_name(clean_name) and clean_name not in seen_names:
            seen_names.add(clean_name)
            names.append(
                {
                    "name": clean_name,
                    **name_parts,
                    "role": ministry_role.strip(),
                    "context": text[max(0, m.start() - 10) : m.end() + 20].strip(),
                    "category": "clergy_staff",
                }
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Pattern 4: Mass Intentions
    # ─────────────────────────────────────────────────────────────────────────
    # "For the repose of the soul of John Smith"
    # "Special intentions of Mary Jones"
    # "+John Smith" (deceased marker)
    # "requested by Jane Doe"
    intention_patterns = [
        r"(?:repose of (?:the soul of )?|intention[s]? of |in memory of |for the (?:healing|health|recovery) of )([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        r"(?:requested by |offered by |from )([A-Z][a-z]+(?:\s+(?:&|and)\s+)?[A-Z]?[a-z]*\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        r"(?:†|✝|\+)\s*([A-Z][a-z]+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
    ]

    for pattern in intention_patterns:
        for m in re.finditer(pattern, text):
            name = m.group(1).strip()
            name = re.sub(r"\s+", " ", name)
            # Remove trailing conjunction fragments
            name = re.sub(r"\s+(?:and|&)\s*$", "", name)
            name = clean_extracted_name(name)
            if is_valid_name(name) and name not in seen_names:
                seen_names.add(name)
                name_parts = parse_name_parts(name)
                names.append(
                    {
                        "name": name,
                        **name_parts,
                        "role": "",
                        "context": text[max(0, m.start() - 20) : m.end() + 20].strip(),
                        "category": "mass_intention",
                    }
                )

    # ─────────────────────────────────────────────────────────────────────────
    # Pattern 5: Prayer / Sick Lists
    # ─────────────────────────────────────────────────────────────────────────
    # Comma-separated lists following headers like "Please pray for:"
    prayer_sections = re.finditer(
        r"(?:(?:pray(?:er)?\s*(?:list|request)?|sick\s*list|(?:those who are )?(?:sick|ill|homebound)|remember in prayer)[:\s]*)((?:[A-Z][a-z]+\s+[A-Z][a-z]+(?:\s*,\s*)?)+)",
        text,
        re.IGNORECASE,
    )
    for section in prayer_sections:
        name_list = section.group(1)
        # Split on commas, semicolons, and newlines
        for name_candidate in re.split(r"[,;\n]+", name_list):
            name = name_candidate.strip()
            name = clean_extracted_name(name)
            # Should be FirstName LastName format
            if re.match(r"^[A-Z][a-z]+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?$", name):
                if is_valid_name(name) and name not in seen_names:
                    seen_names.add(name)
                    name_parts = parse_name_parts(name)
                    names.append(
                        {
                            "name": name,
                            **name_parts,
                            "role": "",
                            "context": "prayer list",
                            "category": "prayer_list",
                        }
                    )

    # ─────────────────────────────────────────────────────────────────────────
    # Pattern 6: Generic contextual names (capitalized first+last near keywords)
    # ─────────────────────────────────────────────────────────────────────────
    # This is the broadest/loosest pattern. It finds "FirstName LastName"
    # patterns near ministry keywords. Confidence is MEDIUM.
    context_keywords = [
        "lector",
        "reader",
        "usher",
        "eucharistic minister",
        "altar server",
        "music director",
        "choir",
        "organist",
        "cantor",
        "knight",
        "ladies",
        "guild",
        "council",
        "committee",
        "contact",
        "volunteer",
        "coordinator",
        "chair",
        "baptism",
        "wedding",
        "funeral",
        "deceased",
        "recently deceased",
        "eternal rest",
        "rest in peace",
        "newly registered",
        "welcome",
    ]

    # Split text into lines for line-based proximity matching
    text_lines = text.split("\n")

    for kw in context_keywords:
        kw_lower = kw.lower()
        for line_idx, line in enumerate(text_lines):
            if kw_lower not in line.lower():
                continue
            # Look at this line + the next 5 lines (not 200 chars away)
            search_lines = text_lines[line_idx : line_idx + 6]
            nearby = "\n".join(search_lines)
            # Find capitalized name patterns in nearby lines
            for name_match in re.finditer(
                r"([A-Z][a-z]{1,15}\s+[A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20})?)", nearby
            ):
                name = clean_extracted_name(name_match.group(1).strip())
                if is_valid_name(name) and name not in seen_names:
                    seen_names.add(name)
                    name_parts = parse_name_parts(name)
                    names.append(
                        {
                            "name": name,
                            **name_parts,
                            "role": "",
                            "context": kw,
                            "category": "ministry_contextual",
                        }
                    )

    return names


# Common words that look like names but aren't
# Canonical list is in src/parsers/bulletin_constants.py
FALSE_POSITIVE_NAMES = (
    _SHARED_FP_NAMES
    if _HAS_SHARED_CONSTANTS
    else {
        "Holy Spirit",
        "Holy Family",
        "Holy Cross",
        "Holy Rosary",
        "Holy Trinity",
        "Sacred Heart",
        "Blessed Sacrament",
        "Blessed Mother",
        "Blessed Virgin",
        "Our Lady",
        "Our Father",
        "Jesus Christ",
        "Holy Name",
        "Good Shepherd",
        "Holy Communion",
        "First Communion",
        "Daily Mass",
        "Sunday Mass",
        "Mass Times",
        "Mass Intentions",
        "Divine Mercy",
        "Eternal Rest",
        "Saint Joseph",
        "Saint Patrick",
        "Saint Mary",
        "Saint Peter",
        "Saint Paul",
        "Saint Michael",
        "Saint Francis",
        "Saint Thomas",
        "Saint Elizabeth",
        "Palm Sunday",
        "Good Friday",
        "Easter Sunday",
        "Ash Wednesday",
        "Office Hours",
        "Parish Office",
        "Faith Formation",
        "Religious Education",
        "Social Media",
        "Weekly Bulletin",
        "Parish Life",
        "Parish Council",
        "Ministry Schedule",
        "Altar Society",
        "Church Bulletin",
        "North America",
        "South America",
        "New York",
        "New Jersey",
        "New Mexico",
        "Al Smith",  # very common false positive
        "Catholic Church",
        "United States",
        "Pope Francis",
        "Dear Parishioners",
        "Dear Friends",
        "For More",
        "Please Contact",
        "High School",
        "Middle School",
        "Sign Up",
        "Last Week",
        "Next Week",
        "This Week",
        "Thank You",
        "God Bless",
        "Weekday Masses",
        "Daily Mass",
        "Sunday Mass",
        "Altar Servers",
        "Eucharistic Ministers",
        "Music Director",
        "Prayer Tree",
        "Table Rentals",
        "Holy Hour",
        "Vincent De Paul",
        "De Paul",
        "Corpus Christi",
        "Stations Cross",
        "Bible Study",
        "Choir Practice",
        "Food Bank",
        "Soup Kitchen",
        "Thrift Store",
        "Office Manager",
        "Business Manager",
        "Facilities Manager",
        "Religious Ed",
        "Youth Minister",
        "Choir Director",
        "Maintenance Director",
        "Athletic Director",
        "Pro Life",
        "Right Life",
        # Bulletin structural/calendar phrases that look like names
        "Ordinary Time",
        "By Appointment",
        "Job Opportunity",
        "Assembly Mtg",
        "Council Mtg",
        "Degree Exemplification",
        "Money Counters",
        "Compliance Officer",
        "Mercy Chaplet",
        "Del Tiempo",
        "Domingo Del",
        "Consejo Matrimonial",
        "Grand Knight",
        "Deputy Grand",
        "Hospital Euch",
        "Hispanic Spiritual",
        "Tech I",
        "Tech II",
        # Common truncated/merged column artifacts from PDF extraction
        "Are Not",
        "You Are",
        "Are Not Alone",
        "Anderson Gift",
        "Business Mgr",
        "The Romo",
        "Of Jensen",
        # Top false positive phrases found in data analysis (751K names, 6 states)
        "New Year",
        "Immaculate Conception",
        "Columbus Council",
        "Finance Council",
        "Pastoral Council",
        "Pope Leo",
        "Food Pantry",
        "Fish Fry",
        "All Souls",
        "Wedding Anniversary",
        "Ordinary Time",
        "Second Vatican Council",
        "Deceased Members",
        "Volunteers Needed",
        "All Souls Day",
        "Lord Jesus Christ",
        "The Knights",
        "Paul Society",
        "May God",
        "Presbyteral Council",
        "Lord Jesus",
        "Good News",
        "Administrative Assistant",
        "The St",
        "Special Intention",
        "Memorial Day",
        "Jubilee Year",
        "Labor Day",
        "Virgin Mary",
        "Feast Day",
        "World Day",
        "Open House",
        "St Mary",
        "Jordan River",
        "Bake Sale",
        "Shawl Ministry",
        "Pancake Breakfast",
        "Latin America",
        "Old Testament",
        "The Lord",
        "Happy New Year",
        "Heavenly Father",
        "Thomas Aquinas",
        "Retirement Fund",
        "First Reconciliation",
        "Diocesan Council",
        "First Reading",
        "Place Your Ad",
        "Safe Environment",
        "Thanksgiving Day",
        "Rice Bowl",
        "The Diocese",
        "Respect Life",
        "Immaculate Heart",
        "The Epiphany",
        "Mailing Address",
        "Poor Souls",
        "Second Reading",
        "Main Street",
        "Columbus Meeting",
        "Extraordinary Minister",
        "Faithful Departed",
        "Life Activities",
        "New Testament",
        "Property Manager",
        "Development Manager",
        "Case Managers",
        "Sun Rehearsal",
        "English Ministry",
        "Brother Knight",
        # Bulletin ad/event junk that passes word-level filters
        "Auto Body",
        "Auto Repair",
        "Auto Insurance",
        "Fall Alert",
        "Craft Beer",
        "Beer Tent",
        "Beer Dance",
        "Wine Bar",
        "Wine Pull",
        "Ice Cream",
        "More Info",
        "Stay Connected",
        "Pork Sausage",
        "Fried Chicken",
        "Chicken Strips",
        "Cake Donation",
        "Smart Roofing",
        "Smart Roof",
        "Smart Driver",
        "Pizza Villa",
        "Sports App",
        "Ascension App",
        "Suggested Donation",
        "Contribution Statement",
        "Contribution Statements",
        "Spring Alpha Session",
        "Generation To Generation",
        "Doyle Vocal Quartet",
        "Vocal Quartet",
        "Blood Drive",
        "Craft Bazaar",
        # Spanish/Latin liturgical phrases
        "Primera Comuni",
        "Primera Comunion",
        "La Primera Comuni",
        "La Cuaresma",
        "El Evangelio",
        "Sacrosanctum Concilium",
        "Nueve Domingos",
        "El Comit",
        "Arroz La Cuaresma",
        # Phrases using 'Will' and 'Christian' that aren't names
        # (these words were unblocked because they're common real names)
        "Will Be",
        "Will Not",
        "Will Have",
        "Will Take",
        "Christian Education",
        "Christian Formation",
        "Christian Initiation",
        "Christian Service",
        "Christian Community",
        "Christian Life",
        # Additional false-positive phrases (org names, bulletin phrases)
        "Thank You",
        "God Bless",
        "Altar Servers",
        "Altar Society",
        "Church Name",
        "Parish Name",
        "Office Hours",
        "Bulletin Sponsor",
        "Weekly Collection",
        "Mass Schedule",
        "Faith Formation",
        "Religious Ed",
        "Choir Practice",
        "Youth Group",
        "Knights Columbus",
        "Ladies Auxiliary",
        "Sanctuary Lamp",
        "Eternal Rest",
        "Rest Peace",
        # Top false positives from data analysis (2026-03-25)
        "Precious Blood",
        "Canon Law",
        "Mardi Gras",
        "Faith Forma",
        "Mount Carmel",
        "Roman Missal",
        "Young People",
        "Ascension Press",
        "Supreme Court",
        "Little Flower",
        "La Crosse",
        "Columbus Free Throw",
        "King Herod",
        "Phone Fax",
        "Bus Driver",
        "Texas Roadhouse",
        "Adult Faith",
        "Mass Times",
        "Gospel Meditation",
        "Spiritual Direction",
        "Spring Work",
        "Parish Fund",
        "In Residence",
        "Same Day",
        "Topsoil Mulch",
        "Parish App",
        "Fulton Sheen",
        "Fulton J. Sheen",
        "Martin Luther King",
        "John Muir",
        "Every Friday",
    }
)


# ── Reference Data & Scoring ─────────────────────────────────────────────────

# Lazy singleton for SSA + Census reference data
_ssa_names = None
_census_surnames = None


def _load_reference_data():
    """Load SSA first names and Census surnames into module-level dicts.

    Returns (ssa_dict, census_dict) where each maps UPPER-cased name -> rank.
    Loaded once and cached for the process lifetime.
    """
    global _ssa_names, _census_surnames
    if _ssa_names is not None and _census_surnames is not None:
        return _ssa_names, _census_surnames

    ref_dir = Path(__file__).resolve().parent / "data" / "reference"

    # SSA first names: name,total_count,rank
    _ssa_names = {}
    ssa_path = ref_dir / "ssa_first_names.csv"
    if ssa_path.exists():
        with open(ssa_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("name") or "").strip().upper()
                try:
                    rank = int(row.get("rank") or "0")
                except ValueError:
                    rank = 999999
                if name:
                    _ssa_names[name] = rank
        logger.info(f"Loaded {len(_ssa_names):,} SSA first names from {ssa_path}")
    else:
        logger.warning(f"SSA first names file not found: {ssa_path}")

    # Census surnames: name,count,rank
    _census_surnames = {}
    census_path = ref_dir / "census_surnames.csv"
    if census_path.exists():
        with open(census_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("name") or "").strip().upper()
                try:
                    rank = int(row.get("rank") or "0")
                except ValueError:
                    rank = 999999
                if name:
                    _census_surnames[name] = rank
        logger.info(f"Loaded {len(_census_surnames):,} Census surnames from {census_path}")
    else:
        logger.warning(f"Census surnames file not found: {census_path}")

    return _ssa_names, _census_surnames


def score_name_confidence(person_name, category="", role="", title=""):
    """Score a name's likelihood of being a real person (0.0 to 1.0).

    Uses SSA first-name and Census surname dictionaries for data-driven
    validation, plus heuristic penalties for common junk patterns.
    """
    if not person_name or not person_name.strip():
        return 0.0

    ssa, census = _load_reference_data()
    parts = person_name.strip().split()
    if not parts:
        return 0.0

    # Skip title prefixes when identifying first/last name words
    title_prefixes_set = {
        "fr.",
        "rev.",
        "dr.",
        "sr.",
        "msgr.",
        "dcn.",
        "deacon",
        "father",
        "sister",
        "brother",
    }
    name_parts = [
        p for p in parts if p.lower().rstrip(".") not in {t.rstrip(".") for t in title_prefixes_set}
    ]
    if not name_parts:
        name_parts = parts  # fallback if all words are titles

    first_word = name_parts[0].upper()
    last_word = name_parts[-1].upper()
    score = 0.0

    # --- Bonuses ---

    # First name in SSA
    first_in_ssa = first_word in ssa
    if first_in_ssa:
        score += 0.30

    # Last name in Census
    last_in_census = last_word in census
    if last_in_census:
        score += 0.30

    # 2-word name bonus (most common real-name pattern)
    if len(name_parts) == 2:
        score += 0.10

    # Top-1000 first name bonus
    if first_in_ssa and ssa.get(first_word, 999999) <= 1000:
        score += 0.10

    # Top-1000 surname bonus
    if last_in_census and census.get(last_word, 999999) <= 1000:
        score += 0.10

    # Category boost for high-signal categories
    cat_lower = (category or "").lower()
    if cat_lower in ("clergy_staff", "mass_intention"):
        score += 0.15

    # Title prefix bonus (from the title field OR detected in person_name)
    has_title = False
    title_str = (title or "").strip()
    if title_str:
        if title_str.lower().rstrip(".") in {t.rstrip(".") for t in title_prefixes_set}:
            has_title = True
    if not has_title and parts[0].lower().rstrip(".") in {
        t.rstrip(".") for t in title_prefixes_set
    }:
        has_title = True
    if has_title:
        score += 0.05

    # --- Penalties ---

    # Non-name word check — any blocklist word means guaranteed removal
    non_name_words = _get_non_name_words()
    if any(p.lower() in non_name_words for p in parts):
        score -= 1.0

    # Newline artifact
    if "\n" in person_name:
        score -= 0.30

    # Truncation: last word < 3 chars and not an initial
    if len(last_word) < 3 and not re.match(r"^[A-Z]\.?$", name_parts[-1]):
        score -= 0.25

    # Merged name detection: 3+ words where last word is SSA-only (not Census)
    if len(name_parts) >= 3 and last_word in ssa and last_word not in census:
        # Last word looks like a first name, not a surname — likely merged
        if ssa.get(last_word, 999999) <= 5000:
            score -= 0.20

    # Org pattern: "X of Y", "X for Y"
    name_lower = person_name.lower()
    org_preps = [" of ", " for ", " de "]
    if any(prep in name_lower for prep in org_preps):
        score -= 0.15

    # First name NOT in SSA and NOT in Census
    if first_word not in ssa and first_word not in census:
        score -= 0.20

    # Last name NOT in Census and NOT in SSA
    if last_word not in census and last_word not in ssa:
        score -= 0.20

    # Clamp to [0.0, 1.0]
    return max(0.0, min(1.0, score))


def confidence_label(score):
    """Convert numeric confidence score to categorical label."""
    if score >= 0.7:
        return "high"
    elif score >= 0.4:
        return "medium"
    else:
        return "low"


def split_merged_name(person_name):
    """For 3+ word names, drop trailing first-name that was merged from next row.

    E.g. "Kevin Steinkamp Cynthia" -> "Kevin Steinkamp"
    (Cynthia is a common first name but not a surname)
    """
    parts = person_name.strip().split()
    if len(parts) < 3:
        return person_name

    ssa, census = _load_reference_data()
    last_word = parts[-1].upper()

    # If last word is a top-5000 SSA first name but NOT a Census surname, drop it
    if last_word in ssa and ssa.get(last_word, 999999) <= 5000 and last_word not in census:
        return " ".join(parts[:-1])

    return person_name


# Cache the non_name_words set so scoring can reuse it without re-creating
_non_name_words_cache = None


def _get_non_name_words():
    """Return the non_name_words set (cached after first call)."""
    global _non_name_words_cache
    if _non_name_words_cache is not None:
        return _non_name_words_cache
    # Build the set — must match the set inside is_valid_name()
    _non_name_words_cache = {
        "the",
        "and",
        "for",
        "from",
        "with",
        "that",
        "this",
        "are",
        "was",
        "has",
        "have",
        "had",
        "their",
        "there",
        "where",
        "when",
        "what",
        "which",
        "also",
        "than",
        "them",
        "not",
        "out",
        "who",
        "how",
        "its",
        "may",
        "can",
        "you",
        "your",
        "church",
        "parish",
        "school",
        "center",
        "hall",
        "room",
        "chapel",
        "sunday",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "january",
        "february",
        "march",
        "april",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "mass",
        "masses",
        "confession",
        "communion",
        "lent",
        "easter",
        "christmas",
        "daily",
        "weekly",
        "monthly",
        "annual",
        "weekday",
        "weekend",
        "morning",
        "evening",
        "night",
        "table",
        "prayer",
        "music",
        "director",
        "ministers",
        "eucharistic",
        "servers",
        "rentals",
        "maintenance",
        "hour",
        "holy",
        "blessed",
        "sacred",
        "saint",
        "our",
        "rite",
        "catholic",
        "initiation",
        "adults",
        "stations",
        "cross",
        "rosary",
        "adoration",
        "benediction",
        "tree",
        "garden",
        "office",
        "online",
        "giving",
        "live",
        "stream",
        "please",
        "contact",
        "call",
        "email",
        "visit",
        "registration",
        "information",
        "schedule",
        "calendar",
        "baptism",
        "confirmation",
        "marriage",
        "funeral",
        "anointing",
        "communion",
        "collection",
        "offertory",
        "budget",
        "total",
        "choir",
        "band",
        "ensemble",
        "group",
        "youth",
        "children",
        "family",
        "women",
        "men",
        "deacon",
        "priest",
        "bishop",
        "pastor",
        "vicar",
        "domingo",
        "tiempo",
        "ordinario",
        "semana",
        "consejo",
        "matrimonial",
        "president",
        "vice",
        "chairman",
        "secretary",
        "treasurer",
        "members",
        "lectors",
        "counters",
        "volunteer",
        "principal",
        "coordinator",
        "gift",
        "shop",
        "sick",
        "opportunity",
        "invitation",
        "tech",
        "degree",
        "exemplification",
        "assembly",
        "mtg",
        "chaplet",
        "spiritual",
        "dcn",
        "alone",
        "activities",
        "picnic",
        "proceeds",
        "novena",
        "drive",
        "sale",
        "bake",
        "pancake",
        "fry",
        "pantry",
        "store",
        "bank",
        "kitchen",
        "shawl",
        "food",
        "soup",
        "thrift",
        "day",
        "year",
        "time",
        "new",
        "old",
        "first",
        "second",
        "labor",
        "memorial",
        "thanksgiving",
        "happy",
        "jubilee",
        "gospel",
        "amen",
        "ordinary",
        "heavenly",
        "immaculate",
        "conception",
        "souls",
        "saints",
        "departed",
        "reconciliation",
        "testament",
        "sacrament",
        "intention",
        "special",
        "manager",
        "property",
        "development",
        "finance",
        "liaison",
        "bookkeeper",
        "case",
        "assistant",
        "needed",
        "volunteers",
        "members",
        "council",
        "meeting",
        "fund",
        "retirement",
        "environment",
        "safe",
        "street",
        "address",
        "mailing",
        "place",
        "house",
        "open",
        "sign",
        "reading",
        "word",
        "river",
        "america",
        "latin",
        "ad",
        "dear",
        "high",
        "middle",
        "pro",
        "right",
        "respect",
        "cardinal",
        "pope",
        "doctor",
        "anniversary",
        "feast",
        "world",
        "society",
        "news",
        "knight",
        "knights",
        "life",
        "active",
        "ministries",
        "ministry",
        "cultural",
        "community",
        "ushers",
        "wheelchair",
        "disciples",
        "committee",
        "basketball",
        "lector",
        "usher",
        "sacristan",
        "presider",
        "cantor",
        "greeters",
        "greeter",
        "server",
        "reader",
        "families",
        "deceased",
        "formation",
        "welcome",
        "education",
        "lenten",
        "university",
        "club",
        "program",
        "service",
        "services",
        "parishioners",
        "dinner",
        "divine",
        "bulletin",
        "bible",
        "road",
        "retreat",
        "care",
        "join",
        "ladies",
        "health",
        "class",
        "classes",
        "living",
        "sacramental",
        "social",
        "study",
        "baptisms",
        "english",
        "spanish",
        "baby",
        "need",
        "cemetery",
        "good",
        "team",
        "county",
        "upcoming",
        "book",
        "avenue",
        "liturgical",
        "stewardship",
        "hospitality",
        "raffle",
        "liturgy",
        "gifts",
        "events",
        "american",
        "parochial",
        "college",
        "financial",
        "support",
        "national",
        "banns",
        "confessions",
        "help",
        "association",
        "training",
        "eucharist",
        "senior",
        "grade",
        "south",
        "north",
        "eternal",
        "phone",
        "child",
        "conference",
        "heart",
        "project",
        "guild",
        "outreach",
        "student",
        "students",
        "baptismal",
        "today",
        "staff",
        "bread",
        "nursing",
        "event",
        "campus",
        "scholarship",
        "scripture",
        "death",
        "perpetual",
        "rehearsal",
        "blvd",
        "extraordinary",
        "recently",
        "central",
        "lunch",
        "gathering",
        "abuse",
        "weddings",
        "vocations",
        "commission",
        "party",
        "general",
        "golf",
        "order",
        "tickets",
        "blessings",
        "clergy",
        "scout",
        "coffee",
        "req",
        "god",
        "christ",
        "wedding",
        "pastoral",
        "diocesan",
        "intentions",
        "feb",
        "mar",
        "jun",
        "jul",
        "aug",
        "sep",
        "oct",
        "nov",
        "dec",
        "tues",
        "thurs",
        "ave",
        "mrs",
        "rev",
        "ext",
        "padre",
        "familia",
        "por",
        "los",
        "san",
        "santa",
        "santo",
        "salad",
        "pasta",
        "pizza",
        "chicken",
        "sausage",
        "pork",
        "beef",
        "taco",
        "tamale",
        "tamales",
        "donut",
        "doughnut",
        "popcorn",
        "chocolate",
        "cocoa",
        "dessert",
        "recipe",
        "catering",
        "menu",
        "insurance",
        "attorney",
        "realtor",
        "plumbing",
        "roofing",
        "heating",
        "cooling",
        "conditioning",
        "dental",
        "pharmacy",
        "flooring",
        "carpet",
        "landscaping",
        "towing",
        "electrician",
        "remodeling",
        "locksmith",
        "furnace",
        "discount",
        "coupon",
        "adobe",
        "acrobat",
        "download",
        "facebook",
        "instagram",
        "twitter",
        "phishing",
        "scam",
        "flocknote",
        "myparish",
        "donation",
        "donations",
        "contribution",
        "statement",
        "statements",
        "suggested",
        "connected",
        "handbook",
        "brochure",
        "quartet",
        "fiesta",
        "session",
        "sessions",
        "requested",
        "strips",
        "rates",
        "repair",
        "alert",
        "serving",
        "expert",
        "experts",
        # Additional bulletin-specific words
        "altar",
        "thank",
        "father",
        "cathedral",
        "basilica",
        "shrine",
        "academy",
        "newsletter",
        "announcement",
        "offering",
        "tithing",
        "catechesis",
        "celebration",
        "mission",
        "pilgrimage",
        "enrollment",
        # Spanish bulletin section headers / phrases (bilingual parishes)
        "aviso",
        "importante",
        "intenciones",
        "misa",
        "vivir",
        "liturgia",
        "cuarto",
        "registro",
        "natividad",
        "senor",
        "nuevo",
        "convocacion",
        "misiones",
        "palms",
        "distributed",
        "foundations",
        "citizen",
        "candle",
        "santuary",
        "sanctuary",
        "bridge",
    }
    return _non_name_words_cache


def clean_extracted_name(name: str) -> str:
    """Clean common artifacts from extracted names before validation.

    Called at every extraction point BEFORE is_valid_name(). The cleaned
    name is what gets stored, so "Jose Gamboa Wed" becomes "Jose Gamboa".
    """
    if not name:
        return name
    # Keep only first line (newlines are PDF column bleed artifacts)
    if "\n" in name:
        name = name.split("\n")[0]
    name = name.strip()
    # Collapse multiple spaces
    name = re.sub(r"\s+", " ", name)
    # Strip trailing day abbreviations (PDF column bleed from mass schedules)
    day_abbrevs = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
    parts = name.split()
    while parts and parts[-1] in day_abbrevs:
        parts.pop()
    # Strip trailing ministry role words (PDF column bleed from schedule grids)
    # e.g. "Richard Martinez Lector" -> "Richard Martinez"
    trailing_roles = {
        "Lector",
        "Lectors",
        "Usher",
        "Ushers",
        "Sacristan",
        "Sacristans",
        "Presider",
        "Cantor",
        "Cantors",
        "Greeters",
        "Greeter",
        "Server",
        "Servers",
        "Reader",
        "Readers",
        "Minister",
        "Ministers",
        "Eucharist",
        "Ext",
        "Req",
        "Deceased",
        "God",
    }
    while parts and parts[-1] in trailing_roles:
        parts.pop()
    # Strip trailing 'All' (from "Edwin Arthur All")
    if parts and parts[-1] == "All":
        parts.pop()
    # Strip trailing contact words (from "Jessica Siemen Fax", "Joseph Keough Email")
    contact_suffixes = {"Email", "email", "Fax", "fax", "Phone", "phone"}
    while parts and parts[-1] in contact_suffixes:
        parts.pop()
    name = " ".join(parts)
    # Strip leading/trailing punctuation
    name = name.strip(".,;:!?()[]{}\"'-/")
    # Split merged names (e.g. "Kevin Steinkamp Cynthia" -> "Kevin Steinkamp")
    name = split_merged_name(name)
    return name


def is_valid_name(name: str) -> bool:
    """
    Check if a string looks like a real person's name.

    Validates that the string has 2-4 properly capitalized words,
    is not a known false positive, and doesn't contain common
    non-name words (church terminology, days, months, etc.).

    Also rejects truncated/fragmented names from PDF column bleed
    (e.g. "Teresa Mu", "Carlos Ze", "Kathleen Shils").

    All-caps phrases (e.g. "AVISO IMPORTANTE", "HOLY ASSUMPTION") are
    rejected — these are bulletin section headers, not person names.
    """
    if not name or len(name) < 4:
        return False

    # Reject names containing newlines (always PDF column bleed artifacts)
    if "\n" in name:
        return False

    # Must have at least a first and last name
    parts = name.split()
    if len(parts) < 2 or len(parts) > 4:
        return False

    # Reject fully all-caps phrases — these are section headers/titles,
    # not person names. Strip any known title prefix first so that
    # "FR. JOHN SMITH" is evaluated on "JOHN SMITH" (still all-caps → rejected).
    _title_strip = re.compile(
        r"^(Fr\.|Rev\.|Deacon|Father|Msgr\.|Sr\.|Sister|Bishop|Dcn\.|Br\.)\s+",
        re.IGNORECASE,
    )
    name_without_title = _title_strip.sub("", name).strip()
    if name_without_title == name_without_title.upper() and len(name_without_title) > 3:
        return False

    # Each part should be capitalized
    for part in parts:
        if not part[0].isupper():
            return False
        # Reject all-caps (except 1-2 letter abbreviations like initials)
        if part.isupper() and len(part) > 2:
            return False
        # Reject merged PDF artifacts (words >15 chars are never real name parts)
        if len(part) > 15:
            return False

    # Check against false positives blocklist
    if name in FALSE_POSITIVE_NAMES:
        return False

    # Reject truncated names: last word should be at least 3 chars
    # (catches PDF column bleed like "Teresa Mu", "Carlos Ze", "Reynaldo Ro")
    # Exception: middle initials are OK (single letter + optional period)
    if len(parts[-1]) < 3 and not re.match(r"^[A-Z]\.?$", parts[-1]):
        return False

    # Reject if first name is too short (catches "Fr Mike" without the period)
    if len(parts[0]) < 2:
        return False

    # Reject if any part is a common non-name word.
    # NOTE: Comparison is case-INSENSITIVE — we lowercase both sides.
    # All entries should be stored lowercase in this set.
    non_name_words = {
        "the",
        "and",
        "for",
        "from",
        "with",
        "that",
        "this",
        "are",
        "was",
        "has",
        "have",
        "had",
        "their",
        "there",
        "where",
        "when",
        "what",
        "which",
        "also",
        "than",
        "them",
        "not",
        "out",
        "who",
        "how",
        "its",
        "may",
        "can",
        "you",
        "your",
        "church",
        "parish",
        "school",
        "center",
        "hall",
        "room",
        "chapel",
        "sunday",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "january",
        "february",
        "march",
        "april",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "mass",
        "masses",
        "confession",
        "communion",
        "lent",
        "easter",
        "christmas",
        "daily",
        "weekly",
        "monthly",
        "annual",
        "weekday",
        "weekend",
        "morning",
        "evening",
        "night",
        "table",
        "prayer",
        "music",
        "director",
        "ministers",
        "eucharistic",
        "servers",
        "rentals",
        "maintenance",
        "hour",
        "holy",
        "blessed",
        "sacred",
        "saint",
        "our",
        "rite",
        "catholic",
        "initiation",
        "adults",
        "stations",
        "cross",
        "rosary",
        "adoration",
        "benediction",
        "tree",
        "garden",
        "office",
        "online",
        "giving",
        "live",
        "stream",
        "please",
        "contact",
        "call",
        "email",
        "visit",
        "registration",
        "information",
        "schedule",
        "calendar",
        "baptism",
        "confirmation",
        "marriage",
        "funeral",
        "anointing",
        "communion",
        "collection",
        "offertory",
        "budget",
        "total",
        "choir",
        "band",
        "ensemble",
        "group",
        "youth",
        "children",
        "family",
        "women",
        "men",
        "deacon",
        "priest",
        "bishop",
        "pastor",
        "vicar",
        # Spanish words that appear in bilingual bulletins
        "domingo",
        "tiempo",
        "ordinario",
        "semana",
        "consejo",
        "matrimonial",
        # Organizational terms that look like names
        "president",
        "vice",
        "chairman",
        "secretary",
        "treasurer",
        "members",
        "lectors",
        "counters",
        "volunteer",
        "principal",
        "coordinator",
        "gift",
        "shop",
        "sick",
        "opportunity",
        "invitation",
        "tech",
        "degree",
        "exemplification",
        "assembly",
        "mtg",
        "chaplet",
        "spiritual",
        "dcn",
        "alone",
        # Event/activity words
        "activities",
        "picnic",
        "proceeds",
        "novena",
        "drive",
        "sale",
        "bake",
        "pancake",
        "fry",
        "pantry",
        "store",
        "bank",
        "kitchen",
        "shawl",
        "food",
        "soup",
        "thrift",
        # Time/calendar words
        "day",
        "year",
        "time",
        "new",
        "old",
        "first",
        "second",
        "labor",
        "memorial",
        "thanksgiving",
        "happy",
        "jubilee",
        # Religious terms missing from current list
        "gospel",
        "amen",
        "ordinary",
        "heavenly",
        "immaculate",
        "conception",
        "souls",
        "saints",
        "departed",
        "reconciliation",
        "testament",
        "sacrament",
        "intention",
        "special",
        # Organizational terms
        "manager",
        "property",
        "development",
        "finance",
        "liaison",
        "bookkeeper",
        "case",
        "assistant",
        "needed",
        "volunteers",
        "members",
        "council",
        "meeting",
        "fund",
        "retirement",
        "environment",
        "safe",
        # Location/structural
        "street",
        "address",
        "mailing",
        "place",
        "house",
        "open",
        "sign",
        "reading",
        "word",
        "river",
        "america",
        "latin",
        # Misc
        "ad",
        "dear",
        "high",
        "middle",
        "pro",
        "right",
        "respect",
        "cardinal",
        "pope",
        "doctor",
        "anniversary",
        "feast",
        "world",
        "society",
        "news",
        "knight",
        "knights",
        "life",
        "active",
        "ministries",
        "ministry",
        "cultural",
        "community",
        "ushers",
        "wheelchair",
        "disciples",
        "committee",
        "basketball",
        # Bulletin vocabulary surfaced by frequency analysis
        "lector",
        "usher",
        "sacristan",
        "presider",
        "cantor",
        "greeters",
        "greeter",
        "server",
        "reader",
        "families",
        "deceased",
        "formation",
        "welcome",
        "education",
        "lenten",
        "university",
        "club",
        "program",
        "service",
        "services",
        "parishioners",
        "dinner",
        "divine",
        "bulletin",
        "bible",
        "road",
        "retreat",
        "care",
        "join",
        "ladies",
        "health",
        "class",
        "classes",
        "living",
        "sacramental",
        "social",
        "study",
        "baptisms",
        "english",
        "spanish",
        "baby",
        "need",
        "cemetery",
        "good",
        "team",
        "county",
        "upcoming",
        "book",
        "avenue",
        "liturgical",
        "stewardship",
        "hospitality",
        "raffle",
        "liturgy",
        "gifts",
        "events",
        "american",
        "parochial",
        "college",
        "financial",
        "support",
        "national",
        "banns",
        "confessions",
        "help",
        "association",
        "training",
        "eucharist",
        "senior",
        "grade",
        "south",
        "north",
        "eternal",
        "phone",
        "child",
        "conference",
        "heart",
        "project",
        "guild",
        "outreach",
        "student",
        "students",
        "baptismal",
        "today",
        "staff",
        "bread",
        "nursing",
        "event",
        "campus",
        "scholarship",
        "scripture",
        "death",
        "perpetual",
        "rehearsal",
        "blvd",
        "extraordinary",
        "recently",
        "central",
        "lunch",
        "gathering",
        "abuse",
        "weddings",
        "vocations",
        "commission",
        "party",
        "general",
        "golf",
        "order",
        "tickets",
        "blessings",
        "clergy",
        "scout",
        "coffee",
        "req",
        "god",
        "christ",
        "wedding",
        "pastoral",
        "diocesan",
        "intentions",
        "feb",
        "mar",
        "jun",
        "jul",
        "aug",
        "sep",
        "oct",
        "nov",
        "dec",
        "tues",
        "thurs",
        "ave",
        "mrs",
        "rev",
        "ext",
        "padre",
        "familia",
        "por",
        "los",
        "san",
        "santa",
        "santo",
        # Food/drink (bulletin ads, event menus)
        "salad",
        "pasta",
        "pizza",
        "chicken",
        "sausage",
        "pork",
        "beef",
        "taco",
        "tamale",
        "tamales",
        "donut",
        "doughnut",
        "popcorn",
        "chocolate",
        "cocoa",
        "dessert",
        "recipe",
        "catering",
        "menu",
        # Commercial/business ads in bulletins
        "insurance",
        "attorney",
        "realtor",
        "plumbing",
        "roofing",
        "heating",
        "cooling",
        "conditioning",
        "dental",
        "pharmacy",
        "flooring",
        "carpet",
        "landscaping",
        "towing",
        "electrician",
        "remodeling",
        "locksmith",
        "furnace",
        "discount",
        "coupon",
        # Tech/social media
        "adobe",
        "acrobat",
        "download",
        "facebook",
        "instagram",
        "twitter",
        "phishing",
        "scam",
        "flocknote",
        "myparish",
        # Non-name actions/objects from bulletin text
        "donation",
        "donations",
        "contribution",
        "statement",
        "statements",
        "suggested",
        "connected",
        "handbook",
        "brochure",
        "quartet",
        "fiesta",
        "session",
        "sessions",
        "requested",
        "strips",
        "rates",
        "repair",
        "alert",
        "serving",
        "expert",
        "experts",
        # Additional bulletin-specific words
        "altar",
        "thank",
        "father",
        "cathedral",
        "basilica",
        "shrine",
        "academy",
        "newsletter",
        "announcement",
        "offering",
        "tithing",
        "catechesis",
        "celebration",
        "mission",
        "pilgrimage",
        "enrollment",
        # Spanish bulletin section headers / phrases (bilingual parishes)
        "aviso",
        "importante",
        "intenciones",
        "misa",
        "vivir",
        "liturgia",
        "cuarto",
        "domingo",
        "registro",
        "natividad",
        "senor",
        "nuevo",
        "convocacion",
        "misiones",
        "palms",
        "distributed",
        "foundations",
        "citizen",
        "candle",
        "santuary",
        "sanctuary",
        "bridge",
        # Contact/address artifacts
        "fax",
        "residence",
        # Organization/phrase words from top false positives
        "press",
        "institute",
        "court",
        "people",
        "missal",
        "topsoil",
        "mulch",
        "app",
        "every",
        "each",
    }
    if any(p.lower() in non_name_words for p in parts):
        return False

    # Reject if first word is a known non-name starter (case-insensitive)
    non_name_starters = {"the", "are", "of", "or", "by", "at", "in", "to", "on", "an", "if"}
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
    with open(jsonl_path, encoding="utf-8") as f:
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
                if url and any(
                    x in url.lower()
                    for x in ["diocese", "archdiocese", "/parish-finder", "/parishfinder"]
                ):
                    continue

                slug = church.get("slug", "")
                churches.append(
                    {
                        "name": church.get("name", "Unknown"),
                        "city": church.get("city", ""),
                        "state": church.get("state", ""),
                        "slug": slug,
                        "url": url,
                        "raw_url": raw_url,
                    }
                )
            except json.JSONDecodeError:
                continue

    if limit > 0:
        churches = churches[:limit]

    return churches


def load_progress(state_dir: Path):
    """Load progress tracking file."""
    progress_path = state_dir / "bulletin_progress.json"
    if progress_path.exists():
        with open(progress_path, encoding="utf-8") as f:
            return json.load(f)
    return {"discovered": {}, "downloaded": {}, "extracted": {}}


def save_progress(state_dir: Path, progress: dict):
    """Save progress tracking file."""
    progress_path = state_dir / "bulletin_progress.json"
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2)


def run_discover(
    state_name: str, state_dir: Path, churches: list, progress: dict, resume: bool = False
):
    """Phase 1: Discover bulletin pages for all churches."""
    logger.info(
        f"=== Phase 1: DISCOVER bulletin pages for {state_name} ({len(churches)} churches) ==="
    )

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
                # Strategy 5: Try DiscoverMass as a fallback
                state_code = church.get("state", "")
                dm_result = try_discovermass_fallback(church["name"], church["city"], state_code)
                if dm_result and dm_result["pdf_urls"]:
                    found_count += 1
                    discovered[slug] = {
                        "status": "found",
                        "bulletin_page": dm_result["bulletin_page_url"],
                        "pdfs": dm_result["pdf_urls"],
                        "source": dm_result["source"],
                        "lpi_parish_id": None,
                        "church_name": church["name"],
                        "church_url": url,
                    }
                    pdf_count = len(dm_result["pdf_urls"])
                    logger.info(f"  [DM] DiscoverMass fallback: {pdf_count} PDFs")
                else:
                    discovered[slug] = {
                        "status": "not_found",
                        "bulletin_page": result.get("bulletin_page_url"),
                        "pdfs": [],
                        "church_name": church["name"],
                        "church_url": url,
                    }
                    logger.info("  [--] No bulletin found")
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
    logger.info(
        f"\n=== Discovery complete: {found_count}/{total_active} churches have bulletins ==="
    )
    logger.info(f"Results saved to {discovery_path}")

    # Log churches with more than MAX_PDFS_PER_CHURCH PDFs (for future full collection)
    over_limit = {
        slug: info
        for slug, info in discovered.items()
        if len(info.get("pdfs", [])) > MAX_PDFS_PER_CHURCH
    }
    if over_limit:
        logger.info(
            f"\n[!] {len(over_limit)} churches have >{MAX_PDFS_PER_CHURCH} PDFs (capped at {MAX_PDFS_PER_CHURCH}):"
        )
        for slug, info in sorted(over_limit.items(), key=lambda x: -len(x[1].get("pdfs", []))):
            logger.info(f"  {info.get('church_name', slug)}: {len(info['pdfs'])} PDFs total")

    return discovered


def run_download(
    state_name: str, state_dir: Path, discovered: dict, progress: dict, resume: bool = False
):
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
                capped_churches.append(
                    {
                        "slug": slug,
                        "church_name": info.get("church_name", slug),
                        "total_pdfs_available": len(all_pdfs),
                        "pdfs_downloaded": MAX_PDFS_PER_CHURCH,
                        "remaining_pdfs": len(all_pdfs) - MAX_PDFS_PER_CHURCH,
                    }
                )
            continue

        all_pdfs = info["pdfs"]
        pdfs = all_pdfs[:MAX_PDFS_PER_CHURCH]
        total_pdfs += len(pdfs)
        church_name = info.get("church_name", slug)

        # Flag if this church hit the cap
        if len(all_pdfs) >= MAX_PDFS_PER_CHURCH:
            capped_churches.append(
                {
                    "slug": slug,
                    "church_name": church_name,
                    "total_pdfs_available": len(all_pdfs),
                    "pdfs_downloaded": len(pdfs),
                    "remaining_pdfs": len(all_pdfs) - len(pdfs),
                }
            )
            logger.info(
                f"  [CAP] {church_name}: {len(all_pdfs)} PDFs available, downloading {len(pdfs)} (capped at {MAX_PDFS_PER_CHURCH})"
            )

        slug_safe = re.sub(r"[^a-zA-Z0-9_-]", "_", slug)
        church_downloads = []

        for pdf_url in pdfs:
            path = download_bulletin_pdf(pdf_url, bulletin_dir, slug_safe)
            if path:
                church_downloads.append(
                    {
                        "url": pdf_url,
                        "local_path": str(path),
                        "size_bytes": path.stat().st_size,
                    }
                )
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
            logger.info(
                f"  {c['church_name']}: {c['total_pdfs_available']} available, {c['remaining_pdfs']} remaining"
            )
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
        with open(discovery_path, encoding="utf-8") as f:
            discovery_data = json.load(f)

    # Build slug→city mapping from church_details.jsonl
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

    # Confidence mapping (computed via confidence_label()):
    # HIGH = clearly a named individual (staff, parishioner mentioned by name, someone prayed for)
    # MEDIUM = likely a real name but found via looser contextual matching
    # LOW = might be a false positive (generic text near a ministry keyword)

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

            # Extract text (column-aware to prevent cross-column merging)
            text, column_texts = extract_text_from_pdf(pdf_path)
            if not text:
                logger.debug(f"  No text extracted from {pdf_path.name}")
                continue

            # Save extracted text
            text_path = text_dir / (pdf_path.stem + ".txt")
            text_path.write_text(text, encoding="utf-8")

            # Extract names from each column separately to prevent
            # cross-column name merging (e.g., "John Smith Jane Doe"
            # when "John Smith" is in col 1 and "Jane Doe" in col 2)
            names = []
            for col_text in column_texts:
                col_names = extract_names_from_text(col_text, church_name)
                names.extend(col_names)

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
                    cat = name_info["category"]
                    n_title = name_info.get("title", "")
                    n_role = name_info.get("role", "")
                    conf_score = score_name_confidence(
                        person_name, category=cat, role=n_role, title=n_title
                    )
                    all_names.append(
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
                            "confidence": confidence_label(conf_score),
                            "confidence_score": f"{conf_score:.2f}",
                            "context": name_info["context"][:100],
                        }
                    )

        if church_name_count > 0:
            churches_with_names += 1
            logger.info(f"  {church_name}: {church_name_count} unique names found")

    # Save names to CSV
    csv_path = state_dir / "bulletin_names.csv"
    if all_names:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
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
                ],
            )
            writer.writeheader()
            writer.writerows(all_names)

    # Also save as JSON for easier programmatic access
    json_path = state_dir / "bulletin_names.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_names, f, indent=2)

    logger.info("\n=== Extraction complete ===")
    logger.info(f"  {total_names} unique names (per-church) from {churches_with_names} churches")
    logger.info(f"  CSV: {csv_path}")
    logger.info(f"  JSON: {json_path}")

    progress["extracted"] = {
        "total_names": total_names,
        "churches_with_names": churches_with_names,
        "csv_path": str(csv_path),
    }
    save_progress(state_dir, progress)


def run_clean(state_name: str, state_dir: Path):
    """Re-clean existing extracted names using updated cleaning/validation logic.

    Reads bulletin_names.json, applies clean_extracted_name() + is_valid_name()
    filters, and rewrites both CSV and JSON. Use after updating blocklists or
    cleaning rules to fix existing data without re-extracting from PDFs.

    Usage: python run_bulletin_scraper.py clean arizona florida georgia
    """
    json_path = state_dir / "bulletin_names.json"
    if not json_path.exists():
        logger.error(f"No bulletin_names.json found for {state_name}. Run 'extract' phase first.")
        return

    with open(json_path, encoding="utf-8") as f:
        all_names = json.load(f)

    original_count = len(all_names)
    cleaned_names = []
    removed_count = 0

    # Track unique names per church (same dedup logic as run_extract)
    church_seen_names = {}  # slug -> set of lowercased names

    for entry in all_names:
        person_name = entry.get("person_name", "")
        slug = entry.get("church_slug", "")

        # Apply new cleaning
        cleaned = clean_extracted_name(person_name)
        if not cleaned:
            removed_count += 1
            continue

        # Re-validate with updated blocklists
        if not is_valid_name(cleaned):
            removed_count += 1
            continue

        # Per-church dedup
        name_key = cleaned.lower().strip()
        if slug not in church_seen_names:
            church_seen_names[slug] = set()
        if name_key in church_seen_names[slug]:
            removed_count += 1
            continue
        church_seen_names[slug].add(name_key)

        # Update the entry with the cleaned name
        if cleaned != person_name:
            entry["person_name"] = cleaned
            # Re-parse name parts
            name_parts = parse_name_parts(cleaned)
            entry["first_name"] = name_parts.get("first_name", "")
            entry["middle_name"] = name_parts.get("middle_name", "")
            entry["last_name"] = name_parts.get("last_name", "")
            entry["title"] = name_parts.get("title", "")

        # Score the name
        conf_score = score_name_confidence(
            entry["person_name"],
            category=entry.get("category", ""),
            role=entry.get("role", ""),
            title=entry.get("title", ""),
        )
        entry["confidence_score"] = f"{conf_score:.2f}"
        entry["confidence"] = confidence_label(conf_score)

        cleaned_names.append(entry)

    # Write cleaned CSV
    csv_path = state_dir / "bulletin_names.csv"
    if cleaned_names:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
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
                ],
            )
            writer.writeheader()
            writer.writerows(cleaned_names)

    # Write cleaned JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(cleaned_names, f, indent=2)

    pct = (removed_count / original_count * 100) if original_count > 0 else 0
    logger.info(f"\n=== Clean complete for {state_name} ===")
    logger.info(f"  Before: {original_count:,} names")
    logger.info(f"  Removed: {removed_count:,} ({pct:.1f}%)")
    logger.info(f"  After: {len(cleaned_names):,} names")
    logger.info(f"  CSV: {csv_path}")
    logger.info(f"  JSON: {json_path}")


# ── CLI Entry Point ───────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Scrape church bulletins, download PDFs, and extract names"
    )
    parser.add_argument(
        "phase",
        choices=["discover", "download", "extract", "clean", "all"],
        help="Which phase to run",
    )
    parser.add_argument("states", nargs="+", help="State names or 'all' for all states")
    parser.add_argument(
        "--limit", type=int, default=0, help="Limit number of churches to process (for testing)"
    )
    parser.add_argument("--resume", action="store_true", help="Resume from where we left off")
    parser.add_argument(
        "--retry-no-url",
        action="store_true",
        help=(
            "Re-check churches that were previously skipped because they "
            "had no resolved URL (status='no_url' in progress). Use after "
            "running URL resolver --retry-failed to pick up newly resolved "
            "URLs. Implies --resume for all other churches."
        ),
    )
    parser.add_argument(
        "--retry-no-pdfs",
        action="store_true",
        help=(
            "Re-run discovery ONLY for churches that were found but got 0 PDFs "
            "(e.g. LPi widgets, eCatholic sites that need a headless browser). "
            "Implies --resume for churches that already have PDFs. "
            "After discovery, automatically runs download + extract phases."
        ),
    )

    args = parser.parse_args()
    # --retry-no-url implies --resume (keep existing progress, just retry no_url ones)
    if args.retry_no_url:
        args.resume = True
    # --retry-no-pdfs implies --resume and forces all phases
    if args.retry_no_pdfs:
        args.resume = True
        if args.phase == "discover":
            args.phase = "all"  # auto-run full pipeline after rediscovery

    # Resolve state names
    if "all" in args.states:
        state_dirs = sorted(
            [
                d
                for d in OUTPUT_DIR.iterdir()
                if d.is_dir() and (d / "church_details.jsonl").exists()
            ]
        )
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

        # Clean phase: just re-filter existing data, no need for churches/progress
        if args.phase == "clean":
            run_clean(state_name, state_dir)
            continue

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

        # If --retry-no-url, remove "no_url" entries from discovered dict so they
        # get re-processed with their now-resolved URLs
        if args.retry_no_url:
            discovered_dict = progress.get("discovered", {})
            no_url_slugs = [
                slug for slug, info in discovered_dict.items() if info.get("status") == "no_url"
            ]
            for slug in no_url_slugs:
                del discovered_dict[slug]
            if no_url_slugs:
                logger.info(
                    f"Retry-no-url: removed {len(no_url_slugs)} 'no_url' entries "
                    f"from discovered dict (will re-check with fresh URLs)"
                )
                save_progress(state_dir, progress)

        # If --retry-no-pdfs, remove entries that were "found" but got 0 PDFs
        # so they get re-processed with Playwright-enabled logic
        if args.retry_no_pdfs:
            discovered_dict = progress.get("discovered", {})
            no_pdf_slugs = [
                slug
                for slug, info in discovered_dict.items()
                if info.get("status") == "found" and not info.get("pdfs")
            ]
            # Also retry "not_found" entries (might succeed with browser)
            not_found_slugs = [
                slug for slug, info in discovered_dict.items() if info.get("status") == "not_found"
            ]
            retry_slugs = no_pdf_slugs + not_found_slugs
            for slug in retry_slugs:
                del discovered_dict[slug]
            if retry_slugs:
                logger.info(
                    f"Retry-no-pdfs: removed {len(no_pdf_slugs)} 'found-but-0-pdfs' + "
                    f"{len(not_found_slugs)} 'not_found' entries from discovered dict "
                    f"(will re-check with Playwright-enabled logic)"
                )
                save_progress(state_dir, progress)

        if args.phase in ("discover", "all"):
            discovered = run_discover(
                state_name, state_dir, with_urls, progress, resume=args.resume
            )
        else:
            # Load existing discovery data
            discovery_path = state_dir / "bulletin_discovery.json"
            if discovery_path.exists():
                with open(discovery_path, encoding="utf-8") as f:
                    discovered = json.load(f)
            else:
                logger.error("No discovery data found. Run 'discover' phase first.")
                continue

        if args.phase in ("download", "all"):
            downloaded = run_download(
                state_name, state_dir, discovered, progress, resume=args.resume
            )
        else:
            downloaded = progress.get("downloaded", {})

        if args.phase in ("extract", "all"):
            run_extract(state_name, state_dir, downloaded, progress)

    # Clean up Playwright browser
    _close_playwright_browser()

    logger.info("\n=== ALL DONE ===")


if __name__ == "__main__":
    main()
