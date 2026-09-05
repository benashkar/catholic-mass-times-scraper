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
    assert resp.status_code == 200, (
        "/livez must return 200 whenever the process is alive; got %s"
        % resp.status_code
    )
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

    assert body.get("status") == "faulted", (
        "expected status 'faulted' when the DB is unreachable, got %r" % body.get("status")
    )
    assert resp.status_code == 503, (
        "/health returned HTTP %s while reporting the database unreachable. "
        "Every monitor that checks the status code reads that as healthy."
        % resp.status_code
    )


def test_health_is_cached_so_it_is_not_nine_aggregates_per_request(app, client, monkeypatch):
    """Two calls in a row must hit the DB at most once.

    Health is polled far more often than it changes, and the checks are
    GROUP BY aggregates over the whole table on a SHARED database instance.
    """
    from app import data_loader

    calls = {"n": 0}
    real = data_loader._get_db_connection

    def _counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(data_loader, "_get_db_connection", _counting)

    _get(client, "/health?fresh=1")   # prime, counts as 1
    before = calls["n"]
    _get(client, "/health")           # must be served from cache
    _get(client, "/health")
    assert calls["n"] == before, (
        "/health opened %d more connection(s) across two cached calls; the "
        "nine aggregates are still running per request"
        % (calls["n"] - before)
    )


def test_health_body_says_when_it_was_built(app, client):
    """A cached health answer that hides its age is worse than an uncached one.

    Without this the reader cannot tell a live 'ok' from one computed before
    the outage started.
    """
    resp = _get(client, "/health")
    body = json.loads(resp.data)
    assert "built_at" in body, "cached /health does not report when it was built"
    assert "cache_age_seconds" in body, "cached /health does not report its age"
