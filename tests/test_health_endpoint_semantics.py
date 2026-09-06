"""`/health` must not lie about being healthy, and must not be the restart trigger.

Three defects this pins down, all of which the crime dashboard had too:

1. **It returned HTTP 200 while reporting `status: "error"`.** A caller that
   checks the status code -- which is every uptime monitor and every platform
   probe -- read a dead database as fine. The body told the truth and the code
   contradicted it, and the code is the part machines read.

2. **It had no liveness/readiness split.** Pointing a platform's restart trigger
   at a dependency-checking endpoint is how you get a restart loop: db99 goes
   down, every worker fails its probe, the platform restarts them all, and the
   restarts cannot fix another host's database. `/livez` must answer "is this
   process alive?" without touching the DB.

3. **It ran nine aggregate queries synchronously on every request.** That is
   both a crash-loop risk on a shared instance and a violation of the
   precompute rule -- health is checked far more often than it changes.

The status contract (see the health-endpoint-liveness-semantics skill):

    ok        200   everything configured is working
    degraded  200   something is wrong but a restart cannot fix it
    faulted   503   the service genuinely cannot do its job

`degraded` returning 200 is the important line. Data-quality issues -- junk
names, stale scrapes -- are `degraded`: real, worth alerting on, and completely
unaffected by bouncing the container.
"""

import json


def _get(client, path):
    return client.get(path)


def test_livez_exists_and_never_touches_the_database(app, client):
    """Liveness must answer without a DB, or it cannot survive a db99 outage."""
    resp = _get(client, "/livez")
    assert (
        resp.status_code == 200
    ), f"/livez must return 200 whenever the process is alive; got {resp.status_code}"
    body = json.loads(resp.data)
    assert body.get("status") == "alive"
    # The whole point: no DB key, because it must not have asked.
    assert "db" not in body, (
        "/livez consulted the database. A liveness probe that depends on db99 "
        "restarts every worker when db99 blips, and the restart cannot help."
    )


def test_livez_is_public(app):
    """A liveness probe behind a login gate is a liveness probe that fails."""
    from app.auth import PUBLIC_ENDPOINTS

    assert "livez" in PUBLIC_ENDPOINTS, (
        "livez is not in PUBLIC_ENDPOINTS, so the platform probe gets a 302 to "
        "/login and reads the service as broken"
    )


def test_health_reports_faulted_with_503_when_the_db_is_unreachable(app, client, monkeypatch):
    """The status code must agree with the body.

    Against the pre-fix route this FAILS: it returned 200 with
    {"status": "error"}.
    """
    import app as app_pkg  # noqa: F401
    from app import data_loader

    def _boom(*a, **kw):
        raise RuntimeError("simulated db99 outage")

    monkeypatch.setattr(data_loader, "_get_db_connection", _boom)

    resp = _get(client, "/health?fresh=1")
    body = json.loads(resp.data)

    assert (
        body.get("status") == "faulted"
    ), "expected status 'faulted' when the DB is unreachable, got {!r}".format(body.get("status"))
    assert resp.status_code == 503, (
        f"/health returned HTTP {resp.status_code} while reporting the database unreachable. "
        "Every monitor that checks the status code reads that as healthy."
    )


def test_health_is_cached_so_it_is_not_nine_aggregates_per_request(app, client, monkeypatch):
    """Two calls in a row must hit the DB at most once.

    Health is polled far more often than it changes, and the checks are
    GROUP BY aggregates over the whole table on a SHARED database instance.

    Deliberately does NOT use the real connection. An earlier version wrapped
    the live `_get_db_connection`, and when db99 was unreachable the test hung
    for minutes on TCP retries and stalled the whole suite. A test that hangs
    when a dependency is down is a test that will be deleted. Stubbing also
    makes the count exact instead of environment-dependent.
    """
    import app as app_pkg
    from app import data_loader

    # Cross-test isolation: another test may have primed the cache.
    app_pkg._HEALTH_CACHE.clear()

    calls = {"n": 0}

    def _stub(*a, **kw):
        calls["n"] += 1
        raise RuntimeError("stubbed -- no real database in this test")

    monkeypatch.setattr(data_loader, "_get_db_connection", _stub)

    _get(client, "/health?fresh=1")  # prime; this one must reach the stub
    assert calls["n"] == 1, "the priming call did not reach the database"

    # The primed entry is a FAULT, which is cached only briefly by design --
    # long enough to prove caching happens, short enough to notice recovery.
    _get(client, "/health")
    _get(client, "/health")
    assert calls["n"] == 1, (
        "/health opened %d connection(s) across two cached calls; the nine "
        "aggregates are still running per request" % calls["n"]
    )


def test_a_faulted_answer_is_not_cached_for_the_full_ttl(app, client, monkeypatch):
    """Recovery must be noticed promptly.

    Caching a failure for the full 15 minutes would keep reporting `faulted`
    long after db99 recovered -- stale, confident and wrong, which is the same
    class of lie this endpoint was just fixed for. Caching it for nothing at
    all would fire nine aggregates per poll at a database already in trouble.
    """
    import app as app_pkg

    assert app_pkg._HEALTH_FAULT_TTL < app_pkg._HEALTH_CACHE_TTL, (
        "a failed health answer is cached as long as a successful one, so the "
        "service keeps reporting faulted after the database has recovered"
    )
    assert (
        app_pkg._HEALTH_FAULT_TTL <= 60
    ), f"fault TTL of {app_pkg._HEALTH_FAULT_TTL}s is too long to notice a recovery"


def test_health_body_says_when_it_was_built(app, client, monkeypatch):
    """A cached health answer that hides its age is worse than an uncached one.

    Without this the reader cannot tell a live 'ok' from one computed before
    the outage started.

    Stubbed rather than hitting db99: this must not depend on a database being
    reachable, and must never hang waiting for one.
    """
    from app import data_loader

    monkeypatch.setattr(
        data_loader,
        "_get_db_connection",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("stubbed")),
    )

    resp = _get(client, "/health?fresh=1")
    body = json.loads(resp.data)
    assert "built_at" in body, "/health does not report when it was built"
    assert "cache_age_seconds" in body, "/health does not report its age"
