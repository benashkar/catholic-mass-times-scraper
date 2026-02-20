"""
test_smoke.py

Smoke test to verify the project is set up correctly and imports work.

A "smoke test" is the simplest possible test — it just checks that the
basic infrastructure is working. If this test passes, it means:
    1. Python can find all our modules
    2. The config loads without errors
    3. pytest is configured correctly

Run this test with:
    pytest tests/test_smoke.py -v
"""


def test_config_imports():
    """Verify that the config module can be imported without errors."""
    from config.settings import TARGET_COMMUNITIES, SCRAPE_DELAY

    # We should have 16 target communities defined
    assert len(TARGET_COMMUNITIES) == 16

    # Scrape delay should be a positive number
    assert SCRAPE_DELAY > 0


def test_utils_import():
    """Verify that utility modules can be imported without errors."""
    from src.utils.logger import get_logger
    from src.utils.file_io import save_to_csv, load_from_csv

    # get_logger should return a logger object
    logger = get_logger("test")
    assert logger is not None


def test_target_communities_have_required_fields():
    """Verify every target community has the fields we expect."""
    from config.settings import TARGET_COMMUNITIES

    for community in TARGET_COMMUNITIES:
        # Each community must have these four fields
        assert "name" in community, f"Missing 'name' in {community}"
        assert "county" in community, f"Missing 'county' in {community}"
        assert "state" in community, f"Missing 'state' in {community}"
        assert "zips" in community, f"Missing 'zips' in {community}"

        # ZIP codes should be a non-empty list of strings
        assert len(community["zips"]) > 0, f"No ZIP codes for {community['name']}"
        for zip_code in community["zips"]:
            assert len(zip_code) == 5, f"Invalid ZIP '{zip_code}' for {community['name']}"
