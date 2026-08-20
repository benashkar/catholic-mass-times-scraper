"""Can a browser resolve the masstimes /api/out redirect to a real church site?

522 zero-PDF churches nationally (CA 366, TX 129, NY 106, AZ 100, PA 87) have a
website_url of the form `/api/out?id=us-<st>-<city>-<name>-<hash>&type=w` — a
relative link that was never resolved when it was scraped. Fetching it plainly
returns an AngularJS shell: masstimes.org resolves the destination CLIENT-side,
so requests sees no redirect and no target.

We already run headless Chromium for walled hosts. This tests whether the same
tool recovers these URLs, before any repair is built on the assumption.

Read-only. Usage (Render one-off):
    python -u diag_masstimes_out.py
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _readback import publish  # noqa: E402

IDS = [
    "us-al-albertville-chapel-of-the-holy-cross-75f0cae6",
    "us-al-albertville-st-william-catholic-church-74540394",
    "us-al-auburn-blessed-pier-giorgio-frassati-chapel-catholic-student-center-0f0b8ff5",
]


def main():
    import run_bulletin_scraper as R

    lines = [
        "masstimes /api/out — can a browser resolve it?",
        f"playwright={R.HAS_PLAYWRIGHT} proxy={bool(R._playwright_proxy())}",
        "",
    ]
    publish("diag_masstimes_out", "\n".join(lines))

    if not R.HAS_PLAYWRIGHT:
        lines.append("no playwright available")
    else:
        from playwright.sync_api import sync_playwright

        for i in IDS:
            url = f"https://masstimes.org/api/out?id={i}&type=w"
            try:
                with sync_playwright() as p:
                    b = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
                    try:
                        pg = b.new_page()
                        pg.goto(url, timeout=45000, wait_until="domcontentloaded")
                        pg.wait_for_timeout(6000)   # let the SPA do its redirect
                        final = pg.url
                        lines.append(f"{i[:52]}\n    final: {final[:110]}")
                    finally:
                        b.close()
            except Exception as e:
                lines.append(f"{i[:52]}\n    EXC {type(e).__name__}: {str(e)[:70]}")
            publish("diag_masstimes_out", "\n".join(lines))

    out = "\n".join(lines)
    print(out, flush=True)
    publish("diag_masstimes_out", out)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        tb = "diag_masstimes_out FAILED\n" + traceback.format_exc()
        print(tb, flush=True)
        publish("diag_masstimes_out", tb)
        sys.exit(1)
