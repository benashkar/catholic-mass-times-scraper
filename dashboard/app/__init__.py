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
        from app.data_loader import _state_list, _bulletin_stats_cache
        return {
            "status": "ok",
            "states_loaded": len(_state_list) if _state_list else 0,
            "bulletin_states": len(_bulletin_stats_cache),
        }, 200

    return app
