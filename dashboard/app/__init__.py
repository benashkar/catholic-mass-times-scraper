"""
Catholic Church Dashboard — Flask Application Factory

Creates and configures the Flask app with three main views:
  1. Home page — US state picker with church counts
  2. Mass Times Browser — state → city → church → services
  3. Bulletin Names Browser — state → city → church → extracted names
"""

import os
import re

from flask import Flask

from app.config import Config

# /health used to run nine aggregate queries against db99 on EVERY request, on
# the instance that hit 1,286 of 1,289 connections on 2026-09-02. Health is
# polled far more often than it changes, so the answer is cached.
#
# Keyed by the `quick` flag, because the two modes return different bodies and
# sharing one slot would serve a table-counts-only answer to a caller asking for
# the full check.
#
# Per gunicorn WORKER, not per service -- see the /health docstring.
_HEALTH_CACHE = {}
_HEALTH_CACHE_TTL = int(os.environ.get("HEALTH_CACHE_TTL_SECONDS", "900"))

# A FAILURE is cached far more briefly than a success, and the two pull in
# opposite directions:
#   - not caching failures at all means every poll during an outage fires nine
#     aggregate queries at a database that is already struggling, and the
#     health check becomes part of the incident;
#   - caching them for the full 15 minutes means the service keeps reporting
#     `faulted` for a quarter of an hour after db99 has recovered, which is the
#     same class of lie this endpoint was just fixed for -- stale, confident,
#     and wrong.
# 30s is short enough to notice recovery promptly and long enough that a
# hammered database is not polled per request.
_HEALTH_FAULT_TTL = int(os.environ.get("HEALTH_FAULT_TTL_SECONDS", "30"))

# /debug/query row cap. An unbounded SELECT on a shared instance is a denial of
# service against every project on db99, not just this one.
_DEBUG_QUERY_MAX_ROWS = int(os.environ.get("DEBUG_QUERY_MAX_ROWS", "1000"))

# The schema /debug/query is allowed to read. db99 hosts every project's
# database on one instance and this connection is not scoped, so without this
# a SELECT can read finance, crime, or newsmaker data.
_OWN_SCHEMA = (os.environ.get("DB_NAME") or "church_scrapes").lower()

# A cross-database table can only enter a query through FROM or JOIN, so that
# is what we check. Column qualifiers (`c.name`) are deliberately NOT matched:
# an alias is not a schema, and a guard that rejects ordinary queries gets
# switched off, which is how a security control becomes a comment.
_QUALIFIED_TABLE_RE = re.compile(
    r"\b(?:from|join)\s+`?([A-Za-z_][A-Za-z0-9_]*)`?\s*\.", re.IGNORECASE
)

# Filesystem reach from inside a SELECT. No data-exploration query needs it.
_FILE_REACH_RE = re.compile(r"\b(?:into\s+(?:out|dump)file|load_file\s*\()", re.IGNORECASE)


def _reject_unsafe_debug_sql(sql):
    """Return a refusal string, or None when the query is in bounds.

    Kept at module level and pure so it can be tested without a database or a
    request context.
    """
    # One statement per request. Without this, "SELECT only" is satisfied by
    # the first statement while the second does whatever it likes.
    if ";" in sql.rstrip().rstrip(";"):
        return "one statement per request (';' not allowed)"

    if _FILE_REACH_RE.search(sql):
        return "filesystem access (OUTFILE/DUMPFILE/LOAD_FILE) is not allowed"

    for schema in _QUALIFIED_TABLE_RE.findall(sql):
        if schema.lower() != _OWN_SCHEMA:
            return (
                f"cross-database access refused: {schema!r} is not {_OWN_SCHEMA}. db99 is shared by "
                "every project and this tool reads only its own schema."
            )
    return None


