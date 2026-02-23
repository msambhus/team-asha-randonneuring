"""Flask app factory for Team Asha Randonneuring."""
from flask import Flask
from config import Config
import db


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Initialize DB
    db.init_app(app)

    # Register blueprints
    from routes.main import main_bp
    from routes.riders import riders_bp
    from routes.signup import signup_bp
    from routes.admin import admin_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(riders_bp)
    app.register_blueprint(signup_bp, url_prefix='/signup')
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # Template helpers
    @app.template_filter('commafy')
    def commafy_filter(value):
        try:
            return f"{int(value):,}"
        except (ValueError, TypeError):
            return value

    @app.context_processor
    def inject_helpers():
        from models import get_all_seasons, get_current_season
        return dict(
            seasons=get_all_seasons(),
            current_season=get_current_season(),
        )

    return app


app = create_app()

if __name__ == '__main__':
    app.run(debug=True)
