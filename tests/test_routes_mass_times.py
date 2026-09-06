"""Tests for the mass_times routes."""

import pytest

pytestmark = pytest.mark.requires_db


class TestMassTimesStateView:
    def test_returns_200(self, client):
        response = client.get("/mass-times/ohio/")
        assert response.status_code == 200

    def test_contains_church_names(self, client):
        response = client.get("/mass-times/ohio/")
        assert b"St. Mary" in response.data
        assert b"St. Joseph" in response.data

    def test_nonexistent_state_returns_404(self, client):
        response = client.get("/mass-times/nonexistent/")
        assert response.status_code == 404


class TestMassTimesChurchView:
    def test_by_slug(self, client):
        # Look the slug up rather than hardcoding one. "st-mary-columbus" was a
        # CatholicIndex-era slug and slugs are DiscoverMass-shaped now, so the
        # literal 404s — invisible while the whole suite was 302ing to /login.
        from app.data_loader import get_services

        df = get_services("ohio")
        slug = df[df["church_slug"] != ""].iloc[0]["church_slug"]
        response = client.get(f"/mass-times/ohio/church/{slug}/")
        assert response.status_code == 200

    def test_by_name_fallback(self, client):
        response = client.get("/mass-times/ohio/church/St. Mary/")
        assert response.status_code == 200

    def test_nonexistent_church_returns_404(self, client):
        response = client.get("/mass-times/ohio/church/nonexistent-slug/")
        assert response.status_code == 404

    def test_nonexistent_state_returns_404(self, client):
        response = client.get("/mass-times/nonexistent/church/st-mary-columbus/")
        assert response.status_code == 404


class TestCalendarView:
    def test_returns_200(self, client):
        response = client.get("/mass-times/ohio/calendar/")
        assert response.status_code == 200

    def test_nonexistent_state_returns_404(self, client):
        response = client.get("/mass-times/nonexistent/calendar/")
        assert response.status_code == 404


class TestCalendarDownload:
    def test_returns_csv(self, client):
        response = client.get("/mass-times/ohio/calendar/download/")
        assert response.status_code == 200
        assert response.content_type == "text/csv; charset=utf-8"

    def test_content_disposition_header(self, client):
        response = client.get("/mass-times/ohio/calendar/download/")
        disposition = response.headers.get("Content-Disposition", "")
        assert "ohio_dated_services.csv" in disposition

    def test_nonexistent_state_returns_404(self, client):
        response = client.get("/mass-times/nonexistent/calendar/download/")
        assert response.status_code == 404


class TestStaleScheduleWarning:
    """A schedule nobody refreshes any more must not read as current.

    DiscoverMass does not list ~7,187 of our churches (chapels, missions,
    seminaries, campus and hospital ministries that CatholicIndex carried), so
    their mass times have been frozen since CatholicIndex went behind
    Cloudflare in April 2026 — some as far back as 2025-09-30. Rendered
    undated, they were indistinguishable from a schedule confirmed yesterday.
    """

    def _stale_church(self, state_dir):
        from app.data_loader import get_services

        df = get_services(state_dir)
        stale = df[df["is_stale"]]
        return None if stale.empty else stale.iloc[0]

    def test_loader_marks_stale_and_fresh_rows(self):
        from app.data_loader import STALE_AFTER_DAYS, get_services

        df = get_services("new_mexico")
        assert not df.empty
        assert {"is_stale", "days_since_scrape", "last_scraped_at"} <= set(df.columns)
        # Every flagged row must actually be past the threshold (or never scraped).
        flagged = df[df["is_stale"]]
        assert not flagged.empty, "New Mexico is ~59% frozen; expected stale rows"
        assert (
            (flagged["days_since_scrape"] > STALE_AFTER_DAYS) | (flagged["days_since_scrape"] < 0)
        ).all()
        # ...and nothing recently scraped may be flagged.
        clean = df[~df["is_stale"]]
        if not clean.empty:
            assert (clean["days_since_scrape"] <= STALE_AFTER_DAYS).all()
            assert (clean["days_since_scrape"] >= 0).all()

    def test_never_scraped_is_stale_not_zero_days(self):
        """NULL last_scraped_at must mean 'never confirmed', not 'today'."""
        import pandas as pd
        from app.data_loader import STALE_AFTER_DAYS

        days = (pd.Timestamp.now().normalize() - pd.to_datetime([None])).days
        filled = pd.Series(days).fillna(-1).astype(int)
        assert filled.iloc[0] == -1
        assert ((filled > STALE_AFTER_DAYS) | (filled < 0)).iloc[0]

    def test_state_page_warns_and_badges(self, client):
        response = client.get("/mass-times/new_mexico/")
        assert response.status_code == 200
        body = response.data
        assert b"Unverified" in body
        assert (
            b"have\n    schedules that have not been confirmed" in body
            or b"schedules that have not been confirmed" in body
        )

    def test_church_page_warns_when_stale(self, client):
        row = self._stale_church("new_mexico")
        assert row is not None, "expected at least one stale NM church"
        response = client.get(f"/mass-times/new_mexico/church/{row['church_slug']}/")
        assert response.status_code == 200
        assert b"These times are unverified" in response.data

    def test_fresh_church_page_has_no_warning(self, client):
        from app.data_loader import get_services

        df = get_services("new_mexico")
        fresh = df[~df["is_stale"]]
        if fresh.empty:
            import pytest as _pytest

            _pytest.skip("no freshly-scraped NM church to contrast against")
        slug = fresh.iloc[0]["church_slug"]
        response = client.get(f"/mass-times/new_mexico/church/{slug}/")
        assert response.status_code == 200
        assert b"These times are unverified" not in response.data
