"""Regression tests for the db99 connection leak (2026-09-02).

db99 is ONE MySQL instance shared by every project: max_connections=1289,
wait_timeout=28800 (EIGHT HOURS). On 2026-09-02 it reached 1,286 of 1,289 and
began refusing connections with errno 1040, breaking scrapes in five unrelated
projects.

No network access is required by these tests.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dashboard"))


# --- central helper: 1040-only retry, and a close that cannot throw ---------


def test_get_connection_retries_only_1040(monkeypatch):
    import pymysql

    from src.utils import db_connection as dbc

    monkeypatch.setattr(dbc.time, "sleep", lambda s: None)
    monkeypatch.setattr(dbc, "_get_secret", lambda: {})
    monkeypatch.setattr(dbc, "_get_credentials", lambda: ("u", "p"))

    # A non-1040 OperationalError must propagate on the FIRST attempt.
    calls = {"n": 0}

    def unknown_db(**kw):
        calls["n"] += 1
        raise pymysql.err.OperationalError(1049, "Unknown database")

    monkeypatch.setattr(pymysql, "connect", unknown_db)
    with pytest.raises(pymysql.err.OperationalError):
        dbc.get_connection()
    assert calls["n"] == 1, "a non-1040 error must not be retried"

    # 1040 retries, then succeeds.
    state = {"n": 0}
    sentinel = object()

    def flaky(**kw):
        state["n"] += 1
        if state["n"] < 3:
            raise pymysql.err.OperationalError(dbc.ER_CON_COUNT_ERROR, "Too many connections")
        return sentinel

    monkeypatch.setattr(pymysql, "connect", flaky)
    assert dbc.get_connection() is sentinel
    assert state["n"] == 3

    # A 1040 that never clears must raise, not spin forever.
    def always(**kw):
        raise pymysql.err.OperationalError(dbc.ER_CON_COUNT_ERROR, "Too many connections")

    monkeypatch.setattr(pymysql, "connect", always)
    with pytest.raises(pymysql.err.OperationalError):
        dbc.get_connection(attempts=2, base_delay=0)


def test_close_quietly_is_safe_on_none_and_double_close():
    from src.utils.db_connection import close_quietly

    closed = {"n": 0}

    class C:
        def close(self):
            closed["n"] += 1
            if closed["n"] > 1:
                raise RuntimeError("already closed")

    c = C()
    close_quietly(c)
    close_quietly(c)  # must not raise
    close_quietly(None)  # must not raise
    assert closed["n"] == 2


def test_connection_context_manager_closes_on_exception(monkeypatch):
    from src.utils import db_connection as dbc

    closed = {"n": 0}

    class C:
        def close(self):
            closed["n"] += 1

    monkeypatch.setattr(dbc, "get_connection", lambda **kw: C())
    with pytest.raises(ValueError):
        with dbc.connection():
            raise ValueError("boom")
    assert closed["n"] == 1, "context manager leaked on the exception path"


# --- dashboard: teardown net closes what a failing view opened --------------


def test_dashboard_teardown_closes_connections_a_failing_view_opened(monkeypatch):
    """Every data_loader query function closed only on the happy path, inside
    a handler that swallows the exception. The teardown is the safety net."""
    from app import data_loader as dl
    from flask import Flask

    closed = {"n": 0}

    class C:
        def close(self):
            closed["n"] += 1

    monkeypatch.setattr(dl, "_get_secret", lambda: {})
    monkeypatch.setattr(dl, "_get_credentials", lambda: ("u", "p"))
    monkeypatch.setattr(dl, "_connect", lambda *a: C())

    app = Flask(__name__)
    app.teardown_appcontext(dl.close_db99_conns)

    @app.route("/boom")
    def boom():
        try:
            dl._get_db_connection()  # opened, never closed by the view
            raise RuntimeError("query failed")
        except Exception as e:  # the swallow that caused the leak
            return f"error: {e}", 500

    assert app.test_client().get("/boom").status_code == 500
    assert closed["n"] == 1, "connection leaked: teardown did not close it"


def test_create_app_registers_the_teardown():
    """Defining the handler is not enough -- it has to be registered."""
    from app import create_app
    from app import data_loader as dl

    assert callable(dl.close_db99_conns)
    # Registration is asserted structurally so this does not need a live DB.
    import inspect

    src = inspect.getsource(create_app)
    assert "teardown_appcontext(close_db99_conns)" in src


def test_get_db_connection_outside_an_app_context_still_works(monkeypatch):
    """Scripts import this module with no Flask context; must not blow up."""
    from app import data_loader as dl

    class C:
        def close(self):
            pass

    monkeypatch.setattr(dl, "_get_secret", lambda: {})
    monkeypatch.setattr(dl, "_get_credentials", lambda: ("u", "p"))
    monkeypatch.setattr(dl, "_connect", lambda *a: C())

    conn = dl._get_db_connection()  # no app context active
    assert conn is not None


# --- the logger typo that made _load_from_db fail on its first line ---------


def test_data_loader_has_no_undefined_log_calls():
    """`log.info(...)` was used at 5 sites but only `logger` was ever defined.

    _load_from_db()'s FIRST statement was `log.info(...)`, so it raised
    NameError on every call -- and the except handler's `log.error(...)` raised
    NameError too. Committed on master, not a local edit.
    """
    import re

    src = (ROOT / "dashboard" / "app" / "data_loader.py").read_text(encoding="utf-8")
    bad = re.findall(r"(?<![\w.])log\.(?:info|error|warning|debug|exception)\(", src)
    assert not bad, f"{len(bad)} calls to an undefined `log` remain"
