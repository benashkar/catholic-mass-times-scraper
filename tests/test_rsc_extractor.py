"""
test_rsc_extractor.py

Unit tests for the RSC (React Server Component) data extractor.

These tests verify that we can correctly extract church data from the
CatholicIndex.org Next.js RSC payloads without hitting the live site.

Run these tests with:
    pytest tests/test_rsc_extractor.py -v
"""

from src.parsers.rsc_extractor import (
    extract_rsc_chunks,
    extract_initial_churches,
    extract_church_detail,
    extract_city_metadata,
    _find_json_object,
    _clean_undefined,
)


# --- Sample RSC data that mimics what CatholicIndex embeds in its HTML ---
# These are simplified versions of real data for testing purposes.

SAMPLE_CITY_HTML = '''
<html>
<head><title>Catholic Mass Times in Grove City, Ohio</title></head>
<body>
<script>self.__next_f=self.__next_f||[];self.__next_f.push([0])</script>
<script>self.__next_f.push([1,"some-module-data"])</script>
<script>self.__next_f.push([1,"8:[\\"$\\",\\"main\\",null,{\\"children\\":[[\\"$\\",\\"$L14\\",null,{\\"sessionId\\":\\"s_test\\",\\"latitude\\":39.887,\\"longitude\\":-83.088,\\"radius\\":25,\\"contextLabel\\":\\"Grove City, Ohio\\",\\"citySlug\\":\\"grove-city-ohio\\",\\"timeZone\\":\\"America/New_York\\",\\"initialChurches\\":[{\\"slug\\":\\"us-oh-grove-city-our-lady-7815f1cf\\",\\"name\\":\\"Our Lady of Perpetual Help Church\\",\\"street\\":\\"3730 Broadway\\",\\"city\\":\\"Grove City\\",\\"stateRegion\\":\\"Ohio\\",\\"latitude\\":39.887,\\"longitude\\":-83.088,\\"phone\\":\\"(614) 875-3322\\",\\"website\\":\\"http://ourladygc.org/\\",\\"distanceMiles\\":\\"0.0\\",\\"massCount\\":7,\\"confessionCount\\":2,\\"adorationCount\\":1,\\"todayMassCount\\":0,\\"todayConfessionCount\\":0,\\"todayAdorationCount\\":0,\\"todayMassTimes\\":[],\\"todayConfessionTimes\\":[],\\"todayAdorationTimes\\":[],\\"upcomingMasses\\":[{\\"day\\":\\"Sat\\",\\"time\\":\\"16:00:00\\",\\"timeEnd\\":null,\\"daysFromToday\\":1,\\"location\\":null}],\\"upcomingConfessions\\":[],\\"upcomingAdorations\\":[],\\"hasPerpetualAdoration\\":false},{\\"slug\\":\\"us-oh-columbus-st-stephen-abc123\\",\\"name\\":\\"St. Stephen the Martyr\\",\\"street\\":\\"1314 Main St\\",\\"city\\":\\"Columbus\\",\\"stateRegion\\":\\"Ohio\\",\\"latitude\\":39.92,\\"longitude\\":-83.1,\\"phone\\":\\"(614) 555-1234\\",\\"website\\":\\"http://ststephen.org/\\",\\"distanceMiles\\":\\"3.1\\",\\"massCount\\":5,\\"confessionCount\\":1,\\"adorationCount\\":0,\\"todayMassCount\\":0,\\"todayConfessionCount\\":0,\\"todayAdorationCount\\":0,\\"todayMassTimes\\":[],\\"todayConfessionTimes\\":[],\\"todayAdorationTimes\\":[],\\"upcomingMasses\\":[{\\"day\\":\\"Sun\\",\\"time\\":\\"09:00:00\\",\\"timeEnd\\":null,\\"daysFromToday\\":2,\\"location\\":null}],\\"upcomingConfessions\\":[],\\"upcomingAdorations\\":[],\\"hasPerpetualAdoration\\":false}]}]]}"])</script>
</body>
</html>
'''

