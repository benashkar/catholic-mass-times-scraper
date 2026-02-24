"""
test_address_parser.py

Unit tests for the US street address parser.

Tests cover:
    - Standard addresses with pre-directional
    - Route addresses (State Rt, County Rd, US Hwy)
    - Post-directional (SW, E, etc.)
    - Direction vs name ambiguity (401 N St)
    - Via prefix streets
    - St = Saint vs Street
    - Ampersand addresses
    - Edge cases (number only, no number, leading zeros)
    - State normalization
    - Postal code splitting

HOW TO RUN:
    pytest tests/test_address_parser.py -v
"""

import pytest

from src.parsers.address_parser import (
    normalize_state,
    parse_church_address,
    parse_street,
    split_postal_code,
)

# =============================================================================
# parse_street() tests
# =============================================================================


class TestParseStreetStandard:
    """Tests for standard street addresses."""

    def test_simple_with_pre_direction(self):
        """300 E Highland Ave -> number=300, pre_dir=E, name=Highland, suffix=Ave"""
        result = parse_street("300 E Highland Ave")
        assert result["street_number"] == "300"
        assert result["pre_direction"] == "E"
        assert result["street_name"] == "Highland"
        assert result["street_suffix"] == "Ave"

    def test_ordinal_street_name(self):
        """1200 E 21st St -> number=1200, pre_dir=E, name=21st, suffix=St"""
        result = parse_street("1200 E 21st St")
        assert result["street_number"] == "1200"
        assert result["pre_direction"] == "E"
        assert result["street_name"] == "21st"
        assert result["street_suffix"] == "St"

    def test_no_directional(self):
        """221 Hanna Ave -> number=221, name=Hanna, suffix=Ave"""
        result = parse_street("221 Hanna Ave")
        assert result["street_number"] == "221"
        assert result["pre_direction"] == ""
        assert result["street_name"] == "Hanna"
        assert result["street_suffix"] == "Ave"

    def test_multi_word_name(self):
        """5384 Wilson Mills Rd -> number=5384, name=Wilson Mills, suffix=Rd"""
        result = parse_street("5384 Wilson Mills Rd")
        assert result["street_number"] == "5384"
        assert result["street_name"] == "Wilson Mills"
        assert result["street_suffix"] == "Rd"

    def test_boulevard(self):
        """4490 Norquest Blvd -> number=4490, name=Norquest, suffix=Blvd"""
        result = parse_street("4490 Norquest Blvd")
        assert result["street_number"] == "4490"
        assert result["street_name"] == "Norquest"
        assert result["street_suffix"] == "Blvd"


class TestParseStreetPostDirection:
    """Tests for addresses with post-directional."""

    def test_post_direction_sw(self):
        """748 Roswell Rd SW -> number=748, name=Roswell, suffix=Rd, post_dir=SW"""
        result = parse_street("748 Roswell Rd SW")
        assert result["street_number"] == "748"
        assert result["street_name"] == "Roswell"
        assert result["street_suffix"] == "Rd"
        assert result["post_direction"] == "SW"

    def test_post_direction_e(self):
        """1001 Main St E -> number=1001, name=Main, suffix=St, post_dir=E"""
        result = parse_street("1001 Main St E")
        assert result["street_number"] == "1001"
        assert result["street_name"] == "Main"
        assert result["street_suffix"] == "St"
        assert result["post_direction"] == "E"

    def test_post_direction_dotted_se(self):
        """2532 Burton St S.E -> number=2532, name=Burton, suffix=St, post_dir=SE"""
        result = parse_street("2532 Burton St S.E")
        assert result["street_number"] == "2532"
        assert result["street_name"] == "Burton"
        assert result["street_suffix"] == "St"
        assert result["post_direction"] == "SE"

    def test_pre_and_post_direction(self):
        """1007 Superior Ave E -> number=1007, name=Superior, suffix=Ave, post_dir=E"""
        result = parse_street("1007 Superior Ave E")
        assert result["street_number"] == "1007"
        assert result["street_name"] == "Superior"
        assert result["street_suffix"] == "Ave"
        assert result["post_direction"] == "E"


