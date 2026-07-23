"""BrevetHub rebuilt on the Team Asha application surface.

The old standalone BrevetHub implementation is archived under
``archive/brevethub_legacy_20260723``. The active BrevetHub app now reuses Team
Asha's proven routes, services, models, templates, Strava analysis, live tracking,
weather, tools, and admin surfaces instead of carrying a divergent copy.

BrevetHub-specific behavior lives here as a thin shell:

* neutral BrevetHub templates are preferred over Team Asha templates;
* the About page is removed;
* ``/calendar`` aliases to the current-season brevet calendar;
* a BrevetHub admin action can auto-generate ride plans for every ride with an
  RWGPS URL, reusing the existing RWGPS plan engine.
"""
import os
import importlib.util
import sys
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, request, session, url_for
from jinja2 import BaseLoader, ChoiceLoader, FileSystemLoader


_BREVETHUB_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _BREVETHUB_DIR.parent


class _BrevetHubNeutralizingLoader(BaseLoader):
    """Reuse Team Asha templates while neutralizing labels and brand colors."""

    _REPLACEMENTS = (
        ('Team <span class="text-accent-light">Asha</span> Randonneuring',
         'Brevet<span class="text-accent-light">Hub</span>'),
        ('Team Asha Randonneuring', 'BrevetHub'),
        ('Team Asha Coaches', 'BrevetHub Coaches'),
        ('Team Asha Route URL', 'Alternate RWGPS URL'),
        ('Team Asha Route', 'Alternate Route'),
        ('Team Asha route', 'alternate route'),
        ('Team Asha riders', 'randonneurs'),
        ('Team Asha rider', 'randonneur'),
        ('Team Asha', 'BrevetHub'),
    )
    _COLOR_REPLACEMENTS = (
        ('#1a365d', '#0088ce'),
        ('#234878', '#006aa3'),
        ('#2a4a7f', '#006aa3'),
        ('#2d5a87', '#006aa3'),
        ('#e53e3e', '#c01700'),
        ('#fc8181', '#ec0000'),
        ('#ff6b6b', '#ec0000'),
        ('#ee5a6f', '#a01300'),
        ('#0891b2', '#0088ce'),
        ('#0e7490', '#006aa3'),
        ('#0f766e', '#006aa3'),
        ('#0369a1', '#006aa3'),
        ('#38bdf8', '#79D1FF'),
        ('#7dd3fc', '#0088ce'),
        ('#805ad5', '#0088ce'),
        ('#b794f4', '#e6f7ff'),
        ('#7c3aed', '#0088ce'),
        ('#6d28d9', '#0088ce'),
        ('#4338ca', '#006aa3'),
        ('#c7d2fe', '#0088ce'),
        ('#d1fae5', '#e6f7ff'),
        ('#ecfdf5', '#e6f7ff'),
        ('#e6fffa', '#e6f7ff'),
        ('#f0fff4', '#e6f7ff'),
        ('#38a169', '#0088ce'),
        ('#48bb78', '#79D1FF'),
        ('#16a34a', '#0088ce'),
        ('#059669', '#006aa3'),
        ('#065f46', '#006aa3'),
        ('#fb923c', '#c01700'),
        ('#f97316', '#a01300'),
        ('#ff6b00', '#c01700'),
        ('#ff8c42', '#ec0000'),
        ('#c2410c', '#c01700'),
        ('#9a3412', '#a01300'),
        ('#f0f9ff', '#e6f7ff'),
        ('#e0f2fe', '#e6f7ff'),
        ('#ebf8ff', '#e6f7ff'),
        ('#e8f0fe', '#e6f7ff'),
        ('#f0f4ff', '#e6f7ff'),
        ('#fff7ed', '#fff0f3'),
        ('#ffedd5', '#fff0f3'),
        ('#fed7aa', '#c01700'),
        ('#fff5f0', '#fff0f3'),
        ('#fff5f5', '#fff0f3'),
        ('#fed7d7', '#fff0f3'),
        ('#fef5e7', '#fff0f3'),
        ('#fefcbf', '#fff0f3'),
        ('#fef3c7', '#fff0f3'),
        ('#fde68a', '#fff0f3'),
        ('#fbbf24', '#c01700'),
        ('#dd6b20', '#c01700'),
        ('#ed8936', '#ec0000'),
        ('#d69e2e', '#c01700'),
        ('#f6ad55', '#ec0000'),
    )

    def __init__(self, wrapped):
        self._wrapped = wrapped

    def get_source(self, environment, template):
        source, filename, uptodate = self._wrapped.get_source(environment, template)
        for old, new in self._REPLACEMENTS:
            source = source.replace(old, new)
        for old, new in self._COLOR_REPLACEMENTS:
            source = source.replace(old, new).replace(old.upper(), new)
        return source, filename, uptodate