SAMPLE_CHURCH_HTML = '''
<html>
<head><title>Our Lady of Perpetual Help Church</title></head>
<body>
<script>self.__next_f=self.__next_f||[];self.__next_f.push([0])</script>
<script>self.__next_f.push([1,"module-data-here"])</script>
<script>self.__next_f.push([1,"15:[\\"$\\",\\"$L20\\",null,{\\"data\\":{\\"church\\":{\\"slug\\":\\"us-oh-grove-city-our-lady-7815f1cf\\",\\"name\\":\\"Our Lady of Perpetual Help Church\\",\\"street\\":\\"3730 Broadway\\",\\"city\\":\\"Grove City\\",\\"stateRegion\\":\\"Ohio\\",\\"postalCode\\":\\"43123\\",\\"latitude\\":39.887,\\"longitude\\":-83.088,\\"phone\\":\\"(614) 875-3322\\",\\"website\\":\\"http://ourladygc.org/\\",\\"updatedAt\\":\\"2026-02-17T23:18:28.730Z\\"},\\"services\\":{\\"Mass\\":[{\\"serviceId\\":\\"svc1\\",\\"category\\":\\"Mass\\",\\"scheduleType\\":\\"sunday\\",\\"dayOfWeek\\":\\"Sun\\",\\"timeStart\\":\\"08:30:00\\",\\"timeEnd\\":null,\\"displayName\\":\\"Holy Mass\\",\\"language\\":null,\\"location\\":null,\\"eventDate\\":\\"$undefined\\",\\"pattern\\":null,\\"timeRelation\\":null,\\"referenceService\\":null,\\"offsetMinutes\\":null,\\"notes\\":null},{\\"serviceId\\":\\"svc2\\",\\"category\\":\\"Mass\\",\\"scheduleType\\":\\"sunday\\",\\"dayOfWeek\\":\\"Sun\\",\\"timeStart\\":\\"10:30:00\\",\\"timeEnd\\":null,\\"displayName\\":\\"Holy Mass\\",\\"language\\":null,\\"location\\":null,\\"eventDate\\":\\"$undefined\\",\\"pattern\\":null,\\"timeRelation\\":null,\\"referenceService\\":null,\\"offsetMinutes\\":null,\\"notes\\":null},{\\"serviceId\\":\\"svc3\\",\\"category\\":\\"Mass\\",\\"scheduleType\\":\\"saturday\\",\\"dayOfWeek\\":\\"Sat\\",\\"timeStart\\":\\"16:00:00\\",\\"timeEnd\\":null,\\"displayName\\":\\"Vigil\\",\\"language\\":null,\\"location\\":null,\\"eventDate\\":\\"$undefined\\",\\"pattern\\":null,\\"timeRelation\\":null,\\"referenceService\\":null,\\"offsetMinutes\\":null,\\"notes\\":\\"vigil\\"}],\\"Confession\\":[{\\"serviceId\\":\\"svc4\\",\\"category\\":\\"Confession\\",\\"scheduleType\\":\\"saturday\\",\\"dayOfWeek\\":\\"Sat\\",\\"timeStart\\":\\"15:00:00\\",\\"timeEnd\\":\\"15:45:00\\",\\"displayName\\":\\"Confession\\",\\"language\\":null,\\"location\\":null,\\"eventDate\\":\\"$undefined\\",\\"pattern\\":null,\\"timeRelation\\":null,\\"referenceService\\":null,\\"offsetMinutes\\":null,\\"notes\\":\\"Or anytime by appointment\\"}],\\"Adoration\\":[{\\"serviceId\\":\\"svc5\\",\\"category\\":\\"Adoration\\",\\"scheduleType\\":\\"specific_weekday\\",\\"dayOfWeek\\":\\"Fri\\",\\"timeStart\\":\\"09:00:00\\",\\"timeEnd\\":\\"21:00:00\\",\\"displayName\\":\\"Adoration\\",\\"language\\":null,\\"location\\":null,\\"eventDate\\":\\"$undefined\\",\\"pattern\\":null,\\"timeRelation\\":null,\\"referenceService\\":null,\\"offsetMinutes\\":null,\\"notes\\":null}]},\\"totalServices\\":5,\\"communityInsights\\":\\"Great parish.\\"}}]"])</script>
</body>
</html>
'''


