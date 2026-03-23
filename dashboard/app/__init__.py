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

    @app.route("/health")
    def health():
        from app.data_loader import _state_list, _bulletin_stats_cache, _get_db_connection

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