def create_app(config_class=Config):
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Close every db99 connection opened during a request, however the view
    # exits. db99 is shared by every project (max_connections=1289,
    # wait_timeout=300s as measured 2026-09-04, NOT the 28800 long assumed)
    # and hit 1,286 of 1,289 on 2026-09-02. Twelve call
    # sites in this file and data_loader.py closed only on the happy path,
    # inside handlers that swallow the exception -- so any query error stranded
    # a connection in a gunicorn process that never restarts.
    # Anchoring the close here also covers routes added later.
    from app.data_loader import close_db99_conns

    app.teardown_appcontext(close_db99_conns)

    # Initialize the data loader (loads master church list on startup)
    from app.data_loader import init_data

    init_data(app)

    # Login gate — registered before the content blueprints so every view below
    # is behind it. /health and /login stay public (see auth.PUBLIC_ENDPOINTS).
    from app.auth import bp as auth_bp
    from app.auth import require_login

    app.register_blueprint(auth_bp)
    app.before_request(require_login)

    # Register blueprints
    from app.routes.bulletin import bp as bulletin_bp
    from app.routes.main import bp as main_bp
    from app.routes.mass_times import bp as mass_times_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(mass_times_bp)
    app.register_blueprint(bulletin_bp)

    @app.route("/schema")
    def schema():
        """Show database schema, ERD, and connection info."""
        from flask import render_template

        from app.data_loader import _get_db_connection

        table_info = [
            ("church", "Catholic churches with location, contact, service counts"),
            ("service", "Mass times, confessions, adoration — recurring and one-time"),
            ("bulletin_source", "Which churches have bulletin pages (one per church)"),
            ("bulletin_pdf", "Downloaded bulletin PDFs with extraction status"),
            ("bulletin_name", "Names extracted from bulletins with confidence scores"),
            ("bulletin_state_stats", "Pre-computed stats per state (dashboard cards)"),
            ("ref_ssa_names", "SSA first names with gender data (scoring reference)"),
            ("ref_census_surnames", "Census last names with frequency (scoring reference)"),
            ("scrape_log", "Audit trail of every pipeline run"),
            ("lk_state", "US state codes and names"),
        ]
        tables = []
        try:
            conn = _get_db_connection()
            cur = conn.cursor()
            for name, desc in table_info:
                try:
                    cur.execute(f"SELECT COUNT(*) AS cnt FROM {name}")
                    count = cur.fetchone()["cnt"]
                except Exception:
                    count = 0
                tables.append({"name": name, "count": count, "description": desc})
            conn.close()
        except Exception:
            for name, desc in table_info:
                tables.append({"name": name, "count": 0, "description": desc})

        return render_template("schema.html", tables=tables)

    @app.route("/schema/erd.html")
    def schema_erd():
        """Serve standalone ERD HTML file."""
        import os

        from flask import send_from_directory

        docs_dir = os.path.join(os.path.dirname(__file__), "..", "..", "docs")
        return send_from_directory(os.path.abspath(docs_dir), "erd.html")

    @app.route("/livez")
    def livez():
        """Liveness: is this process alive? Never touches the database.

        This is what a platform restart trigger must point at. /health checks
        db99, and db99 is shared by every project -- pointing the restart
        trigger there means one shared-database blip restarts every worker,
        repeatedly, when a restart cannot possibly fix another host's database.

        Always 200 unless the process is unrecoverable, in which case it is not
        answering at all.
        """
        return {"status": "alive", "service": "church-dashboard"}, 200

    @app.route("/health")
    def health():
        """Readiness + diagnostics, cached.

        THREE THINGS THIS FIXES (all present before 2026-09-05):

        1. It returned HTTP 200 while the body said `status: "error"`. Every
           uptime monitor and platform probe reads the status code, so a dead
           database presented as fine. The contract now is:
               ok        200  everything configured is working
               degraded  200  something is wrong that a RESTART CANNOT FIX
               faulted   503  the service genuinely cannot do its job
           Data-quality issues (junk names, stale scrapes) are `degraded` and
           deliberately still 200 -- they are real and worth alerting on, and
           bouncing the container does nothing for them. Only an unreachable
           database is `faulted`.

        2. It ran NINE aggregate queries synchronously per request, against a
           shared instance that hit 1,286 of 1,289 connections on 2026-09-02.
           Health is polled far more often than it changes, so the answer is
           cached for HEALTH_CACHE_TTL_SECONDS (default 900).

        3. There was no liveness/readiness split -- see /livez above.

        The cache is per gunicorn WORKER, not per service: N workers means at
        most N computations per TTL, not one. That is fine here, and is said
        out loud so nobody reads a low query count as a service-wide guarantee.

        `?fresh=1` bypasses the cache (for the diagnostic agent).
        `?quick=1` runs table counts only.
        """
        import datetime as _dt
        import os
        import sys
        import time

        # src/ sits one level up in the container (/app/src, next to /app/app)
        # but two levels up in the repo (dashboard/app -> repo root). Add both
        # so this resolves in development and in the deployed image.
        _here = os.path.dirname(__file__)
        for _candidate in (os.path.join(_here, ".."), os.path.join(_here, "..", "..")):
            _candidate = os.path.abspath(_candidate)
            if os.path.isdir(os.path.join(_candidate, "src")) and _candidate not in sys.path:
                sys.path.insert(0, _candidate)
        from flask import request

        # Imported as a MODULE, not as names: the tests patch
        # data_loader._get_db_connection, and `from ... import _get_db_connection`
        # would bind the original function here and sail straight past the patch.
        from app import data_loader
        from src.utils.health_checks import run_all_checks

        # QUICK BY DEFAULT since 2026-09-06. The deep sweep is a DIAGNOSTIC, not
        # a health check, and it could not finish: /health returned HTTP 500
        # after 122s -- the gunicorn worker timeout -- because nine aggregates
        # over a 35.6M-row bulletin_name table do not complete in time. Meanwhile
        # ?quick=1 answered in 16s.
        #
        # Caching it, added earlier the same day, did NOT fix that, and it is
        # worth being exact about why: a cache only helps AFTER one successful
        # computation, and there had never been one. An endpoint monitors poll
        # has to answer reliably every time, not eventually.
        #
        # ?full=1 asks for the deep sweep explicitly and accepts the wait.
        # ?quick=1 keeps working for anything that already passes it.
        full = request.args.get("full", "").lower() in ("1", "true", "yes")
        quick = not full
        fresh = request.args.get("fresh", "").lower() in ("1", "true", "yes")
        now = time.time()

        cached = _HEALTH_CACHE.get(quick)
        if not fresh and cached:
            ttl = _HEALTH_FAULT_TTL if cached["code"] >= 500 else _HEALTH_CACHE_TTL
            if (now - cached["built_at"]) < ttl:
                payload = dict(cached["payload"])
                payload["cache_age_seconds"] = round(now - cached["built_at"], 1)
                payload["cached"] = True
                return payload, cached["code"]

        result = {
            "states_loaded": len(data_loader._state_list) if data_loader._state_list else 0,
            "bulletin_states": len(data_loader._bulletin_stats_cache),
        }
        conn = None
        try:
            conn = data_loader._get_db_connection()
            cur = conn.cursor()
            result["db"] = "connected"
            checks = run_all_checks(cur, quick=quick)
            result.update(checks)
            # run_all_checks reports "healthy"/"degraded" about the DATA. Map it
            # onto the service contract -- neither of those is a service fault.
            result["status"] = "ok" if checks.get("status") == "healthy" else "degraded"
            code = 200
        except Exception as e:  # noqa: BLE001 -- a health check must never throw
            # An unhandled exception here is indistinguishable from a dead
            # service, which is the one thing this endpoint must never look like.
            result["status"] = "faulted"
            result["db"] = f"error: {str(e)[:200]}"
            code = 503
        finally:
            # teardown_appcontext also closes this. The explicit close returns
            # the slot to db99 now rather than at the end of the request.
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

        result["built_at"] = _dt.datetime.now(_dt.UTC).isoformat()
        result["cache_age_seconds"] = 0.0
        result["cached"] = False
        _HEALTH_CACHE[quick] = {"built_at": now, "payload": result, "code": code}
        return result, code

    @app.route("/debug/query")
    def debug_query():
        """Run a read-only SQL query for data exploration."""
        from flask import jsonify, request

        from app.data_loader import _get_db_connection

        sql = request.args.get("sql", "")
        if not sql:
            return jsonify({"error": "?sql= required"}), 400
        sql_lower = sql.strip().lower()
        if not sql_lower.startswith("select"):
            return jsonify({"error": "SELECT only"}), 400

        # "SELECT only" bounds the VERB, not the blast radius. db99 is one
        # instance shared by every project and this connection is not scoped to
        # church_scrapes, so `SELECT * FROM finance.people` reads another
        # project's data. The route is authenticated, so this is not an open
        # door -- but a data-exploration tool has no business reaching sideways.
        refusal = _reject_unsafe_debug_sql(sql)
        if refusal:
            return jsonify({"error": refusal}), 400

        conn = None
        try:
            conn = _get_db_connection()
            cur = conn.cursor()
            cur.execute(sql)
            # Cap the read. An unbounded SELECT against a shared instance is a
            # denial of service against every other project, not just this one.
            rows = cur.fetchmany(_DEBUG_QUERY_MAX_ROWS + 1)
            truncated = len(rows) > _DEBUG_QUERY_MAX_ROWS
            rows = rows[:_DEBUG_QUERY_MAX_ROWS]
            return jsonify(
                {
                    "rows": rows,
                    "count": len(rows),
                    # Say so. A silently truncated result read as complete is how
                    # someone concludes a table is empty when it is not.
                    "truncated": truncated,
                    "max_rows": _DEBUG_QUERY_MAX_ROWS,
                }
            )
        except Exception as e:
            return jsonify({"error": str(e)[:500]}), 500
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    @app.route("/debug/audit")
    def debug_audit():
        """Return random high-confidence names for manual spot-checking."""
        from flask import jsonify, request

        from app.data_loader import _get_db_connection

        try:
            n = min(int(request.args.get("n", 50)), 200)
            conn = _get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT bn.bulletin_name_id, bn.person_name, bn.first_name, "
                "bn.last_name, bn.confidence, bn.category, c.name AS church, "
                "c.city, c.state_code "
                "FROM bulletin_name bn "
                "JOIN bulletin_pdf bp ON bn.bulletin_pdf_id = bp.bulletin_pdf_id "
                "JOIN bulletin_source bs ON bp.bulletin_source_id = bs.bulletin_source_id "
                "JOIN church c ON bs.church_id = c.church_id "
                "WHERE bn.confidence IN ('high','medium') AND bn.is_suspect = 0 "
                "ORDER BY RAND() LIMIT %s",
                (n,),
            )
            rows = cur.fetchall()
            conn.close()
            return jsonify({"rows": rows, "count": len(rows)})
        except Exception as e:
            return jsonify({"error": str(e)[:500]}), 500

    @app.route("/debug/logs")
    def debug_logs():
        """Show recent scrape_log entries for debugging cron job failures."""
        from flask import request

        from app.data_loader import _get_db_connection

        try:
            conn = _get_db_connection()
            cur = conn.cursor()
            scrape_type = request.args.get("type")
            limit = min(int(request.args.get("limit", 20)), 100)
            if scrape_type:
                cur.execute(
                    "SELECT * FROM scrape_log WHERE scrape_type = %s "
                    "ORDER BY completed_at DESC LIMIT %s",
                    (scrape_type, limit),
                )
            else:
                cur.execute(
                    "SELECT * FROM scrape_log ORDER BY completed_at DESC LIMIT %s",
                    (limit,),
                )
            rows = cur.fetchall()
            conn.close()
            return {"logs": rows}, 200
        except Exception as e:
            return {"error": str(e)[:500]}, 500

    return app