class TestParseStreetDirectionAmbiguity:
    """Tests for direction-as-street-name ambiguity."""

    def test_n_is_street_name(self):
        """401 N St -> number=401, name=N, suffix=St (N is the street name)"""
        result = parse_street("401 N St")
        assert result["street_number"] == "401"
        assert result["pre_direction"] == ""
        assert result["street_name"] == "N"
        assert result["street_suffix"] == "St"

    def test_s_broadway(self):
        """104 W Broadway St -> number=104, pre_dir=W, name=Broadway, suffix=St"""
        result = parse_street("104 W Broadway St")
        assert result["street_number"] == "104"
        assert result["pre_direction"] == "W"
        assert result["street_name"] == "Broadway"
        assert result["street_suffix"] == "St"


class TestParseStreetRoutes:
    """Tests for route/highway addresses."""

    def test_county_road(self):
        """1098 County Rd 39 -> number=1098, name=County Rd 39"""
        result = parse_street("1098 County Rd 39")
        assert result["street_number"] == "1098"
        assert result["street_name"] == "County Rd 39"
        assert result["street_suffix"] == ""

    def test_state_route(self):
        """02441 State Rt 364 -> number=02441, name=State Rt 364"""
        result = parse_street("02441 State Rt 364")
        assert result["street_number"] == "02441"
        assert result["street_name"] == "State Rt 364"

    def test_state_route_with_post_dir(self):
        """4893 State Rt 634 N -> name=State Rt 634, post_dir=N"""
        result = parse_street("4893 State Rt 634 N")
        assert result["street_number"] == "4893"
        assert result["street_name"] == "State Rt 634"
        assert result["post_direction"] == "N"

    def test_us_route(self):
        """10272 US 42 -> number=10272, name includes US 42"""
        result = parse_street("10272 US 42")
        assert result["street_number"] == "10272"
        assert "42" in result["street_name"]

    def test_oh_route(self):
        """4659 OH-46 -> number=4659, name=OH-46"""
        result = parse_street("4659 OH-46")
        assert result["street_number"] == "4659"
        assert result["street_name"] == "OH-46"


class TestParseStreetSpecial:
    """Tests for special address patterns."""

    def test_via_prefix(self):
        """343 Via Mount Carmel Ave -> name=Via Mount Carmel, suffix=Ave"""
        result = parse_street("343 Via Mount Carmel Ave")
        assert result["street_number"] == "343"
        assert result["street_name"] == "Via Mount Carmel"
        assert result["street_suffix"] == "Ave"

    def test_ampersand(self):
        """109 & Cowen St -> number=109, name includes Cowen, suffix=St"""
        result = parse_street("109 & Cowen St")
        assert result["street_number"] == "109"
        assert "Cowen" in result["street_name"]
        assert result["street_suffix"] == "St"

    def test_saint_prefix(self):
        """200 St Joseph Dr -> name=St Joseph, suffix=Dr (St = Saint)"""
        result = parse_street("200 St Joseph Dr")
        assert result["street_number"] == "200"
        assert result["street_name"] == "St Joseph"
        assert result["street_suffix"] == "Dr"

    def test_saint_marys_ln(self):
        """10 St Mary's Ln -> name=St Mary's, suffix=Ln"""
        result = parse_street("10 St Mary's Ln")
        assert result["street_number"] == "10"
        assert "St Mary" in result["street_name"]
        assert result["street_suffix"] == "Ln"


