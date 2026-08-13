"""End-to-end auth test against the real Flask app (no DB needed for the gate).

Runs under pytest, and still runs standalone (`python tests/test_dashboard_auth.py`).

It used to do all of this at import time and finish with sys.exit(), which made
`pytest tests/` abort during COLLECTION with `SystemExit: 0` — the whole suite,
not just this file. It also set DASHBOARD_USERS to "" as its last act, which
leaked into every other module's requests because auth._load_users() re-reads
the env var per request. Both are why the checks live inside a function now.
"""
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "dashboard"))

# Test-only credentials. The real ones live in DASHBOARD_USERS on the Render
# service and must never be committed — this file used to carry them verbatim.
USER_EMAIL = "auth-test@example.com"
USER_NAME = "AuthTest"
PASSWORD = "auth-test-password"
USERS = f"{USER_EMAIL}:{PASSWORD},{USER_NAME}:{PASSWORD}"

GATED_PATHS = (
    "/", "/bulletin", "/bulletin/OH", "/schema", "/debug/query?sql=select+1",
    "/debug/audit", "/debug/logs", "/mass-times/OH", "/mass-times/OH/calendar/",
    "/bulletin/OH/api/names", "/bulletin/OH/api/names.csv", "/bulletin/suspect/",
)


def _run_auth_checks(verbose=True):
    """Run every auth check; return the list of failed labels."""
    os.environ["DASHBOARD_USERS"] = USERS
    os.environ["SECRET_KEY"] = "test-secret"
    os.environ["SESSION_COOKIE_SECURE"] = "0"  # test client speaks http
    os.environ.setdefault("DB_HOST", "10.10.0.8")

    from app import create_app

    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    fails = []

    def check(label, cond):
        if verbose:
            print(f"  {'[OK] ' if cond else '[FAIL]'} {label}")
        if not cond:
            fails.append(label)

    def section(title):
        if verbose:
            print(f"\n== {title} ==")

    try:
        section("anonymous access is blocked")
        # follow_redirects because Flask's trailing-slash 308 fires during
        # routing, before before_request — /bulletin -> /bulletin/ -> /login.
        with app.test_client() as c:
            for path in GATED_PATHS:
                r = c.get(path, follow_redirects=True)
                landed = r.request.path == "/login" and b'name="password"' in r.data
                check(f"{path} lands on /login", landed)

        section("/health stays public (Render health check)")
        with app.test_client() as c:
            check("/health -> 200", c.get("/health?quick=1").status_code == 200)

        section("login page renders")
        with app.test_client() as c:
            r = c.get("/login")
            check("/login -> 200", r.status_code == 200)
            check("has password field", b'name="password"' in r.data)

        section("wrong password rejected")
        with app.test_client() as c:
            r = c.post("/login", data={"username": USER_NAME, "password": "wrong"})
            check("bad password -> 401", r.status_code == 401)
            r = c.post("/login", data={"username": "nobody@example.com", "password": PASSWORD})
            check("unknown user -> 401", r.status_code == 401)

        section("configured users can sign in and reach the app")
        for user in (USER_EMAIL, USER_NAME):
            with app.test_client() as c:
                r = c.post("/login", data={"username": user, "password": PASSWORD})
                check(f"{user} login -> 302", r.status_code == 302)
                check(f"{user} then / -> 200", c.get("/").status_code == 200)

        section("username is case-insensitive, password is not")
        with app.test_client() as c:
            r = c.post("/login", data={"username": USER_NAME.lower(), "password": PASSWORD})
            check("lowercased username works", r.status_code == 302)
        with app.test_client() as c:
            r = c.post("/login", data={"username": USER_NAME, "password": PASSWORD.upper()})
            check("wrong-case password rejected", r.status_code == 401)

        section("next= redirect is preserved and cannot leave the site")
        with app.test_client() as c:
            r = c.get("/bulletin/")
            check("next= carried", "next=/bulletin/" in r.headers.get("Location", ""))
            r = c.post("/login", data={"username": USER_NAME, "password": PASSWORD,
                                       "next": "https://evil.example.com"})
            check("external next -> / not evil", r.headers.get("Location") == "/")

        section("sign out drops the session")
        with app.test_client() as c:
            c.post("/login", data={"username": USER_NAME, "password": PASSWORD})
            c.get("/logout")
            check("after logout / -> 302", c.get("/").status_code == 302)

        section("unconfigured deployment fails closed")
        os.environ["DASHBOARD_USERS"] = ""
        with app.test_client() as c:
            r = c.post("/login", data={"username": USER_NAME, "password": PASSWORD})
            check("no users configured -> 503, no access", r.status_code == 503)
    finally:
        # Never leave the gate unconfigured for whatever runs next: _load_users
        # reads this per request, so a stray "" fails every later route test.
        os.environ["DASHBOARD_USERS"] = USERS

    return fails


def test_dashboard_auth():
    fails = _run_auth_checks(verbose=False)
    assert not fails, f"auth checks failed: {fails}"


if __name__ == "__main__":
    failed = _run_auth_checks()
    print("\n" + ("ALL PASS" if not failed else f"{len(failed)} FAILED: {failed}"))
    sys.exit(1 if failed else 0)
