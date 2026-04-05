"""Flask app factory for Team Asha Randonneuring."""
from flask import Flask, session
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix
from config import Config
import db
from cache import init_cache

# Load environment variables from .env file
load_dotenv()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Initialize DB
    db.init_app(app)

    # Initialize Cache
    init_cache(app)

    # Initialize OAuth
    from routes.auth import init_oauth
    init_oauth(app)

    # Register blueprints
    from routes.main import main_bp
    from routes.riders import riders_bp
    from routes.signup import signup_bp
    from routes.admin import admin_bp
    from routes.auth import auth_bp
    from routes.strava import strava_bp
    from routes.cron import cron_bp
    from routes.chat import chat_bp
    from routes.weather import weather_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(riders_bp)
    app.register_blueprint(signup_bp, url_prefix='/signup')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(strava_bp, url_prefix='/strava')
    app.register_blueprint(cron_bp, url_prefix='/api/cron')
    app.register_blueprint(chat_bp)
    app.register_blueprint(weather_bp)

    # Template helpers
    @app.template_filter('commafy')
    def commafy_filter(value):
        try:
            return f"{int(value):,}"
        except (ValueError, TypeError):
            return value

    @app.template_filter('clean_name')
    def clean_name_filter(value):
        """Clean HTML entities from ride names (e.g. &nbsp; from web scraping)."""
        if not value:
            return value
        import html as html_mod
        return html_mod.unescape(str(value)).replace('\xa0', ' ')

    # Debug auto-login: simulate an authenticated user in local dev
    @app.before_request
    def debug_auto_login():
        if app.debug and 'user_id' not in session:
            from models import _execute
            try:
                # Pick the first app_user who has a linked rider (for Strava context)
                row = _execute(
                    "SELECT au.id, au.email, au.rider_id, r.first_name, r.last_name "
                    "FROM app_user au "
                    "LEFT JOIN rider r ON r.id = au.rider_id "
                    "WHERE au.rider_id IS NOT NULL "
                    "ORDER BY au.id LIMIT 1"
                ).fetchone()
                if row:
                    session['user_id'] = row['id']
                    session['email'] = row['email']
                    session['rider_id'] = row['rider_id']
                    session['rider_name'] = f"{row['first_name']} {row['last_name']}"
            except Exception:
                pass  # DB not available — skip

    @app.context_processor
    def inject_helpers():
        from models import get_all_seasons, get_current_season
        try:
            # Note: get_all_seasons() and get_current_season() are cached at the model level
            return dict(
                seasons=get_all_seasons(),
                current_season=get_current_season(),
                user_logged_in=session.get('user_id') is not None,
                user_email=session.get('email'),
                rider_name=session.get('rider_name'),
            )
        except Exception:
            # Return mock data if database is not available
            return dict(
                seasons=[
                    {'id': 3, 'name': '2025-2026', 'is_current': True},
                    {'id': 2, 'name': '2022-2023', 'is_current': False},
                    {'id': 1, 'name': '2021-2022', 'is_current': False}
                ],
                current_season={'id': 3, 'name': '2025-2026', 'is_current': True},
                user_logged_in=session.get('user_id') is not None,
                user_email=session.get('email'),
                rider_name=session.get('rider_name'),
            )

    return app


app = create_app()

if __name__ == '__main__':
    app.run(debug=True)