class TestParseStreetEdgeCases:
    """Tests for edge cases and malformed addresses."""

    def test_leading_zero_preserved(self):
        """02441 State Rt 364 -> street_number is string '02441' not '2441'"""
        result = parse_street("02441 State Rt 364")
        assert result["street_number"] == "02441"

    def test_number_only(self):
        """520 -> number=520, everything else empty"""
        result = parse_street("520")
        assert result["street_number"] == "520"
        assert result["street_name"] == ""

    def test_empty_string(self):
        """Empty string -> all empty"""
        result = parse_street("")
        assert result["street_number"] == ""
        assert result["street_name"] == ""

    def test_none(self):
        """None -> all empty"""
        result = parse_street(None)
        assert result["street_number"] == ""

    def test_no_number(self):
        """Chapel Hill Rd -> name=Chapel Hill, suffix=Rd, no number"""
        result = parse_street("Chapel Hill Rd")
        assert result["street_number"] == ""
        assert result["street_name"] == "Chapel Hill"
        assert result["street_suffix"] == "Rd"

    def test_parenthetical_removed(self):
        """300 Main St (Building A) -> parens removed, name=Main, suffix=St"""
        result = parse_street("300 Main St (Building A)")
        assert result["street_number"] == "300"
        assert result["street_name"] == "Main"
        assert result["street_suffix"] == "St"


# =============================================================================
# normalize_state() tests
# =============================================================================


class TestNormalizeState:
    """Tests for state name normalization."""

    def test_full_name_ohio(self):
        assert normalize_state("Ohio") == "OH"

    def test_abbreviation_oh(self):
        assert normalize_state("OH") == "OH"

    def test_full_name_michigan(self):
        assert normalize_state("Michigan") == "MI"

    def test_abbreviation_mi(self):
        assert normalize_state("MI") == "MI"

    def test_west_virginia(self):
        assert normalize_state("West Virginia") == "WV"

    def test_pennsylvania(self):
        assert normalize_state("Pennsylvania") == "PA"

    def test_empty(self):
        assert normalize_state("") == ""

    def test_none(self):
        assert normalize_state(None) == ""

    def test_lowercase(self):
        assert normalize_state("ohio") == "OH"


# =============================================================================
# split_postal_code() tests
# =============================================================================


class TestSplitPostalCode:
    """Tests for postal code splitting."""

    def test_zip_plus_four(self):
        assert split_postal_code("45810-1122") == ("45810", "1122")

    def test_zip_five_only(self):
        assert split_postal_code("43901") == ("43901", "")

    def test_leading_space(self):
        assert split_postal_code(" 43912") == ("43912", "")

    def test_trailing_space(self):
        assert split_postal_code("47327 ") == ("47327", "")

    def test_none(self):
        assert split_postal_code(None) == ("", "")

    def test_empty(self):
        assert split_postal_code("") == ("", "")


# =============================================================================
# parse_church_address() integration tests
# =============================================================================


class TestParseChurchAddress:
    """Integration tests for the full church address parser."""

    def test_full_church_record(self):
        """Test parsing a complete church record from JSONL data."""
        church = {
            "slug": "us-oh-ada-our-lady-of-lourdes-7728ad72",
            "name": "Our Lady of Lourdes",
            "street": "300 E Highland Ave",
            "city": "Ada",
            "stateRegion": "Ohio",
            "postalCode": "45810-1122",
            "latitude": 40.77245252,
            "longitude": -83.81983261,
            "phone": "(419) 675-1162",
        }
        result = parse_church_address(church)

        assert result["slug"] == "us-oh-ada-our-lady-of-lourdes-7728ad72"
        assert result["name"] == "Our Lady of Lourdes"
        assert result["street_number"] == "300"
        assert result["pre_direction"] == "E"
        assert result["street_name"] == "Highland"
        assert result["street_suffix"] == "Ave"
        assert result["full_street"] == "300 E Highland Ave"
        assert result["city"] == "Ada"
        assert result["state_code"] == "OH"
        assert result["zip5"] == "45810"
        assert result["zip4"] == "1122"
        assert result["latitude"] == 40.77245252
        assert result["longitude"] == -83.81983261
        assert result["phone"] == "(419) 675-1162"

    def test_empty_church(self):
        """Test parsing a church with missing fields."""
        result = parse_church_address({})
        assert result["slug"] == ""
        assert result["state_code"] == ""
        assert result["zip5"] == ""
