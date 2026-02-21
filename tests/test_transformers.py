"""
test_transformers.py

Unit tests for the ETL transformer functions that map CatholicIndex raw data
to our normalized database schema.

Run these tests with:
    pytest tests/test_transformers.py -v
"""

from src.etl.transformers import (
    transform_church,
    transform_service,
    transform_community,
    parse_note_tags,
    CATEGORY_MAP,
    SCHEDULE_TYPE_MAP,
    LANGUAGE_MAP,
    PATTERN_MAP,
    STATE_MAP,
)


# --- Sample raw data mimicking what CatholicIndex returns ---

SAMPLE_RAW_CHURCH = {
    "slug": "us-oh-grove-city-our-lady-of-perpetual-help-7815f1cf",
    "name": "Our Lady of Perpetual Help Church",
    "street": "3730 Broadway",
    "city": "Grove City",
    "stateRegion": "Ohio",
    "postalCode": "43123",
    "latitude": 39.88751302,
    "longitude": -83.08795897,
    "phone": "(614) 875-3322",
    "website": "/api/out?id=us-oh-grove-city-our-lady-7815f1cf&type=website",
    "livestreamUrl": "/api/out?id=us-oh-grove-city-our-lady-7815f1cf&type=livestream",
    "updatedAt": "2026-02-17T23:18:28.730Z",
    "hasPerpetualAdoration": False,
    "massCount": 7,
    "confessionCount": 2,
    "adorationCount": 1,
    "streamCount": 0,
    "communityInsights": "Great parish with welcoming community.",
}

SAMPLE_RAW_SERVICE_MASS = {
    "serviceId": "57846898eca4fef3",
    "category": "Mass",
    "scheduleType": "sunday",
    "dayOfWeek": "Sun",
    "timeStart": "08:30:00",
    "timeEnd": None,
    "displayName": "Holy Mass",
    "language": None,
    "location": None,
    "eventDate": None,
    "pattern": None,
    "timeRelation": None,
    "referenceService": None,
    "offsetMinutes": None,
    "notes": None,
}

SAMPLE_RAW_SERVICE_CONFESSION = {
    "serviceId": "476c94cf3cee634e",
    "category": "Confession",
    "scheduleType": "other",
    "dayOfWeek": "Mon",
    "timeStart": None,
    "timeEnd": None,
    "displayName": "Confession",
    "language": None,
    "location": None,
    "eventDate": None,
    "pattern": None,
    "timeRelation": "after",
    "referenceService": "Mass",
    "offsetMinutes": None,
    "notes": "Sat; After 08:30 Mass",
}

SAMPLE_RAW_SERVICE_SPANISH = {
    "serviceId": "2cf9c7791a051ab5",
    "category": "Mass",
    "scheduleType": "sunday",
    "dayOfWeek": "Sun",
    "timeStart": "11:00:00",
    "timeEnd": None,
    "displayName": "Mass",
    "language": "spanish",
    "location": None,
    "eventDate": None,
    "pattern": None,
    "timeRelation": None,
    "referenceService": None,
    "offsetMinutes": None,
    "notes": None,
}

SAMPLE_RAW_SERVICE_MONTHLY = {
    "serviceId": "66c28e854640cc37",
    "category": "Adoration",
    "scheduleType": "monthly",
    "dayOfWeek": None,
    "timeStart": "09:00:00",
    "timeEnd": None,
    "displayName": "Adoration",
    "language": None,
    "location": None,
    "eventDate": None,
    "pattern": "first_friday_(recurring)",
    "timeRelation": None,
    "referenceService": None,
    "offsetMinutes": None,
    "notes": "24 hours",
}

SAMPLE_RAW_COMMUNITY = {
    "name": "Grove City",
    "county": "Franklin",
    "state": "OH",
    "zips": ["43123"],
}


