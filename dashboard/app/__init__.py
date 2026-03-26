"""
Catholic Church Dashboard — Flask Application Factory

Creates and configures the Flask app with three main views:
  1. Home page — US state picker with church counts
  2. Mass Times Browser — state → city → church → services
  3. Bulletin Names Browser — state → city → church → extracted names
"""

from flask import Flask

from app.config import Config


def create_app(config_class=Config):
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize the data loader (loads master church list on startup)
    from app.data_loader import init_data

    init_data(app)

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

    @app.route("/health")
    def health():
        from app.data_loader import _bulletin_stats_cache, _get_db_connection, _state_list

        result = {
            "status": "ok",
            "states_loaded": len(_state_list) if _state_list else 0,
            "bulletin_states": len(_bulletin_stats_cache),
        }
        # Live DB check
        try:
            conn = _get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS cnt FROM church")
            result["db_churches"] = cur.fetchone()["cnt"]
            result["db"] = "connected"
            conn.close()
        except Exception as e:
            result["db"] = f"error: {str(e)[:200]}"
        return result, 200

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
        try:
            conn = _get_db_connection()
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
            conn.close()
            return jsonify({"rows": rows, "count": len(rows)})
        except Exception as e:
            return jsonify({"error": str(e)[:500]}), 500

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