class TestExtractRscChunks:
    """Tests for extract_rsc_chunks() function."""

    def test_extracts_chunks_from_valid_html(self):
        """Should find all self.__next_f.push([1, ...]) chunks."""
        chunks = extract_rsc_chunks(SAMPLE_CITY_HTML)
        # We have 2 push([1,...]) calls in the sample
        assert len(chunks) == 2

    def test_returns_empty_for_non_nextjs_html(self):
        """Should return empty list for HTML without RSC data."""
        html = "<html><body><p>Hello world</p></body></html>"
        chunks = extract_rsc_chunks(html)
        assert chunks == []

    def test_unescapes_json_strings(self):
        """Should unescape double-escaped characters in RSC data."""
        chunks = extract_rsc_chunks(SAMPLE_CITY_HTML)
        # The chunk with church data should have unescaped quotes
        church_chunk = [c for c in chunks if 'initialChurches' in c]
        assert len(church_chunk) == 1
        assert '"initialChurches"' in church_chunk[0]


class TestExtractInitialChurches:
    """Tests for extract_initial_churches() function."""

    def test_extracts_churches_from_city_page(self):
        """Should find all churches in the initialChurches array."""
        chunks = extract_rsc_chunks(SAMPLE_CITY_HTML)
        churches = extract_initial_churches(chunks)
        assert len(churches) == 2

    def test_first_church_has_correct_name(self):
        """Should correctly parse the church name."""
        chunks = extract_rsc_chunks(SAMPLE_CITY_HTML)
        churches = extract_initial_churches(chunks)
        assert churches[0]['name'] == "Our Lady of Perpetual Help Church"

    def test_church_has_required_fields(self):
        """Every church should have the essential fields we need."""
        chunks = extract_rsc_chunks(SAMPLE_CITY_HTML)
        churches = extract_initial_churches(chunks)

        required_fields = ['slug', 'name', 'street', 'city', 'stateRegion',
                           'latitude', 'longitude', 'phone']
        for church in churches:
            for field in required_fields:
                assert field in church, f"Missing field '{field}' in church {church.get('name')}"

    def test_church_has_mass_count(self):
        """Should include mass count for each church."""
        chunks = extract_rsc_chunks(SAMPLE_CITY_HTML)
        churches = extract_initial_churches(chunks)
        assert churches[0]['massCount'] == 7
        assert churches[1]['massCount'] == 5

    def test_returns_empty_for_no_churches(self):
        """Should return empty list when no initialChurches in data."""
        chunks = ["some random data without churches"]
        churches = extract_initial_churches(chunks)
        assert churches == []


class TestExtractCityMetadata:
    """Tests for extract_city_metadata() function."""

    def test_extracts_city_label(self):
        """Should extract the city name/label."""
        chunks = extract_rsc_chunks(SAMPLE_CITY_HTML)
        metadata = extract_city_metadata(chunks)
        assert metadata['contextLabel'] == "Grove City, Ohio"

    def test_extracts_coordinates(self):
        """Should extract center lat/lng."""
        chunks = extract_rsc_chunks(SAMPLE_CITY_HTML)
        metadata = extract_city_metadata(chunks)
        assert abs(metadata['latitude'] - 39.887) < 0.01
        assert abs(metadata['longitude'] - (-83.088)) < 0.01

    def test_extracts_radius(self):
        """Should extract the search radius."""
        chunks = extract_rsc_chunks(SAMPLE_CITY_HTML)
        metadata = extract_city_metadata(chunks)
        assert metadata['radius'] == 25


