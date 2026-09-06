"""`/debug/query` must not reach outside this project's schema.

The route is authenticated, so this is not an open door -- but db99 is ONE
instance shared by every project, and the connection it uses is not scoped to
`church_scrapes`. A `SELECT` with an explicit `otherdb.table` qualifier reads
another project's data: finance, crime, newsmaker. "SELECT only" bounds the
verb, not the blast radius.

What is checked, and why each one:

  - cross-schema qualifiers after FROM/JOIN -- the only syntax that can pull in
    another database's table. Column qualifiers (`c.name`) are deliberately NOT
    matched; an alias is not a schema.
  - `;` -- one statement per request. Without it "SELECT only" is satisfied by
    the first statement while the second does anything it likes.
  - INTO OUTFILE / DUMPFILE / LOAD_FILE -- filesystem reach from inside a
    SELECT, which no data-exploration query needs.
  - an unbounded result set -- a full-table SELECT on a shared instance is a
    denial of service against every other project, not just this one.
"""

import json

import pytest


def _q(client, sql):
    return client.get("/debug/query", query_string={"sql": sql})


@pytest.fixture()
def auth_client(client):
    """/debug/query is behind the login gate; log in so we test the route."""
    from tests.conftest import TEST_PASSWORD, TEST_USER

    client.post("/login", data={"username": TEST_USER, "password": TEST_PASSWORD})
    return client


CROSS_SCHEMA = [
    "SELECT * FROM finance.people",
    "select id from  crime.bookings limit 1",
    "SELECT a.id FROM church c JOIN newsmaker_db.orgs a ON a.id = c.id",
]


@pytest.mark.parametrize("sql", CROSS_SCHEMA)
def test_cross_schema_reads_are_refused(auth_client, sql):
    """Reading another project's database is the actual risk here."""
    resp = _q(auth_client, sql)
    assert (
        resp.status_code == 400
    ), f"cross-schema query was not refused (HTTP {resp.status_code}): {sql}"
    body = json.loads(resp.data)
    assert "error" in body


def test_column_qualifiers_are_not_mistaken_for_schemas(auth_client):
    """Do not over-block: `c.name` is an alias, not a database.

    If this fails the guard is unusable and someone will disable it, which is
    how a security control becomes a comment.
    """
    resp = _q(auth_client, "SELECT c.name FROM church c LIMIT 1")
    assert (
        resp.status_code != 400
    ), f"a normal aliased query was refused; the guard is too broad: {resp.data[:300]}"


def test_multiple_statements_are_refused(auth_client):
    """`SELECT 1; DROP ...` satisfies a startswith('select') check."""
    resp = _q(auth_client, "SELECT 1; SELECT 2")
    assert resp.status_code == 400, "stacked statements were accepted"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM church INTO OUTFILE '/tmp/x'",
        "SELECT LOAD_FILE('/etc/passwd')",
    ],
)
def test_filesystem_reach_is_refused(auth_client, sql):
    resp = _q(auth_client, sql)
    assert resp.status_code == 400, f"filesystem access was accepted: {sql}"


def test_results_are_capped(auth_client):
    """An unbounded SELECT on a shared instance harms every other project."""
    resp = _q(auth_client, "SELECT id FROM church")
    if resp.status_code != 200:
        pytest.skip("db99 unreachable in this environment")
    body = json.loads(resp.data)
    assert body["count"] <= 1000, "returned %d rows uncapped" % body["count"]
    assert "truncated" in body, "a capped result does not say that it was capped"
