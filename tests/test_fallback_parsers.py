"""Tests for fallback_parsers.py — secondary parsing for empty bulletin fields."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.parsers.fallback_parsers import (
    parse_category_from_context,
    parse_first_last_from_person_name,
    parse_role_from_context,
    parse_title_from_person_name,
)

# ── parse_first_last_from_person_name ────────────────────────────────────────


class TestParseFirstLast:
    def test_comma_reversed(self):
        result = parse_first_last_from_person_name("SMITH, JOHN")
        assert result["first_name"] == "John"
        assert result["last_name"] == "Smith"

    def test_comma_reversed_with_middle(self):
        result = parse_first_last_from_person_name("Martinez, John M.")
        assert result["first_name"] == "John"
        assert result["last_name"] == "Martinez"

    def test_standard_two_word(self):
        result = parse_first_last_from_person_name("John Smith")
        assert result["first_name"] == "John"
        assert result["last_name"] == "Smith"

    def test_three_word_with_middle(self):
        result = parse_first_last_from_person_name("John Michael Smith")
        assert result["first_name"] == "John"
        assert result["last_name"] == "Smith"

    def test_honorific_stripped(self):
        result = parse_first_last_from_person_name("Fr. John Smith")
        assert result["first_name"] == "John"
        assert result["last_name"] == "Smith"

    def test_suffix_stripped(self):
        result = parse_first_last_from_person_name("John Smith Jr.")
        assert result["first_name"] == "John"
        assert result["last_name"] == "Smith"

    def test_single_word_is_last_name(self):
        result = parse_first_last_from_person_name("Martinez")
        assert result["first_name"] == ""
        assert result["last_name"] == "Martinez"

    def test_empty_string(self):
        result = parse_first_last_from_person_name("")
        assert result["first_name"] == ""
        assert result["last_name"] == ""

    def test_none_input(self):
        result = parse_first_last_from_person_name(None)
        assert result["first_name"] == ""
        assert result["last_name"] == ""

    def test_honorific_with_comma_reversed(self):
        result = parse_first_last_from_person_name("Rev. GARCIA, MARIA")
        assert result["first_name"] == "Maria"
        assert result["last_name"] == "Garcia"

    def test_hyphenated_last_name(self):
        result = parse_first_last_from_person_name("Maria De La Rosa")
        assert result["first_name"] == "Maria"
        assert result["last_name"] == "Rosa"


# ── parse_category_from_context ──────────────────────────────────────────────


class TestParseCategoryFromContext:
    def test_prayer_list_pray(self):
        assert parse_category_from_context("Please pray for the sick") == "prayer_list"

    def test_prayer_list_sick(self):
        assert parse_category_from_context("The following are sick") == "prayer_list"

    def test_prayer_list_homebound(self):
        assert parse_category_from_context("Our homebound parishioners") == "prayer_list"

    def test_mass_intention_repose(self):
        assert parse_category_from_context("For the repose of the soul") == "mass_intention"

    def test_mass_intention_deceased(self):
        assert parse_category_from_context("In memory of our deceased") == "mass_intention"

    def test_mass_intention_intention(self):
        assert parse_category_from_context("Mass intention for") == "mass_intention"

    def test_clergy_staff_pastor(self):
        assert parse_category_from_context("Pastor: Fr. John Smith") == "clergy_staff"

    def test_clergy_staff_director(self):
        assert parse_category_from_context("Music Director Jane Doe") == "clergy_staff"

    def test_no_keywords(self):
        assert parse_category_from_context("Sunday schedule and events") == ""

    def test_empty_string(self):
        assert parse_category_from_context("") == ""

    def test_none_input(self):
        assert parse_category_from_context(None) == ""


# ── parse_role_from_context ──────────────────────────────────────────────────


class TestParseRoleFromContext:
    def test_staff_role_pastor(self):
        assert parse_role_from_context("Pastor Fr. John Smith 555-1234") == "Pastor"

    def test_staff_role_business_manager(self):
        assert parse_role_from_context("Business Manager: Teresa Mullen") == "Business Manager"

    def test_ministry_role_lector(self):
        result = parse_role_from_context("Lectors for Sunday")
        assert result == "Lectors"

    def test_no_role(self):
        assert parse_role_from_context("John and Mary will attend the event") == ""

    def test_empty_string(self):
        assert parse_role_from_context("") == ""

    def test_none_input(self):
        assert parse_role_from_context(None) == ""


# ── parse_title_from_person_name ─────────────────────────────────────────────


class TestParseTitleFromPersonName:
    def test_fr_with_period(self):
        assert parse_title_from_person_name("Fr. John Smith") == "Fr."

    def test_rev_with_period(self):
        assert parse_title_from_person_name("Rev. Michael Brown") == "Rev."

    def test_msgr_with_period(self):
        assert parse_title_from_person_name("Msgr. Robert Lee") == "Msgr."

    def test_father_full_word(self):
        assert parse_title_from_person_name("Father John Smith") == "Father"

    def test_deacon_full_word(self):
        assert parse_title_from_person_name("Deacon Michael Brown") == "Deacon"

    def test_no_title(self):
        assert parse_title_from_person_name("John Smith") == ""

    def test_empty_string(self):
        assert parse_title_from_person_name("") == ""

    def test_none_input(self):
        assert parse_title_from_person_name(None) == ""

    def test_dr_title(self):
        assert parse_title_from_person_name("Dr. Jane Wilson") == "Dr."

    def test_sister_title(self):
        assert parse_title_from_person_name("Sister Mary Catherine") == "Sister"