def _remove_endpoint(app, endpoint):
    """Remove a route endpoint from Flask's URL map.

    Flask has no public unregister API. This is intentionally limited to the
    inherited Team Asha About route so BrevetHub has no About page or nav item.
    """
    rules = [rule for rule in app.url_map.iter_rules() if rule.endpoint == endpoint]
    if not rules:
        return
    for rule in rules:
        app.url_map._rules.remove(rule)  # noqa: SLF001 - deliberate one-route removal
        app.url_map._rules_by_endpoint.get(endpoint, []).remove(rule)  # noqa: SLF001
    if not app.url_map._rules_by_endpoint.get(endpoint):
        app.url_map._rules_by_endpoint.pop(endpoint, None)  # noqa: SLF001
    app.view_functions.pop(endpoint, None)


def _register_brevethub_routes(app):
    overrides = Blueprint('brevethub_overrides', __name__)

    @overrides.route('/calendar')
    def calendar_alias():
        from models import get_current_season

        current = get_current_season()
        if not current:
            abort(404)
        return redirect(url_for('riders.upcoming_brevets', season_name=current['name']))

    @overrides.route('/admin/auto-generate-plans', methods=['POST'])
    def auto_generate_plans():
        """Generate ride plans for rides that already have RWGPS URLs.

        This preserves the current BrevetHub behavior request: when a ride has a
        RideWithGPS link, try to create a plan automatically using the same shared
        RWGPS engine Team Asha/BrevetHub already use.
        """
        from routes.riders import is_admin_user

        if not session.get('user_id'):
            return redirect(url_for('auth.login', next=request.path))
        if not is_admin_user():
            abort(403)

        result = _auto_generate_plans_for_rwgps_rides()
        flash(
            f"Auto plan generation complete: {result['created']} created, "
            f"{result['skipped']} skipped, {result['failed']} failed.",
            'success' if result['failed'] == 0 else 'warning',
        )
        return redirect(url_for('admin.dashboard'))

    @overrides.route('/api/cron/auto-generate-plans', methods=['GET', 'POST'])
    def cron_auto_generate_plans():
        """Cron endpoint for RWGPS-backed ride plan creation."""
        from routes.cron import _verify_cron_auth

        auth_error = _verify_cron_auth()
        if auth_error:
            return auth_error
        result = _auto_generate_plans_for_rwgps_rides()
        return jsonify({'ok': True, **result}), 200

    app.register_blueprint(overrides)