class TestExtractChurchDetail:
    """Tests for extract_church_detail() function."""

    def test_extracts_church_info(self):
        """Should extract church name, address, and contact info."""
        chunks = extract_rsc_chunks(SAMPLE_CHURCH_HTML)
        detail = extract_church_detail(chunks)

        assert detail is not None
        assert detail['church']['name'] == "Our Lady of Perpetual Help Church"
        assert detail['church']['street'] == "3730 Broadway"
        assert detail['church']['city'] == "Grove City"
        assert detail['church']['postalCode'] == "43123"

    def test_extracts_mass_services(self):
        """Should extract all mass times."""
        chunks = extract_rsc_chunks(SAMPLE_CHURCH_HTML)
        detail = extract_church_detail(chunks)

        masses = detail['services']['Mass']
        assert len(masses) == 3

        # Check the Sunday 8:30 AM mass
        sun_830 = [m for m in masses if m['timeStart'] == '08:30:00' and m['dayOfWeek'] == 'Sun']
        assert len(sun_830) == 1
        assert sun_830[0]['displayName'] == "Holy Mass"
        assert sun_830[0]['scheduleType'] == "sunday"

    def test_extracts_confession_with_time_range(self):
        """Should extract confession times with start AND end times."""
        chunks = extract_rsc_chunks(SAMPLE_CHURCH_HTML)
        detail = extract_church_detail(chunks)

        confessions = detail['services']['Confession']
        assert len(confessions) == 1
        assert confessions[0]['timeStart'] == "15:00:00"
        assert confessions[0]['timeEnd'] == "15:45:00"
        assert confessions[0]['notes'] == "Or anytime by appointment"

    def test_extracts_adoration(self):
        """Should extract adoration hours."""
        chunks = extract_rsc_chunks(SAMPLE_CHURCH_HTML)
        detail = extract_church_detail(chunks)

        adoration = detail['services']['Adoration']
        assert len(adoration) == 1
        assert adoration[0]['dayOfWeek'] == "Fri"
        assert adoration[0]['timeStart'] == "09:00:00"
        assert adoration[0]['timeEnd'] == "21:00:00"

    def test_extracts_total_services(self):
        """Should include the total service count."""
        chunks = extract_rsc_chunks(SAMPLE_CHURCH_HTML)
        detail = extract_church_detail(chunks)
        assert detail['totalServices'] == 5

    def test_cleans_undefined_values(self):
        """Should replace '$undefined' with None."""
        chunks = extract_rsc_chunks(SAMPLE_CHURCH_HTML)
        detail = extract_church_detail(chunks)

        # eventDate fields were "$undefined" in the raw data
        for mass in detail['services']['Mass']:
            assert mass['eventDate'] is None  # Should be None, not "$undefined"

    def test_vigil_mass_identified(self):
        """Should correctly identify Saturday vigil masses."""
        chunks = extract_rsc_chunks(SAMPLE_CHURCH_HTML)
        detail = extract_church_detail(chunks)

        vigil = [m for m in detail['services']['Mass']
                 if m['dayOfWeek'] == 'Sat' and m['scheduleType'] == 'saturday']
        assert len(vigil) == 1
        assert vigil[0]['displayName'] == "Vigil"
        assert vigil[0]['timeStart'] == "16:00:00"


class TestCleanUndefined:
    """Tests for the _clean_undefined helper function."""

    def test_replaces_string_undefined(self):
        """Should replace '$undefined' with None in dicts."""
        data = {"key": "$undefined", "other": "value"}
        _clean_undefined(data)
        assert data["key"] is None
        assert data["other"] == "value"

    def test_handles_nested_dicts(self):
        """Should clean nested dictionaries."""
        data = {"outer": {"inner": "$undefined"}}
        _clean_undefined(data)
        assert data["outer"]["inner"] is None

    def test_handles_lists(self):
        """Should clean values inside lists."""
        data = {"items": ["$undefined", "keep", "$undefined"]}
        _clean_undefined(data)
        assert data["items"] == [None, "keep", None]

    def test_handles_mixed_structures(self):
        """Should clean complex nested structures."""
        data = {
            "a": "$undefined",
            "b": [{"c": "$undefined", "d": "ok"}, "$undefined"],
        }
        _clean_undefined(data)
        assert data["a"] is None
        assert data["b"][0]["c"] is None
        assert data["b"][0]["d"] == "ok"
        assert data["b"][1] is None
