"""BrevetHub Flask app factory.

Standalone from Team Asha: BrevetHub has its own `create_app()`, its own config
namespace, its own DB connection, and its own `rp_*` models. It imports nothing
from Team Asha's `app`, `models`, `routes`, `db`, or `config` — only from the
sibling `shared/` package and its own `brevethub` package. The
`tests/brevethub/test_brevethub_isolation.py` scan enforces that boundary.
"""
import os

from flask import Flask
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

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(signup_bp, url_prefix='/signup')
    app.register_blueprint(clubs_bp)
    app.register_blueprint(strava_bp, url_prefix='/strava')
    app.register_blueprint(live_bp)
    app.register_blueprint(calendar_bp)

    @app.context_processor
    def inject_branding():
        # Single source of truth for the product name so templates never need a
        # club name. Deliberately club-agnostic — no Team Asha identity anywhere.
        return {'product_name': 'BrevetHub'}

    return app


# Module-level app for the Vercel entrypoint (api/index.py imports this).
app = create_app()


if __name__ == '__main__':
    # Local dev server on 5001 so it never collides with Team Asha on 5000.
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=True)
