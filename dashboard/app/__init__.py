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
    from app.routes.main import bp as main_bp
    from app.routes.mass_times import bp as mass_times_bp
    from app.routes.bulletin import bp as bulletin_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(mass_times_bp)
    app.register_blueprint(bulletin_bp)

    @app.route("/health")
    def health():
        return {"status": "ok"}, 200

    return app