def _auto_generate_plans_for_rwgps_rides():
    from models import _execute, create_ride_plan_from_rwgps, get_db, get_ride_plan_by_rwgps_route_id
    from services.rwgps import build_ride_plan, extract_controls, extract_rwgps_route_id, fetch_route

    rows = _execute("""
        SELECT id, name, rwgps_url, rwgps_url_team, ride_plan_id, start_time
        FROM ride
        WHERE COALESCE(rwgps_url_team, rwgps_url) IS NOT NULL
        ORDER BY date DESC NULLS LAST, id DESC
    """).fetchall()

    api_key = current_app.config.get('RWGPS_API_KEY')
    auth_token = current_app.config.get('RWGPS_AUTH_TOKEN')
    conn = get_db()
    created = skipped = failed = 0

    for ride in rows:
        if ride.get('ride_plan_id'):
            skipped += 1
            continue
        rwgps_url = ride.get('rwgps_url_team') or ride.get('rwgps_url')
        route_id = extract_rwgps_route_id(rwgps_url)
        if not route_id:
            skipped += 1
            continue
        try:
            existing = get_ride_plan_by_rwgps_route_id(route_id)
            if existing:
                plan_id = existing['id']
            else:
                route_data = fetch_route(route_id, api_key, auth_token)
                controls = extract_controls(route_data)
                built = build_ride_plan(
                    route_data,
                    controls,
                    insert_meals=True,
                    start_time=ride.get('start_time') or '07:00',
                )
                plan_data = dict(built['plan'])
                stops_data = built['stops']
                plan_id = create_ride_plan_from_rwgps(plan_data, stops_data)

            cur = conn.cursor()
            cur.execute("UPDATE ride SET ride_plan_id = %s WHERE id = %s", (plan_id, ride['id']))
            conn.commit()
            created += 1
        except Exception as exc:  # noqa: BLE001 - continue through all rides
            conn.rollback()
            failed += 1
            current_app.logger.warning(
                'BrevetHub auto plan generation failed for ride %s route %s: %s',
                ride.get('id'), route_id, exc,
            )

    return {'created': created, 'skipped': skipped, 'failed': failed}


def create_app():
    os.environ.setdefault('BREVETHUB_MODE', '1')
    # Team Asha's shared Config validates SECRET_KEY at import time. BrevetHub
    # intentionally has its own secret, so bridge the BrevetHub-specific env var
    # before loading that shared module instead of making production startup fail.
    if os.environ.get('BREVETHUB_SECRET_KEY') and not os.environ.get('SECRET_KEY'):
        os.environ['SECRET_KEY'] = os.environ['BREVETHUB_SECRET_KEY']

    # Do not use ``from app import ...`` here. In Vercel's flat root-directory
    # deployment, that name can resolve back to this BrevetHub shell and recurse
    # through ``create_app``. Load the Team Asha root module from its file path
    # instead, while retaining the normal repo-root path for its sibling imports.
    bundled_factory = _BREVETHUB_DIR / 'team_asha_factory.py'
    root_app_path = _REPO_ROOT / 'app.py'
    if bundled_factory.exists():
        module_name = '_brevethub_team_asha_factory'
        root_app_module = sys.modules.get(module_name)
        if root_app_module is None:
            spec = importlib.util.spec_from_file_location(module_name, bundled_factory)
            if spec is None or spec.loader is None:
                raise ImportError(f'Unable to load bundled Team Asha factory from {bundled_factory}')
            root_app_module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = root_app_module
            spec.loader.exec_module(root_app_module)
    else:
        if root_app_path.resolve() == Path(__file__).resolve() or not root_app_path.exists():
            root_app_path = _REPO_ROOT.parent / 'app.py'
        if not root_app_path.exists():
            raise ImportError(f'Team Asha root app.py not found near {_BREVETHUB_DIR}')
        module_name = '_brevethub_team_asha_root_app'
        root_app_module = sys.modules.get(module_name)
        if root_app_module is None:
            spec = importlib.util.spec_from_file_location(module_name, root_app_path)
            if spec is None or spec.loader is None:
                raise ImportError(f'Unable to load Team Asha app factory from {root_app_path}')
            root_app_module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = root_app_module
            spec.loader.exec_module(root_app_module)
    create_team_asha_app = root_app_module.create_app

    app = create_team_asha_app()

    # Prefer BrevetHub overrides for templates we deliberately neutralize, while
    # every other template falls through to the Team Asha template tree.
    app.jinja_loader = ChoiceLoader([
        _BrevetHubNeutralizingLoader(FileSystemLoader(str(_BREVETHUB_DIR / 'templates'))),
        _BrevetHubNeutralizingLoader(app.jinja_loader),
    ])

    app.config['PRODUCT_NAME'] = 'BrevetHub'
    _remove_endpoint(app, 'main.about')
    _register_brevethub_routes(app)
    return app


app = create_app()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=True)
