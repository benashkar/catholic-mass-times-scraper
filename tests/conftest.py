"""
Shared test fixtures for the Catholic Mass Times Dashboard.

Provides:
  - Flask app and test client (session-scoped)
  - Automatic LRU cache clearing between tests (autouse)
"""

import os

import pytest

# Point DATA_DIR to fixture data BEFORE importing the app
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "data_output")


@pytest.fixture(scope="session")
def app():
    """Create a Flask app configured with fixture data."""
    os.environ["DATA_DIR"] = FIXTURES_DIR
    from app import create_app

    application = create_app()
    application.config["TESTING"] = True
    yield application


@pytest.fixture(scope="session")
def client(app):
    """Flask test client for making requests."""
    return app.test_client()


@pytest.fixture(autouse=True)
def clear_lru_caches():
    """Clear all data_loader LRU caches between tests to prevent state leakage."""
    yield
    from app.data_loader import (
        _load_church_details_jsonl,
        get_bulletin_names,
        get_dated_services,
        get_services,
    )

    get_services.cache_clear()
    get_bulletin_names.cache_clear()
    get_dated_services.cache_clear()
    _load_church_details_jsonl.cache_clear()
