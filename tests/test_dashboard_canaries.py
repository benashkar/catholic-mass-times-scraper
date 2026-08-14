"""Empty-result canaries for every dashboard screen.

An empty set is a valid SQL result and a 200 HTTP response, so it looks
identical to working code. That is exactly how /bulletin/ broke: the startup
load timed out in some gunicorn workers, those workers set the state list to []
permanently, and the page served "No bulletin name data available yet" with a
200 while its siblings served the real thing. Every status-code check passed.

So these assert on CONTENT, one assertion per screen dependency, each message
naming the UI element that disappears when it returns nothing.

Run:  PYTHONPATH=dashboard DB_HOST=10.10.0.8 pytest tests/test_dashboard_canaries.py -v
"""
import pytest

# NOTE: requires_db is applied per class, not to the whole module. CI has no
# route to db99, so anything marked requires_db auto-skips there — and a canary
# that only ever skips is not a check. The failure-path regressions below stub
# the connection out and therefore run in CI, where they matter most.

# Literal, not computed. A canary that derives its own parameters from
# datetime.now() tracks the same logic the endpoint uses and cannot catch a
# window that has become impossible.
STATE_DIR = "wisconsin"
STATE_WITH_MASS_TIMES = "ohio"
EMPTY_MARKER = b"No bulletin name data available yet"


@pytest.mark.requires_db
class TestBulletinIndexCanary:
    def test_index_lists_states(self, client):
        r = client.get("/bulletin/")
        assert r.status_code == 200
        assert EMPTY_MARKER not in r.data, (
            "/bulletin/ rendered the empty-state banner — the entire state grid "
            "vanishes. This is the worker-poisoning regression."
        )

    def test_index_names_a_known_state(self, client):
        r = client.get("/bulletin/")
        assert b"Wisconsin" in r.data, (
            "/bulletin/ does not list Wisconsin — state cards vanish"
        )


@pytest.mark.requires_db
class TestBulletinStateCanary:
    def test_state_page_renders(self, client):
        r = client.get(f"/bulletin/{STATE_DIR}/")
        assert r.status_code == 200, (
            f"/bulletin/{STATE_DIR}/ returned {r.status_code} — the whole names "
            f"screen 404s when get_bulletin_stats() returns None"
        )

    def test_names_api_returns_rows(self, client):
        r = client.get(f"/bulletin/{STATE_DIR}/api/names?start=0&length=25")
        assert r.status_code == 200
        payload = r.get_json()
        assert payload["recordsTotal"] > 0, (
            "names API recordsTotal is 0 — the names table renders empty"
        )
        assert len(payload["data"]) > 0, (
            "names API returned no rows — the names table renders empty"
        )

    def test_filters_populated(self, client):
        from app.data_loader import get_bulletin_filters

        f = get_bulletin_filters(STATE_DIR)
        assert f, "filter cache empty — city and church dropdowns vanish"
        assert f["cities"], "no cities — city dropdown vanishes"
        assert f["church_options"], "no churches — church dropdown vanishes"


@pytest.mark.requires_db
class TestMassTimesCanary:
    def test_state_page_lists_churches(self, client):
        r = client.get(f"/mass-times/{STATE_WITH_MASS_TIMES}/")
        assert r.status_code == 200
        assert b"church" in r.data.lower(), (
            "mass-times state page has no churches — the church table vanishes"
        )

    def test_services_present(self):
        from app.data_loader import get_services

        df = get_services(STATE_WITH_MASS_TIMES)
        assert not df.empty, "get_services empty — every mass-times screen vanishes"


@pytest.mark.requires_db
class TestCsvDownloadNotSilentlyTruncated:
    """The CSV had a hardcoded length=500000.

    Wisconsin has 744,120 names, so a download that looked complete was short
    by 244,120 rows with nothing anywhere to say so.
    """

    def test_headers_report_totals(self, client):
        r = client.get(f"/bulletin/{STATE_DIR}/api/names.csv?confidence=high")
        assert r.status_code == 200
        total = int(r.headers["X-Total-Matching-Rows"])
        returned = int(r.headers["X-Rows-Returned"])
        assert total > 0, "CSV matched no rows — the download is empty"
        assert returned == total, (
            f"CSV returned {returned:,} of {total:,} matching rows and did not "
            f"flag it — silent truncation"
        )
        assert "X-Truncated" not in r.headers

    def test_truncation_is_announced_when_a_cap_bites(self, client, monkeypatch):
        """With a deliberately tiny cap, the response must SAY it is short."""
        monkeypatch.setenv("CSV_ROW_CAP", "5")
        r = client.get(f"/bulletin/{STATE_DIR}/api/names.csv?confidence=high")
        assert r.status_code == 200
        assert r.headers.get("X-Truncated") == "true", (
            "a capped download did not set X-Truncated — this is the silent "
            "truncation bug returning"
        )
        assert int(r.headers["X-Rows-Returned"]) == 5
        assert int(r.headers["X-Total-Matching-Rows"]) > 5


class TestFailedLoadIsRetryable:
    """Regression: a failed startup load used to poison the worker forever.

    init_data() set _state_list = [] and returned on any exception, so a worker
    whose boot-time queries timed out served an empty dashboard for its entire
    life. With several gunicorn workers, the site was broken on some requests
    and fine on others — measured at roughly 50/50 on 2026-08-14.
    """

    def test_failure_does_not_mark_data_loaded(self, monkeypatch):
        from app import data_loader as dl

        monkeypatch.setattr(dl, "_data_loaded", False)
        monkeypatch.setattr(dl, "_last_load_attempt", 0.0)

        def boom():
            raise TimeoutError("simulated db99 timeout at worker boot")

        monkeypatch.setattr(dl, "_get_db_connection", boom)

        class _Log:
            def info(self, *a, **k):
                pass

            def warning(self, *a, **k):
                pass

            def error(self, *a, **k):
                pass

        assert dl._load_from_db(_Log()) is False
        assert dl._data_loaded is False, (
            "a failed load marked the worker loaded — it will never retry and "
            "serves an empty dashboard forever"
        )

    @pytest.mark.requires_db
    def test_recovers_on_a_later_call(self, monkeypatch):
        """After a failed load, a normal accessor must repopulate the data."""
        from app import data_loader as dl

        monkeypatch.setattr(dl, "_data_loaded", False)
        monkeypatch.setattr(dl, "_state_list", [])
        monkeypatch.setattr(dl, "_last_load_attempt", 0.0)

        states = dl.get_states_with_bulletins()
        assert states, (
            "get_states_with_bulletins() stayed empty after a failed load — the "
            "worker never recovered, which is the original bug"
        )
        assert dl._data_loaded is True

    @pytest.mark.requires_db
    def test_transient_failure_keeps_previous_data(self, monkeypatch):
        """A blip must not replace a good state list with an empty one."""
        from app import data_loader as dl

        dl.get_states()  # ensure something good is cached
        good = list(dl._state_list or [])
        assert good, "precondition: expected a populated state list"

        monkeypatch.setattr(dl, "_data_loaded", False)
        monkeypatch.setattr(dl, "_last_load_attempt", 0.0)
        monkeypatch.setattr(
            dl, "_get_db_connection", lambda: (_ for _ in ()).throw(TimeoutError("blip"))
        )

        class _Log:
            def info(self, *a, **k):
                pass

            def warning(self, *a, **k):
                pass

            def error(self, *a, **k):
                pass

        dl._load_from_db(_Log())
        assert dl._state_list, (
            "a transient failure wiped the already-good state list — the page "
            "goes blank on a blip instead of serving the last good data"
        )
