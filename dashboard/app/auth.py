"""
Login gate for the Catholic Church Dashboard.

Session-cookie auth in front of every page. Credentials come from the
DASHBOARD_USERS env var (set on Render, never committed) in the form:

    DASHBOARD_USERS="user.one@example.com:secret,OtherUser:secret"

Usernames match case-insensitively (they are mostly email addresses);
passwords match exactly, via compare_digest to avoid leaking length/prefix
through timing.

/health and /login stay open — Render's health check and the diagnostic
agent poll /health unauthenticated.
"""

import hmac
import os

from flask import (
    Blueprint,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

bp = Blueprint("auth", __name__)

# Endpoints reachable without a session.
PUBLIC_ENDPOINTS = {"auth.login", "auth.logout", "health", "static"}


def _load_users():
    """Parse DASHBOARD_USERS into {lowercased username: password}."""
    raw = os.environ.get("DASHBOARD_USERS", "")
    users = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        username, password = pair.split(":", 1)
        username = username.strip().lower()
        password = password.strip()
        if username and password:
            users[username] = password
    return users


def check_credentials(username, password):
    """True if username/password match a configured user."""
    users = _load_users()
    expected = users.get((username or "").strip().lower())
    if not expected:
        return False
    return hmac.compare_digest(expected, password or "")


@bp.route("/login", methods=["GET", "POST"])
def login():
    # Where to bounce back to after a successful login.
    nxt = request.args.get("next") or request.form.get("next") or "/"
    # Only allow internal paths — an absolute URL here would be an open redirect.
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"

    if not _load_users():
        return (
            render_template(
                "login.html",
                error="Login is not configured. Set DASHBOARD_USERS on the service.",
                next=nxt,
            ),
            503,
        )

    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if check_credentials(username, password):
            session.permanent = True
            session["user"] = username.strip()
            return redirect(nxt)
        return (
            render_template("login.html", error="Invalid username or password.", next=nxt),
            401,
        )

    return render_template("login.html", error=None, next=nxt)


@bp.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("auth.login"))


def require_login():
    """before_request guard — send anonymous users to /login."""
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    # Unmatched routes (404s) fall through to Flask's own handling.
    if request.endpoint is None:
        return None
    if session.get("user"):
        return None
    return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
