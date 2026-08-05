"""End-to-end auth test against the real Flask app (no DB needed for the gate)."""
import os, sys

os.environ["DASHBOARD_USERS"] = "ben.ashkar@locallabs.com:Churches2026,RPAC:Churches2026"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["SESSION_COOKIE_SECURE"] = "0"   # test client speaks http
os.environ.setdefault("DB_HOST", "10.10.0.8")

sys.path.insert(0, r"C:\Users\cashk\OneDrive\Projects\church scrapes\dashboard")
from app import create_app

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False
fails = []


def check(label, cond):
    print(f"  {'[OK] ' if cond else '[FAIL]'} {label}")
    if not cond:
        fails.append(label)


print("\n== anonymous access is blocked ==")
# follow_redirects because Flask's trailing-slash 308 fires during routing,
# before before_request — /bulletin -> /bulletin/ -> /login.
with app.test_client() as c:
    for path in ("/", "/bulletin", "/bulletin/OH", "/schema", "/debug/query?sql=select+1",
                 "/debug/audit", "/debug/logs", "/mass-times/OH",
                 "/mass-times/OH/calendar/", "/bulletin/OH/api/names",
                 "/bulletin/OH/api/names.csv", "/bulletin/suspect/"):
        r = c.get(path, follow_redirects=True)
        landed_on_login = r.request.path == "/login" and b'name="password"' in r.data
        check(f"{path} lands on /login", landed_on_login)

print("\n== /health stays public (Render health check) ==")
with app.test_client() as c:
    r = c.get("/health?quick=1")
    check("/health -> 200", r.status_code == 200)

print("\n== login page renders ==")
with app.test_client() as c:
    r = c.get("/login")
    check("/login -> 200", r.status_code == 200)
    check("has password field", b'name="password"' in r.data)

print("\n== wrong password rejected ==")
with app.test_client() as c:
    r = c.post("/login", data={"username": "RPAC", "password": "wrong"})
    check("bad password -> 401", r.status_code == 401)
    r = c.post("/login", data={"username": "nobody@example.com", "password": "Churches2026"})
    check("unknown user -> 401", r.status_code == 401)

print("\n== both real users can sign in and reach the app ==")
for user in ("ben.ashkar@locallabs.com", "RPAC"):
    with app.test_client() as c:
        r = c.post("/login", data={"username": user, "password": "Churches2026"})
        check(f"{user} login -> 302", r.status_code == 302)
        r = c.get("/")
        check(f"{user} then / -> 200", r.status_code == 200)

print("\n== username is case-insensitive, password is not ==")
with app.test_client() as c:
    r = c.post("/login", data={"username": "rpac", "password": "Churches2026"})
    check("lowercase 'rpac' works", r.status_code == 302)
with app.test_client() as c:
    r = c.post("/login", data={"username": "RPAC", "password": "churches2026"})
    check("lowercase password rejected", r.status_code == 401)

print("\n== next= redirect is preserved and cannot leave the site ==")
with app.test_client() as c:
    r = c.get("/bulletin/")
    check("next= carried", "next=/bulletin/" in r.headers.get("Location", ""))
    r = c.post("/login", data={"username": "RPAC", "password": "Churches2026",
                               "next": "https://evil.example.com"})
    check("external next -> / not evil", r.headers.get("Location") == "/")

print("\n== sign out drops the session ==")
with app.test_client() as c:
    c.post("/login", data={"username": "RPAC", "password": "Churches2026"})
    c.get("/logout")
    r = c.get("/")
    check("after logout / -> 302", r.status_code == 302)

print("\n== unconfigured deployment fails closed ==")
os.environ["DASHBOARD_USERS"] = ""
with app.test_client() as c:
    r = c.post("/login", data={"username": "RPAC", "password": "Churches2026"})
    check("no users configured -> 503, no access", r.status_code == 503)

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
