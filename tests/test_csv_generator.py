"""Tests for src/etl/csv_generator.py — date/time helpers and CSV generation."""

import csv
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.etl.csv_generator import (
    format_time_12h,
    generate_dated_services_csv,
    generate_services_csv,
    get_dates_for_day,
    get_nth_weekday_of_month,
    get_pattern_dates,
)


class TestFormatTime12h:
    def test_midnight(self):
        assert format_time_12h("00:00:00") == "12:00 AM"

    def test_noon(self):
        assert format_time_12h("12:00:00") == "12:00 PM"

    def test_pm(self):
        assert format_time_12h("17:30:00") == "5:30 PM"

    def test_am(self):
        assert format_time_12h("09:15:00") == "9:15 AM"

    def test_none_returns_tbd(self):
        assert format_time_12h(None) == "TBD"

    def test_empty_string_returns_tbd(self):
        assert format_time_12h("") == "TBD"

    def test_malformed_returns_original(self):
        assert format_time_12h("not-a-time") == "not-a-time"


class TestGetDatesForDay:
    def test_returns_correct_weekday(self):
        # 2026-01-05 is a Monday
        start = date(2026, 1, 5)
        dates = get_dates_for_day("Mon", start, num_weeks=3)
        assert len(dates) == 3
        for d in dates:
            assert d.weekday() == 0  # Monday

    def test_sunday_from_monday_start(self):
        start = date(2026, 1, 5)  # Monday
        dates = get_dates_for_day("Sun", start, num_weeks=2)
        assert len(dates) == 2
        for d in dates:
            assert d.weekday() == 6  # Sunday

    def test_same_day_includes_start(self):
        start = date(2026, 1, 5)  # Monday
        dates = get_dates_for_day("Mon", start, num_weeks=1)
        assert dates[0] == start

    def test_unknown_day_returns_empty(self):
        start = date(2026, 1, 5)
        dates = get_dates_for_day("Xyz", start)
        assert dates == []


class TestGetNthWeekdayOfMonth:
    def test_first_friday_jan_2026(self):
        result = get_nth_weekday_of_month(2026, 1, 4, 1)  # First Friday
        assert result == date(2026, 1, 2)

    def test_first_saturday_jan_2026(self):
        result = get_nth_weekday_of_month(2026, 1, 5, 1)  # First Saturday
        assert result == date(2026, 1, 3)

    def test_fifth_monday_returns_none_when_absent(self):
        # Feb 2026 has only 4 Mondays
        result = get_nth_weekday_of_month(2026, 2, 0, 5)
        assert result is None


class TestGetPatternDates:
    def test_first_friday(self):
        start = date(2026, 1, 1)
        dates = get_pattern_dates("first_friday_(recurring)", start, num_months=2)
        assert len(dates) >= 1
        for d in dates:
            assert d.weekday() == 4  # Friday

    def test_first_saturday(self):
        start = date(2026, 1, 1)
        dates = get_pattern_dates("first_saturday_(recurring)", start, num_months=2)
        assert len(dates) >= 1
        for d in dates:
            assert d.weekday() == 5  # Saturday

    def test_unknown_pattern_returns_empty(self):
        start = date(2026, 1, 1)
        dates = get_pattern_dates("unknown_pattern", start)
        assert dates == []


def _make_church_detail(name="Test Church", city="Columbus", services=None):
    """Helper to create a church detail dict matching scrape_church_detail() output."""
    if services is None:
        services = {
            "Mass": [
                {
                    "dayOfWeek": "Sun",
                    "timeStart": "10:00:00",
                    "timeEnd": None,
                    "displayName": "Sunday Mass",
                    "scheduleType": "weekly",
                    "language": "English",
                    "location": "",
                    "pattern": "",
                    "eventDate": "",
                    "timeRelation": "",
                    "referenceService": "",
                    "notes": "",
                }
            ]
        }
    return {
        "church": {
            "name": name,
            "street": "123 Main St",
            "city": city,
            "stateRegion": "OH",
            "postalCode": "43215",
            "phone": "614-555-0101",
        },
        "services": services,
    }


class TestGenerateServicesCsv:
    def test_writes_csv(self, tmp_path):
        output = tmp_path / "all_services.csv"
        details = [_make_church_detail()]
        count = generate_services_csv(details, output)
        assert output.exists()
        assert count == 1

    def test_correct_row_count(self, tmp_path):
        output = tmp_path / "all_services.csv"
        details = [
            _make_church_detail("Church A"),
            _make_church_detail("Church B"),
        ]
        count = generate_services_csv(details, output)
        assert count == 2

    def test_csv_has_headers(self, tmp_path):
        output = tmp_path / "all_services.csv"
        generate_services_csv([_make_church_detail()], output)
        with open(output, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            headers = next(reader)
        assert "Church" in headers
        assert "Category" in headers

    def test_empty_details_writes_nothing(self, tmp_path):
        output = tmp_path / "all_services.csv"
        count = generate_services_csv([], output)
        assert count == 0
        assert not output.exists()


class TestGenerateDatedServicesCsv:
    def test_writes_csv(self, tmp_path):
        output = tmp_path / "dated_services.csv"
        details = [_make_church_detail()]
        count = generate_dated_services_csv(details, output)
        assert output.exists()
        assert count > 0

    def test_dated_rows_are_in_future(self, tmp_path):
        output = tmp_path / "dated_services.csv"
        generate_dated_services_csv([_make_church_detail()], output)
        with open(output, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                row_date = date.fromisoformat(row["Date Sort"])
                assert row_date >= date.today()

    def test_empty_details_writes_nothing(self, tmp_path):
        output = tmp_path / "dated_services.csv"
        count = generate_dated_services_csv([], output)
        assert count == 0
        assert not output.exists()
