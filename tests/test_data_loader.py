"""Tests for dashboard/app/data_loader.py — all public functions."""

import os

import pandas as pd
import pytest

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "data_output")

pytestmark = pytest.mark.requires_db


@pytest.fixture(autouse=True)
def _setup_data_dir(app):
    """Ensure DATA_DIR and module globals are initialized via the app fixture."""
    pass


class TestGetStates:
    def test_returns_list(self):
        from app.data_loader import get_states

        states = get_states()
        assert isinstance(states, list)

    def test_contains_ohio(self):
        from app.data_loader import get_states

        states = get_states()
        ohio = [s for s in states if s["state_dir"] == "ohio"]
        assert len(ohio) == 1

    def test_ohio_church_count(self):
        from app.data_loader import get_states

        states = get_states()
        ohio = [s for s in states if s["state_dir"] == "ohio"][0]
        assert ohio["church_count"] == 3

    def test_ohio_display_name(self):
        from app.data_loader import get_states

        states = get_states()
        ohio = [s for s in states if s["state_dir"] == "ohio"][0]
        assert ohio["display_name"] == "Ohio"

    def test_ohio_has_bulletin(self):
        from app.data_loader import get_states

        states = get_states()
        ohio = [s for s in states if s["state_dir"] == "ohio"][0]
        assert ohio["has_bulletin"] is True

    def test_ohio_has_services(self):
        from app.data_loader import get_states

        states = get_states()
        ohio = [s for s in states if s["state_dir"] == "ohio"][0]
        assert ohio["has_services"] is True


class TestGetChurchesForState:
    def test_returns_dataframe(self):
        from app.data_loader import get_churches_for_state

        df = get_churches_for_state("ohio")
        assert isinstance(df, pd.DataFrame)

    def test_ohio_has_three_churches(self):
        from app.data_loader import get_churches_for_state

        df = get_churches_for_state("ohio")
        assert len(df) == 3

    def test_church_names(self):
        from app.data_loader import get_churches_for_state

        df = get_churches_for_state("ohio")
        names = set(df["name"].tolist())
        assert "St. Mary" in names
        assert "St. Joseph" in names
        assert "Holy Family" in names

    def test_nonexistent_state_returns_empty(self):
        from app.data_loader import get_churches_for_state

        df = get_churches_for_state("nonexistent")
        assert df.empty


class TestGetServices:
    def test_returns_dataframe(self):
        from app.data_loader import get_services

        df = get_services("ohio")
        assert isinstance(df, pd.DataFrame)

    def test_ohio_has_services(self):
        from app.data_loader import get_services

        df = get_services("ohio")
        assert len(df) == 4

    def test_services_have_city_column(self):
        from app.data_loader import get_services

        df = get_services("ohio")
        assert "city" in df.columns

    def test_services_have_church_slug_column(self):
        from app.data_loader import get_services

        df = get_services("ohio")
        assert "church_slug" in df.columns

    def test_nonexistent_state_returns_empty(self):
        from app.data_loader import get_services

        df = get_services("nonexistent")
        assert df.empty

    def test_cross_state_filtering(self):
        """Services with addresses matching Ohio state should be kept."""
        from app.data_loader import get_services

        df = get_services("ohio")
        # All fixture addresses are in Ohio, so all 4 should remain
        assert len(df) == 4


class TestGetBulletinNames:
    def test_returns_dataframe(self):
        from app.data_loader import get_bulletin_names

        df = get_bulletin_names("ohio")
        assert isinstance(df, pd.DataFrame)

    def test_ohio_has_three_names(self):
        from app.data_loader import get_bulletin_names

        df = get_bulletin_names("ohio")
        assert len(df) == 3

    def test_nonexistent_state_returns_none(self):
        from app.data_loader import get_bulletin_names

        result = get_bulletin_names("nonexistent")
        assert result is None

    def test_has_city_column(self):
        from app.data_loader import get_bulletin_names

        df = get_bulletin_names("ohio")
        assert "city" in df.columns


class TestGetBulletinStats:
    def test_returns_dict(self):
        from app.data_loader import get_bulletin_stats

        stats = get_bulletin_stats("ohio")
        assert isinstance(stats, dict)

    def test_has_required_keys(self):
        from app.data_loader import get_bulletin_stats

        stats = get_bulletin_stats("ohio")
        assert "total_names" in stats
        assert "unique_names" in stats
        assert "church_count" in stats
        assert "city_count" in stats

    def test_total_names_count(self):
        from app.data_loader import get_bulletin_stats

        stats = get_bulletin_stats("ohio")
        assert stats["total_names"] == 3

    def test_church_count(self):
        from app.data_loader import get_bulletin_stats

        stats = get_bulletin_stats("ohio")
        assert stats["church_count"] == 2  # St. Mary and St. Joseph

    def test_nonexistent_state_returns_none(self):
        from app.data_loader import get_bulletin_stats

        result = get_bulletin_stats("nonexistent")
        assert result is None


class TestGetBulletinNamesPage:
    def test_returns_tuple(self):
        from app.data_loader import get_bulletin_names_page

        rows, total, filtered, _unique = get_bulletin_names_page("ohio")
        assert isinstance(rows, list)
        assert isinstance(total, int)
        assert isinstance(filtered, int)

    def test_total_matches_data(self):
        from app.data_loader import get_bulletin_names_page

        _, total, _, _ = get_bulletin_names_page("ohio")
        assert total == 3

    def test_pagination(self):
        from app.data_loader import get_bulletin_names_page

        rows, _, _, _ = get_bulletin_names_page("ohio", start=0, length=2)
        assert len(rows) == 2

    def test_search_filters(self):
        from app.data_loader import get_bulletin_names_page

        rows, _, filtered, _ = get_bulletin_names_page("ohio", search="John")
        assert filtered >= 1
        assert len(rows) >= 1

    def test_church_filter(self):
        from app.data_loader import get_bulletin_names_page

        rows, _, filtered, _ = get_bulletin_names_page("ohio", church_filter="St. Mary")
        assert filtered == 2  # Fr. John Smith and Jane Doe

    def test_nonexistent_state_returns_empty(self):
        from app.data_loader import get_bulletin_names_page

        rows, total, filtered, _unique = get_bulletin_names_page("nonexistent")
        assert rows == []
        assert total == 0


class TestChurchHasBulletinNames:
    def test_true_for_st_mary(self):
        from app.data_loader import church_has_bulletin_names

        assert church_has_bulletin_names("ohio", "St. Mary")

    def test_false_for_unknown_church(self):
        from app.data_loader import church_has_bulletin_names

        assert not church_has_bulletin_names("ohio", "Unknown Parish")

    def test_false_for_nonexistent_state(self):
        from app.data_loader import church_has_bulletin_names

        assert not church_has_bulletin_names("nonexistent", "St. Mary")