class TestTransformChurch:
    """Tests for transform_church() function."""

    def test_maps_state_name_to_code(self):
        """Should convert 'Ohio' to 'OH'."""
        result = transform_church(SAMPLE_RAW_CHURCH)
        assert result["state_code"] == "OH"

    def test_preserves_slug(self):
        """Should keep the CatholicIndex slug as-is."""
        result = transform_church(SAMPLE_RAW_CHURCH)
        assert result["slug"] == "us-oh-grove-city-our-lady-of-perpetual-help-7815f1cf"

    def test_preserves_coordinates(self):
        """Should keep lat/lng as numeric values."""
        result = transform_church(SAMPLE_RAW_CHURCH)
        assert abs(result["latitude"] - 39.88751302) < 0.0001
        assert abs(result["longitude"] - (-83.08795897)) < 0.0001

    def test_sets_has_livestream_from_url(self):
        """Should set has_livestream=True when livestreamUrl is present."""
        result = transform_church(SAMPLE_RAW_CHURCH)
        assert result["has_livestream"] is True

    def test_sets_has_livestream_false_when_no_url(self):
        """Should set has_livestream=False when livestreamUrl is None."""
        raw = {**SAMPLE_RAW_CHURCH, "livestreamUrl": None}
        result = transform_church(raw)
        assert result["has_livestream"] is False

    def test_parses_updated_at_timestamp(self):
        """Should parse ISO 8601 timestamp to datetime."""
        result = transform_church(SAMPLE_RAW_CHURCH)
        assert result["schedule_updated_at"] is not None
        assert result["schedule_updated_at"].year == 2026

    def test_preserves_community_insights(self):
        """Should keep the community insights text as-is."""
        result = transform_church(SAMPLE_RAW_CHURCH)
        assert "Great parish" in result["community_insights"]

    def test_sets_last_scraped_at(self):
        """Should set last_scraped_at to current UTC time."""
        result = transform_church(SAMPLE_RAW_CHURCH)
        assert result["last_scraped_at"] is not None

    def test_accepts_diocese_id(self):
        """Should include diocese_id when provided."""
        result = transform_church(SAMPLE_RAW_CHURCH, diocese_id=1)
        assert result["diocese_id"] == 1

    def test_handles_missing_fields_gracefully(self):
        """Should not crash on minimal input."""
        minimal = {"slug": "test", "name": "Test", "city": "City"}
        result = transform_church(minimal)
        assert result["slug"] == "test"
        assert result["has_livestream"] is False
        assert result["mass_count"] == 0


class TestTransformService:
    """Tests for transform_service() function."""

    def test_maps_category_to_code(self):
        """Should convert 'Mass' to 'mass'."""
        result = transform_service(SAMPLE_RAW_SERVICE_MASS, church_id=1)
        assert result["category_code"] == "mass"

    def test_maps_schedule_type_to_code(self):
        """Should convert 'sunday' to 'sunday'."""
        result = transform_service(SAMPLE_RAW_SERVICE_MASS, church_id=1)
        assert result["schedule_type_code"] == "sunday"

    def test_sets_church_id(self):
        """Should include the church_id foreign key."""
        result = transform_service(SAMPLE_RAW_SERVICE_MASS, church_id=42)
        assert result["church_id"] == 42

    def test_maps_language_to_code(self):
        """Should convert 'spanish' to 'es'."""
        result = transform_service(SAMPLE_RAW_SERVICE_SPANISH, church_id=1)
        assert result["language_code"] == "es"

    def test_null_language_stays_null(self):
        """Should keep language_code as None when language is None (= English)."""
        result = transform_service(SAMPLE_RAW_SERVICE_MASS, church_id=1)
        assert result["language_code"] is None

    def test_maps_time_relation(self):
        """Should convert 'after' to 'after'."""
        result = transform_service(SAMPLE_RAW_SERVICE_CONFESSION, church_id=1)
        assert result["relation_code"] == "after"
        assert result["reference_service"] == "Mass"

    def test_maps_recurrence_pattern(self):
        """Should convert 'first_friday_(recurring)' to 'first_friday'."""
        result = transform_service(SAMPLE_RAW_SERVICE_MONTHLY, church_id=1)
        assert result["pattern_code"] == "first_friday"

    def test_preserves_raw_notes(self):
        """Should keep the original notes string in notes_raw."""
        result = transform_service(SAMPLE_RAW_SERVICE_CONFESSION, church_id=1)
        assert result["notes_raw"] == "Sat; After 08:30 Mass"

    def test_preserves_source_service_id(self):
        """Should keep the CatholicIndex service ID for dedup."""
        result = transform_service(SAMPLE_RAW_SERVICE_MASS, church_id=1)
        assert result["source_service_id"] == "57846898eca4fef3"

    def test_unknown_category_defaults_to_other(self):
        """Should default to 'other' for unknown categories."""
        raw = {**SAMPLE_RAW_SERVICE_MASS, "category": "SomethingNew"}
        result = transform_service(raw, church_id=1)
        assert result["category_code"] == "other"

    def test_unknown_schedule_type_defaults_to_other(self):
        """Should default to 'other' for unknown schedule types."""
        raw = {**SAMPLE_RAW_SERVICE_MASS, "scheduleType": "biweekly"}
        result = transform_service(raw, church_id=1)
        assert result["schedule_type_code"] == "other"


