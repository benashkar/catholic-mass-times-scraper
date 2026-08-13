"""
Shared test fixtures for the Catholic Mass Times Dashboard.

Provides:
  - Flask app and test client (session-scoped)
  - Automatic LRU cache clearing between tests (autouse)
  - `requires_db` marker: auto-skips tests when db99 is unreachable in CI
"""

import os

import pytest

# Point DATA_DIR to fixture data BEFORE importing the app
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "data_output")

# Track whether db99 data loaded successfully at startup
_db_available = False


# Every view is behind a session (added 2026-08-05); only /login and /health
# are public. Tests need a real credential pair or every route request is a
# 302 to /login — which is what silently happened to the whole route suite:
# the assertions kept passing against the redirect body, not the page.
TEST_USER, TEST_PASSWORD = "pytest@example.com", "pytest-only"


@pytest.fixture(scope="session")
def app():
    """Create a Flask app configured with fixture data."""
    global _db_available
    os.environ["DATA_DIR"] = FIXTURES_DIR
    os.environ["DASHBOARD_USERS"] = f"{TEST_USER}:{TEST_PASSWORD}"
    from app import create_app

    application = create_app()
    application.config["TESTING"] = True

    # Check if db99 data was loaded (init_data succeeded)
    from app.data_loader import _state_list

    _db_available = bool(_state_list)
    yield application


@pytest.fixture(scope="session")
def anon_client(app):
    """Unauthenticated client — for asserting the login gate holds."""
    return app.test_client()


@pytest.fixture(scope="session")
def client(app):
    """Logged-in test client, so route tests assert on pages and not redirects."""
    c = app.test_client()
    resp = c.post(
        "/login",
        data={"username": TEST_USER, "password": TEST_PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 302, (
        f"test login failed ({resp.status_code}); without it every route test "
        f"asserts against the /login redirect instead of the real page"
    )
    return c


@pytest.fixture(autouse=True)
def skip_if_no_db(request):
    """Auto-skip tests marked with `requires_db` when db99 is unreachable."""
    if request.node.get_closest_marker("requires_db") and not _db_available:
        pytest.skip("db99 not available (CI environment)")


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
