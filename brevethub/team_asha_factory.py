"""Bundled Team Asha Flask factory for BrevetHub's flat Vercel deployment.

Vercel's BrevetHub project uses ``brevethub/`` as its root directory.  The
shared root ``app.py`` therefore collides with BrevetHub's shell at runtime;
keep this deliberately small copy under a distinct module name so the
production function can always import the shared route surface.
"""
import os
from pathlib import Path

from flask import Flask, session
from dotenv import load_dotenv
from config import Config
import db
from cache import init_cache

load_dotenv()


def create_app():
    module_dir = Path(__file__).resolve().parent
    # In the repository the shared templates are one level above BrevetHub;
    # in a flat Vercel bundle they are copied beside this module.
    template_dir = module_dir / 'templates'
    if not (template_dir / 'login.html').exists():
        template_dir = module_dir.parent / 'templates'
    static_dir = module_dir / 'static'
    if not static_dir.exists():
        static_dir = module_dir.parent / 'static'
    app = Flask(__name__, template_folder=str(template_dir), static_folder=str(static_dir))
    app.config.from_object(Config)
    db.init_app(app)
    init_cache(app)

    from routes.auth import init_oauth
    init_oauth(app)
    from routes.main import main_bp
    from routes.riders import riders_bp
    from routes.signup import signup_bp
    from routes.admin import admin_bp
    from routes.auth import auth_bp
    from routes.strava import strava_bp
    from routes.cron import cron_bp
    from routes.chat import chat_bp
    from routes.weather import weather_bp
    from routes.live import live_bp
    from routes.api_auth import api_auth_bp
    from routes.tools import tools_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(riders_bp)
    app.register_blueprint(signup_bp, url_prefix='/signup')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(strava_bp, url_prefix='/strava')
    app.register_blueprint(cron_bp, url_prefix='/api/cron')
    app.register_blueprint(chat_bp)
    app.register_blueprint(weather_bp)
    app.register_blueprint(live_bp)
    app.register_blueprint(api_auth_bp, url_prefix='/api/auth')
    app.register_blueprint(tools_bp, url_prefix='/tools')

    @app.template_filter('commafy')
    def commafy_filter(value):
        try:
            return f"{int(value):,}"
        except (ValueError, TypeError):
            return value

    @app.template_filter('clean_name')
    def clean_name_filter(value):
        if not value:
            return value
        import html
        return html.unescape(str(value)).replace('\xa0', ' ')

    is_local_dev = os.environ.get('FLASK_ENV') == 'development'

    @app.before_request
    def debug_auto_login():
        if not is_local_dev or 'user_id' in session:
            return
        from models import _execute
        try:
            row = _execute(
                "SELECT au.id, au.email, au.rider_id, r.first_name, r.last_name, r.rusa_id "
                "FROM app_user au LEFT JOIN rider r ON r.id = au.rider_id "
                "WHERE au.rider_id IS NOT NULL ORDER BY au.id LIMIT 1"
            ).fetchone()
            if row:
                session['user_id'] = row['id']
                session['email'] = row['email']
                session['rider_id'] = row['rider_id']
                session['rider_name'] = f"{row['first_name']} {row['last_name']}"
                session['rider_rusa_id'] = row['rusa_id']
        except Exception:
            pass

    @app.context_processor
    def inject_helpers():
        from models import get_all_seasons, get_current_season
        try:
            seasons = get_all_seasons()
            current = get_current_season()
        except Exception:
            seasons = [{'id': 3, 'name': '2025-2026', 'is_current': True}]
            current = seasons[0]
        return dict(
            seasons=seasons,
            current_season=current,
            user_logged_in=session.get('user_id') is not None,
            user_email=session.get('email'),
            rider_name=session.get('rider_name'),
            rider_rusa_id=session.get('rider_rusa_id'),
        )

    return app
