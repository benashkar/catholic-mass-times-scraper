"""Do Maine's walled hosts refuse our CLIENT rather than our ADDRESS?

Maine sits at 6.4% coverage. 185 of its 205 zero-PDF churches are on hosts
labelled blocked, and those verdicts are sound: every one records direct 403
AND proxy 403 — real HTTP responses on both legs, not the 407/ProxyError
signature of our own account failing. So they were measured correctly and a
proxy will not move them.

But the shape is suspicious. Dozens of unrelated domains — hc-catholics.org,
portlanddiocese.net, sjvcatholics.org, theppb.org, cluster30.org — all
answering with the same 403 looks like one diocesan platform behind one WAF.
And a WAF that 403s a residential IP is usually filtering on how the client
looks, not where it comes from. Our probes are python-requests; a real browser
is a different proposition.

That is worth exactly one test, because if a headless browser gets through it is
~190 churches in Maine alone and the same platform likely recurs elsewhere. The
existing note that WI's Cloudflare walls need a solver is about Cloudflare
specifically — this may be a different vendor.

Read-only: fetches pages, writes nothing.

Usage (Render one-off):
    python -u diag_browser_vs_requests.py
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests  # noqa: E402

from _readback import publish  # noqa: E402

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DEFAULT_HOSTS = [
    "https://hc-catholics.org",
    "https://www.portlanddiocese.net",
    "https://www.sjvcatholics.org",
    "https://www.theppb.org",
    "https://www.allsaintsmaine.com",
]

# Override with --hosts so the same matrix can be pointed at another state's
# walls. The Maine result (403/403/403 but 200 on browser+proxy) needs testing
# against the Wisconsin hosts that defeated the recovery, because those are the
# ones still holding the originally-reported churches at zero.
HOSTS = DEFAULT_HOSTS


def via_requests(url, proxies=None):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=40, proxies=proxies)
        return f"{r.status_code} ({len(r.content)}b)"
    except Exception as e:
        return f"EXC {type(e).__name__}"


def _proxy_for_playwright():
    """Split PROXY_URL into Playwright's server/username/password form."""
    raw = os.environ.get("PROXY_URL", "").strip()
    if not raw:
        return None
    from urllib.parse import urlparse

    u = urlparse(raw)
    if not u.hostname:
        return None
    server = f"{u.scheme or 'http'}://{u.hostname}:{u.port or 80}"
    cfg = {"server": server}
    if u.username:
        cfg["username"] = u.username
        cfg["password"] = u.password or ""
    return cfg


def via_browser(url, use_proxy=False):
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        return f"no playwright ({type(e).__name__})"
    launch_kw = {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    if use_proxy:
        cfg = _proxy_for_playwright()
        if not cfg:
            return "no PROXY_URL"
        launch_kw["proxy"] = cfg
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(**launch_kw)
            try:
                page = b.new_page(user_agent=UA)
                resp = page.goto(url, timeout=45000, wait_until="domcontentloaded")
                status = resp.status if resp else "no-response"
                body = page.content() or ""
                return f"{status} ({len(body)}b)"
            finally:
                b.close()
    except Exception as e:
        return f"EXC {type(e).__name__}: {str(e)[:60]}"


def main():
    global HOSTS
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--hosts", default="", help="Comma-separated URLs to test")
    ap.add_argument("--label", default="Maine walled hosts")
    args = ap.parse_args()
    if args.hosts:
        HOSTS = [h.strip() for h in args.hosts.split(",") if h.strip()]

    proxy = os.environ.get("PROXY_URL", "").strip() or None
    proxies = {"http": proxy, "https": proxy} if proxy else None

    lines = [
        f"{args.label} — client fingerprint vs address",
        f"PROXY_URL set: {bool(proxy)}",
        "",
        f"{'host':30} {'requests':>13} {'req+proxy':>13} {'browser':>13} {'brow+proxy':>14}",
        "-" * 90,
    ]
    for url in HOSTS:
        r1 = via_requests(url)
        r2 = via_requests(url, proxies) if proxies else "-"
        r3 = via_browser(url)
        # The only untested cell. Neither a residential address nor a real
        # browser works alone; some WAFs score several signals and want both.
        r4 = via_browser(url, use_proxy=True) if proxies else "-"
        host = url.replace("https://", "")[:28]
        lines.append(f"{host:30} {r1:>13} {r2:>13} {r3:>13} {r4:>14}")
        # Publish as we go: the browser step is the one that might be killed.
        publish("diag_browser_vs_requests", "\n".join(lines))

    lines.append("")
    lines.append(
        "If browser >= 200 while requests is 403, the wall is on the client and a "
        "browser-backed fetch unlocks ~190 ME churches plus whatever else shares "
        "this platform."
    )
    out = "\n".join(lines)
    print(out, flush=True)
    publish("diag_browser_vs_requests", out)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        tb = "diag_browser_vs_requests FAILED\n" + traceback.format_exc()
        print(tb, flush=True)
        publish("diag_browser_vs_requests", tb)
        sys.exit(1)
