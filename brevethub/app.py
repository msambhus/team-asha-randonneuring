"""BrevetHub Flask app factory.

Standalone from Team Asha: BrevetHub has its own `create_app()`, its own config
namespace, its own DB connection, and its own `rp_*` models. It imports nothing
from Team Asha's `app`, `models`, `routes`, `db`, or `config` — only from the
sibling `shared/` package and its own `brevethub` package. The
`tests/brevethub/test_brevethub_isolation.py` scan enforces that boundary.
"""
import os
import html as html_mod
from datetime import date
from pathlib import Path

from flask import Flask, session
from jinja2 import BaseLoader, ChoiceLoader, TemplateNotFound
from werkzeug.middleware.proxy_fix import ProxyFix

from brevethub.config import Config


def _nav_seasons(today=None):
    """The season names shown in the Riders nav dropdown: the current
    randonneuring season plus the two prior, newest first. Derived from the Nov 1
    boundary so the nav needs no per-club season table."""
    today = today or date.today()
    start = today.year if today.month >= 11 else today.year - 1
    return [f'{y}-{y + 1}' for y in range(start, start - 3, -1)]


class _TeamAshaTemplateLoader(BaseLoader):
    """Expose selected Team Asha templates to BrevetHub.

    This is intentionally narrow: BrevetHub can render the real
    ``templates/strava_ride_analysis.html`` and ``templates/my_strava_analysis.html``
    files, but the rest of Team Asha's templates cannot shadow BrevetHub templates
    such as ``base.html``.
    """

    _TEMPLATE_NAMES = {'strava_ride_analysis.html', 'my_strava_analysis.html'}

    def __init__(self, template_dirs):
        self._template_dirs = [Path(p) for p in template_dirs]

    def get_source(self, environment, template):
        if template not in self._TEMPLATE_NAMES:
            raise TemplateNotFound(template)
        for directory in self._template_dirs:
            path = directory / template
            if path.is_file():
                source = path.read_text(encoding='utf-8')
                mtime = path.stat().st_mtime
                return source, str(path), lambda: path.is_file() and path.stat().st_mtime == mtime
        raise TemplateNotFound(template)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app_dir = Path(__file__).resolve().parent
    team_asha_template_dirs = [
        # BrevetHub-owned packaging copy for Vercel root-directory deployments.
        # Guardrail tests keep these byte-for-byte identical to the Team Asha source.
        app_dir / '_team_asha_templates',
        # Normal repo checkout: <repo>/brevethub/app.py -> <repo>/templates.
        app_dir.parent / 'templates',
        # Flat Vercel layout: app.py and included root templates live together.
        app_dir / 'templates',
    ]
    app.jinja_loader = ChoiceLoader([
        app.jinja_loader,
        _TeamAshaTemplateLoader(team_asha_template_dirs),
    ])

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
    from brevethub.routes.tools import tools_bp
    from brevethub.routes.cron import cron_bp
    from brevethub.routes.admin import admin_bp
    from brevethub.routes.validation import validation_bp
    # Community surfaces (club directory / season rosters / public
    # rider profiles), all club-scoped and login-gated.
    from brevethub.routes.riders import riders_bp
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
    app.register_blueprint(tools_bp, url_prefix='/tools')
    app.register_blueprint(riders_bp)
    # cron_bp OWNS the '/cron' segment; the route decorator is leaf-only
    # ('/refresh-calendar') so the composed URL is exactly '/cron/refresh-calendar'
    # (matches vercel.json's cron path). Never put '/cron' in the decorator too.
    app.register_blueprint(cron_bp, url_prefix='/cron')
    # admin_bp OWNS the '/admin' segment; owner-gated real ride-plan generation.
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(validation_bp)
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
            'user_rider_id': session.get('rider_id'),
            # Seasons for the Riders nav dropdown. Clock-derived, club-agnostic.
            'nav_seasons': _nav_seasons(),
        }

    @app.template_filter('clean_name')
    def clean_name_filter(value):
        if not value:
            return value
        return html_mod.unescape(str(value)).replace('\xa0', ' ')

    @app.template_filter('commafy')
    def commafy_filter(value):
        try:
            return f"{int(value):,}"
        except (ValueError, TypeError):
            return value

    return app


# Module-level app for the Vercel entrypoint (api/index.py imports this).
app = create_app()


if __name__ == '__main__':
    # Local dev server on 5001 so it never collides with Team Asha on 5000.
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=True)
