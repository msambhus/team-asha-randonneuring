"""BrevetHub Flask app factory.

Standalone from Team Asha: BrevetHub has its own `create_app()`, its own config
namespace, its own DB connection, and its own `rp_*` models. It imports nothing
from Team Asha's `app`, `models`, `routes`, `db`, or `config` — only from the
sibling `shared/` package and its own `brevethub` package. The
`tests/brevethub/test_brevethub_isolation.py` scan enforces that boundary.
"""
import os

from flask import Flask, session
from werkzeug.middleware.proxy_fix import ProxyFix

from brevethub.config import Config


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Vercel terminates TLS at the edge; trust one proxy hop so `_external`
    # URLs (OAuth redirect URIs) are built with the real https host.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    # Per-request DB teardown (rp_* tables only).
    from brevethub import db
    db.init_app(app)

    # Google OAuth — reuses Team Asha's existing client (own redirect URIs).
    from brevethub.routes.auth import init_oauth
    init_oauth(app)

    # Blueprints: main (public shell), auth (Google OAuth), signup
    # (profile completion), clubs (directory API).
    from brevethub.routes.main import main_bp
    from brevethub.routes.auth import auth_bp
    from brevethub.routes.signup import signup_bp
    from brevethub.routes.clubs import clubs_bp
    from brevethub.routes.strava import strava_bp
    from brevethub.routes.live import live_bp
    from brevethub.routes.calendar import calendar_bp
    from brevethub.routes.plan import plan_bp
    from brevethub.routes.analysis import analysis_bp
    from brevethub.routes.cron import cron_bp
    from brevethub.routes.admin import admin_bp
    # BH-native mobile bearer-token mint (login-gated). Server half of a future
    # BrevetHub mobile client; no BH client consumes it yet.
    from brevethub.auth_api import api_auth_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(signup_bp, url_prefix='/signup')
    app.register_blueprint(clubs_bp)
    app.register_blueprint(strava_bp, url_prefix='/strava')
    app.register_blueprint(live_bp)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(plan_bp)
    app.register_blueprint(analysis_bp)
    # cron_bp OWNS the '/cron' segment; the route decorator is leaf-only
    # ('/refresh-calendar') so the composed URL is exactly '/cron/refresh-calendar'
    # (matches vercel.json's cron path). Never put '/cron' in the decorator too.
    app.register_blueprint(cron_bp, url_prefix='/cron')
    # admin_bp OWNS the '/admin' segment; owner-gated real ride-plan generation.
    app.register_blueprint(admin_bp, url_prefix='/admin')
    # Bearer-token mint at /api/auth/token (leaf path in the decorator).
    app.register_blueprint(api_auth_bp)

    @app.context_processor
    def inject_branding():
        # Single source of truth for the product name so templates never need a
        # club name. Deliberately club-agnostic — no parent-app identity anywhere.
        # `user_logged_in`/`user_email` drive the shared base.html nav (the user
        # dropdown vs. the "Sign in" button) from the session alone — no per-request
        # DB lookup. BrevetHub riders have no display name (Google OAuth stores only
        # email), so the nav label is the email; there is no rider-page link.
        return {
            'product_name': 'BrevetHub',
            'user_logged_in': bool(session.get('rider_id')),
            'user_email': session.get('email'),
        }

    return app


# Module-level app for the Vercel entrypoint (api/index.py imports this).
app = create_app()


if __name__ == '__main__':
    # Local dev server on 5001 so it never collides with Team Asha on 5000.
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=True)
