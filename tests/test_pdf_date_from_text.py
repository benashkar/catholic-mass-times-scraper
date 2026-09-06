"""Edition dates: forward-dated bulletins, and covers whose URL carries no date.

Two causes put 34% of downloaded PDFs on pdf_date NULL:

  * A bulletin is published BEFORE the weekend it covers — the 16 August
    edition goes up on the 13th. _valid() rejected anything dated after today,
    so it discarded precisely the current week's editions, the ones that answer
    "did this week's scrape work?".

  * CDN-hosted files carry no date in the URL at all (irp.cdn-website.com,
    img1.wsimg.com, bulletins.discovermass.com were the top three). The cover
    prints it, and the text is already extracted for name parsing.
"""

from datetime import UTC, datetime, timedelta

import pytest

from extract_bulletins_to_db99 import _valid, pdf_date_from_text, pdf_date_from_url


class TestForwardDatedBulletins:
    def test_next_sunday_is_accepted(self):
        """The whole point: a bulletin dated for the coming weekend."""
        soon = datetime.now(UTC) + timedelta(days=3)
        assert _valid(soon.year, soon.month, soon.day) == soon.strftime("%Y-%m-%d")

    def test_today_still_accepted(self):
        now = datetime.now(UTC)
        assert _valid(now.year, now.month, now.day) is not None

    def test_far_future_still_rejected(self):
        far = datetime.now(UTC) + timedelta(days=120)
        assert _valid(far.year, far.month, far.day) is None

    def test_prehistoric_still_rejected(self):
        assert _valid(1990, 6, 1) is None

    def test_impossible_date_still_rejected(self):
        assert _valid(2026, 2, 30) is None


class TestDateFromCoverText:
    @pytest.mark.parametrize(
        "cover,expected",
        [
            ("Twentieth Sunday in Ordinary Time | August 16, 2026", "2026-08-16"),
            ("St. Mary Parish\nAug. 16, 2026\nMass Schedule", "2026-08-16"),
            ("Bulletin for August 16th, 2026", "2026-08-16"),
            ("16 August 2026 — Parish Bulletin", "2026-08-16"),
            ("Sunday Bulletin 8/16/2026", "2026-08-16"),
            ("Weekly Bulletin 08-16-2026", "2026-08-16"),
        ],
    )
    def test_masthead_shapes(self, cover, expected):
        assert pdf_date_from_text(cover) == expected

    def test_month_name_beats_a_stray_number(self):
        cover = "Parish Office 555-1234\nAugust 16, 2026\nMass intentions 1/2/2020"
        assert pdf_date_from_text(cover) == "2026-08-16"

    def test_only_the_cover_is_searched(self):
        """Mass intentions deep in the file must not win."""
        text = "Parish Bulletin\n" + ("filler " * 900) + "January 5, 2019"
        assert pdf_date_from_text(text) is None

    def test_no_date_returns_none(self):
        assert pdf_date_from_text("Parish Bulletin\nWelcome!") is None

    def test_empty_is_safe(self):
        assert pdf_date_from_text("") is None
        assert pdf_date_from_text(None) is None

    def test_implausible_year_rejected(self):
        assert pdf_date_from_text("Bulletin for August 16, 2099") is None


class TestUrlStillWins:
    def test_url_parsing_unaffected(self):
        assert pdf_date_from_url("https://example.org/bulletins/20260816_t-168.pdf") == "2026-08-16"

    def test_dateless_cdn_url_returns_none(self):
        """These are the ones the text fallback exists to rescue."""
        assert pdf_date_from_url("https://irp.cdn-website.com/abc123/files/bulletin.pdf") is None