class TestTransformCommunity:
    """Tests for transform_community() function."""

    def test_maps_community_fields(self):
        """Should convert config dict to DB format."""
        result = transform_community(SAMPLE_RAW_COMMUNITY)
        assert result["name"] == "Grove City"
        assert result["county"] == "Franklin"
        assert result["state_code"] == "OH"
        assert result["zip_codes"] == "43123"

    def test_joins_multiple_zips(self):
        """Should join multiple ZIP codes with commas."""
        raw = {**SAMPLE_RAW_COMMUNITY, "zips": ["43110", "43125"]}
        result = transform_community(raw)
        assert result["zip_codes"] == "43110,43125"

    def test_includes_fallback_city(self):
        """Should include fallback_city when present."""
        raw = {**SAMPLE_RAW_COMMUNITY, "fallback_city": "Columbus"}
        result = transform_community(raw)
        assert result["fallback_city"] == "Columbus"

    def test_no_fallback_city_is_none(self):
        """Should be None when no fallback_city."""
        result = transform_community(SAMPLE_RAW_COMMUNITY)
        assert result["fallback_city"] is None


class TestParseNoteTags:
    """Tests for parse_note_tags() function."""

    def test_detects_vigil(self):
        """Should detect 'vigil' in notes."""
        tags = parse_note_tags("vigil")
        assert "vigil" in tags

    def test_detects_by_appointment(self):
        """Should detect 'by appointment' phrase."""
        tags = parse_note_tags("Or anytime by appointment")
        assert "by_appointment" in tags

    def test_detects_24_hours(self):
        """Should detect '24 hours' in notes."""
        tags = parse_note_tags("24 hours")
        assert "24_hours" in tags

    def test_detects_school_mass(self):
        """Should detect 'school Mass' in notes."""
        tags = parse_note_tags("Tuesday 08:30 is all-school Mass. Public is welcome!")
        assert "school_mass" in tags
        assert "public_welcome" in tags

    def test_detects_holy_day(self):
        """Should detect 'Holy Day' in notes."""
        tags = parse_note_tags("Holy Day of Obligation Mass")
        assert "holy_day" in tags

    def test_detects_multiple_tags(self):
        """Should detect multiple tags in one notes string."""
        tags = parse_note_tags("Vigil; Or anytime by appointment")
        assert "vigil" in tags
        assert "by_appointment" in tags

    def test_returns_empty_for_none(self):
        """Should return empty list for None notes."""
        tags = parse_note_tags(None)
        assert tags == []

    def test_returns_empty_for_empty_string(self):
        """Should return empty list for empty string."""
        tags = parse_note_tags("")
        assert tags == []

    def test_case_insensitive(self):
        """Should match regardless of case."""
        tags = parse_note_tags("VIGIL")
        assert "vigil" in tags

    def test_detects_seasonal(self):
        """Should detect liturgical seasons."""
        tags = parse_note_tags("During Lent only")
        assert "seasonal" in tags

    def test_detects_exposition(self):
        """Should detect Eucharistic Exposition."""
        tags = parse_note_tags("Includes Exposition and Benediction")
        assert "exposition" in tags
        assert "benediction" in tags


class TestMappingCompleteness:
    """Tests to verify our mappings cover all known CatholicIndex values."""

    def test_all_categories_mapped(self):
        """All known CatholicIndex categories should have a mapping."""
        known = ["Mass", "Confession", "Adoration", "Devotions", "Other", "Education", "Community"]
        for cat in known:
            assert cat in CATEGORY_MAP, f"Category '{cat}' not in CATEGORY_MAP"

    def test_all_schedule_types_mapped(self):
        """All known schedule types should have a mapping."""
        known = ["sunday", "saturday", "weekday", "specific_weekday", "monthly",
                 "special_event", "parish_event", "other"]
        for st in known:
            assert st in SCHEDULE_TYPE_MAP, f"Schedule type '{st}' not in SCHEDULE_TYPE_MAP"

    def test_all_languages_mapped(self):
        """All known languages should have a mapping."""
        known = ["english", "spanish", "latin", "bilingual"]
        for lang in known:
            assert lang in LANGUAGE_MAP, f"Language '{lang}' not in LANGUAGE_MAP"

    def test_all_patterns_mapped(self):
        """All known recurrence patterns should have a mapping."""
        known = [
            "first_friday_(recurring)",
            "first_saturday_(recurring)",
            "first_sunday",
            "thursday_(before_first_friday)",
        ]
        for pat in known:
            assert pat in PATTERN_MAP, f"Pattern '{pat}' not in PATTERN_MAP"

    def test_ohio_in_state_map(self):
        """Ohio must be in the state map (Phase 1 state)."""
        assert "Ohio" in STATE_MAP
        assert STATE_MAP["Ohio"] == "OH"
