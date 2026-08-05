"""Edition-date parsing from bulletin PDF URLs.

A wrong date is worse than no date: it would silently misfile an edition and
corrupt any "which weeks are we missing" answer. So the negative cases here
matter as much as the positive ones.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from extract_bulletins_to_db99 import pdf_date_from_url as f

CASES = [
    # YYYYMMDD
    ("http://x.org/23933/bulletins/20230521_t-1684641512000.pdf", "2023-05-21"),
    ("https://x.org/b/20260104.pdf", "2026-01-04"),
    # month name, URL-encoded spaces and underscores
    ("https://uploads.weconnect.com/mce/abc/Bulletin%20September%2022%202024.pdf", "2024-09-22"),
    ("http://www.sjoachim.org/uploads/1/2/5/9/125917856/05-4143_january_11_2026.pdf", "2026-01-11"),
    ("https://x.org/Bulletin-Dec-24-2023.pdf", "2023-12-24"),
    # M-D-YYYY and underscore form
    ("https://x.org/bulletins/07-20-2025-bulletin.pdf", "2025-07-20"),
    ("https://www.stceciliaboston.org/wp-content/uploads/4338_Cecilia_Bos_2_8_2026.pdf", "2026-02-08"),
    ("https://x.org/files/6%2F15%2F2025.pdf", "2025-06-15"),
    # two-digit year
    ("https://uploads.weconnect.com/mce/x/2025Bulletins/Bulletin%208-3-25.pdf", "2025-08-03"),
    ("https://sacredheartnb.org/uploads/files/fr-johnbulletins/3544-10-19-25.pdf", "2025-10-19"),
    ("https://4.files.edl.io/1eef/05/30/24/173737-82b07847.pdf", "2024-05-30"),
    # YYYY/MM upload path — day unknown, anchors to the 1st
    ("https://x.org/wp-content/uploads/2026/07/bulletin.pdf", "2026-07-01"),

    # regressions: a date must not be assembled across a path boundary
    ("https://x.org/uploads/go-x/u/74fc4216-8370-49e9-98b9-5ed342f3ab07/07-06-25-bulletin-final.pdf", "2025-07-06"),
    ("https://files.ecatholic.com/11428/documents/2026/1/1.18.26.pdf?t=1768507880000", "2026-01-18"),
    ("https://x.org/2024/03/file.pdf?ver=1771714154508", "2024-03-01"),

    ("https://irp.cdn-website.com/x/uploaded/hccc_santa-cruz_bulletin_2026_01_26-02-01.pdf.pdf", "2026-01-26"),
    ("https://x.org/bulletin-2025-07-20.pdf", "2025-07-20"),

    # YYMMDD in filename, trusted only because it agrees with the upload path
    ("https://x.org/chapel/wp-content/uploads/sites/6/2023/11/31353_231126-1.pdf", "2023-11-26"),
    ("https://x.org/chapel/wp-content/uploads/sites/6/2025/06/31353_250615.pdf", "2025-06-15"),
    # 6-digit run that does NOT agree with the path is an id -> fall back to the 1st
    ("https://x.org/uploads/2024/03/99887766-doc.pdf", "2024-03-01"),

    # --- must stay None ---
    ("https://x.org/bulletin/latest.pdf", None),
    ("https://x.org/b/20991231_x.pdf", None),                      # future
    ("https://x.org/b/19700101.pdf", None),                        # pre-web
    ("https://www.fataonline.com/bulletin/bulletin-981771448616.pdf", None),   # opaque id
    ("https://bulletins.discovermass.com/download.php?bulletin=UYqEln3fxevG", None),
    ("https://irp.cdn-website.com/d5cb6a28/files/uploaded/ccd1020.pdf", None), # no year
    ("https://x.org/b/20230230.pdf", None),                        # Feb 30 is not a date
    ("https://x.org/b/13-45-2024.pdf", None),                      # month 13
    ("", None),
    (None, None),
]


def main():
    fails = []
    for url, want in CASES:
        got = f(url)
        ok = got == want
        if not ok:
            fails.append((url, want, got))
        print(f"  {'[OK] ' if ok else '[FAIL]'} {str(url)[:66]:66} -> {got}")
    print()
    if fails:
        print(f"{len(fails)} FAILED:")
        for url, want, got in fails:
            print(f"  {url}\n    want={want} got={got}")
        return 1
    print(f"ALL {len(CASES)} PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
