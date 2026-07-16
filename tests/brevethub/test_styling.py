"""BrevetHub design-system smoke test (Mission 10 — styling cleanup).

Four guards, all DB-free / network-free per the established BrevetHub test pattern.
The first three are pure stdlib (no jinja2/flask/pytest-fixture needed), so they run
even in a minimal checkout; the fourth needs the Flask test client.

1. **Stylesheet contract** — the shared design-system classes and element ids that
   every template references are actually DEFINED in ``static/style.css``. This is
   the regression guard that the three feature pages bolted on during M8–M9 (plan,
   analysis, live-map) never render as raw, unstyled HTML again.

2. **Static missing-filter guard** — a dependency-free scan that every ``| filter``
   used in a BrevetHub template is a Jinja builtin or a BrevetHub-registered filter.
   BrevetHub does NOT inherit Team Asha's ``commafy``/``clean_name`` filters, so any
   such usage would 500 at render. This is the durable static counterpart to the
   render tests below.

3. **Structural parse smoke** — a dependency-free stand-in for a Jinja parse when
   jinja2 isn't importable: every template has balanced ``{% %}``/``{{ }}``
   delimiters, only known statement tags, and correctly nested/closed blocks. Not a
   substitute for a real render (the client tests below are), but it catches the
   markup-structure class of error a parse would, with zero deps.

4. **Render-path contract** — each key page returns 200, links ``style.css``, and
   contains its expected component class(es) in the rendered markup. A render (not
   a parse check) is the only thing that catches a missing-filter 500, so the
   plan/analysis/live pages are exercised through the real client with mocked models.
"""
import os
import re
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BREVETHUB_DIR = os.path.join(REPO_ROOT, 'brevethub')
STYLE_CSS = os.path.join(BREVETHUB_DIR, 'static', 'style.css')
TEMPLATES_DIR = os.path.join(BREVETHUB_DIR, 'templates')


_RIDER = {'id': 7, 'email': 'rider@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': None,
          'rusa_id_duplicate': False,
          'created_at': datetime(2024, 3, 1, tzinfo=timezone.utc)}

_EVENT = {
    'id': 11, 'rusa_route_id': '1234', 'name': 'Point Reyes Lighthouse 200',
    'date': '2026-08-15', 'distance_km': 200, 'region': 'CA: San Francisco',
    'ride_type': 'ACP brevet', 'elevation_ft': 4200, 'rwgps_url': None,
    'start_location': None, 'start_time': None, 'time_limit_hours': 13.5,
}

# A fully-populated cached analysis (mirrors test_analysis.py) so the detail page
# renders its map + segment/stop tables, exercising the newly-defined classes.
_ANALYSIS = {
    'activity': {'name': 'Coastal 200', 'date': '2026-06-20', 'distance_km': 203.4,
                 'elevation_ft': 6800, 'moving_time': '9h 12m',
                 'elapsed_time': '11h 40m', 'avg_speed_kmh': 22.1},
    'summary': {'moving_speed_kmh': 23.4, 'avg_hr': 138, 'max_hr': 171,
                'avg_watts': 165, 'max_watts': 520},
    'stop_count': 2,
    'stops': [{'distance_km': 100.0, 'duration_min': 18.0, 'lat': 37.5, 'lng': -122.3}],
    'legs': [{'to_km': 100.0, 'distance_km': 100.0, 'riding_time': '4h 30m',
              'speed_kmh': 22.2, 'avg_hr': 140, 'avg_watts': 170, 'np_watts': 178,
              'avg_cadence': 84, 'grade_pct': 1.2, 'climb_ft_per_mi': 45}],
    'map': {'track': [[37.5, -122.3], [37.6, -122.4]],
            'bounds': [[37.5, -122.4], [37.6, -122.3]]},
}


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


# --------------------------------------------------------------------------- #
# 1. Stylesheet contract — every referenced component class/id is defined.
# --------------------------------------------------------------------------- #
def _css():
    with open(STYLE_CSS, 'r', encoding='utf-8') as fh:
        return fh.read()


# Selectors the templates rely on. A missing one means a page renders unstyled.
REQUIRED_SELECTORS = [
    # Core design-system components (base + shared).
    '.container', '.card', '.btn', '.badge', '.form', '.flash', '.empty-state',
    '.site-header', '.nav',
    # Table shells.
    '.rusa-history', '.signups-table', '.live-rides',
    # Plan page (M8) — previously undefined.
    '.plan-page', '.plan-intro', '.plan-event-meta', '.plan-target-form',
    '.plan-summary', '.plan-schedule', '.plan-bank-ok', '.plan-bank-low',
    '.plan-save-section',
    # Analysis + live (M9) — previously undefined.
    '.analysis-list', '.analysis-legs', '.analysis-stops',
    '#analysis-map', '#live-map', '.live-timeline',
]


@pytest.mark.parametrize('selector', REQUIRED_SELECTORS)
def test_design_system_selector_defined(selector):
    css = _css()
    # A defined rule is the selector followed (possibly after other selectors in a
    # group, or a combinator) by an opening brace somewhere in the file.
    assert re.search(re.escape(selector) + r'[\s,:.#\w>()\-\[\]="\']*\{', css), \
        f'{selector} is referenced by a template but not defined in style.css'


def test_no_dead_calendar_table_rule():
    """The dead ``.calendar-table`` selector was removed (no template uses it)."""
    assert '.calendar-table' not in _css()


def test_root_tokens_present():
    """The neutral palette is still driven entirely by :root variables."""
    css = _css()
    for token in ('--bg', '--surface', '--text', '--border', '--accent',
                  '--success', '--warning', '--danger'):
        assert token in css, f'design token {token} missing from :root'


# --------------------------------------------------------------------------- #
# 2. Static missing-filter guard — dep-free (no jinja2/flask). Catches the exact
#    class of bug a Jinja *parse* check misses: a template using a filter that this
#    app never registers (commafy/clean_name are Team Asha-only) -> a 500 at render.
# --------------------------------------------------------------------------- #

# Jinja2 3.x built-in filters. Flask registers no extra template filters by default.
_JINJA_BUILTIN_FILTERS = {
    'abs', 'attr', 'batch', 'capitalize', 'center', 'default', 'd', 'dictsort',
    'escape', 'e', 'filesizeformat', 'first', 'float', 'forceescape', 'format',
    'groupby', 'indent', 'int', 'items', 'join', 'last', 'length', 'count', 'list',
    'lower', 'map', 'max', 'min', 'pprint', 'random', 'reject', 'rejectattr',
    'replace', 'reverse', 'round', 'safe', 'select', 'selectattr', 'slice', 'sort',
    'string', 'striptags', 'sum', 'title', 'tojson', 'trim', 'truncate', 'unique',
    'upper', 'urlencode', 'urlize', 'wordcount', 'wordwrap', 'xmlattr',
}

_JINJA_REGION = re.compile(r'{{.*?}}|{%.*?%}', re.DOTALL)   # only scan Jinja delimiters
_FILTER_USE = re.compile(r'\|\s*(\w+)')                     # a `| filter` application


def _registered_brevethub_filters():
    """Filters BrevetHub registers on its own Jinja env (currently none — the app
    only adds the product_name context processor). Scanning keeps this honest if a
    future mission adds a brevethub-local commafy/clean_name."""
    src = open(os.path.join(BREVETHUB_DIR, 'app.py'), 'r', encoding='utf-8').read()
    names = set(re.findall(r"template_filter\(['\"](\w+)['\"]\)", src))
    names |= set(re.findall(r"jinja_env\.filters\[['\"](\w+)['\"]\]", src))
    names |= set(re.findall(r"add_template_filter\([^,]+,\s*['\"]?(\w+)", src))
    return names


def test_no_unregistered_jinja_filters():
    allowed = _JINJA_BUILTIN_FILTERS | _registered_brevethub_filters()
    offenders = {}
    for name in sorted(os.listdir(TEMPLATES_DIR)):
        if not name.endswith('.html'):
            continue
        src = open(os.path.join(TEMPLATES_DIR, name), 'r', encoding='utf-8').read()
        for region in _JINJA_REGION.findall(src):
            for filt in _FILTER_USE.findall(region):
                if filt not in allowed:
                    offenders.setdefault(name, set()).add(filt)
    assert not offenders, (
        'BrevetHub templates use filters this app does not register (would 500 at '
        f'render — e.g. Team Asha-only commafy/clean_name): {offenders}')


def test_no_ta_only_filters_used():
    """Explicit guard for the two Team Asha-only filters BrevetHub must never use."""
    for name in sorted(os.listdir(TEMPLATES_DIR)):
        if not name.endswith('.html'):
            continue
        src = open(os.path.join(TEMPLATES_DIR, name), 'r', encoding='utf-8').read()
        for region in _JINJA_REGION.findall(src):
            used = set(_FILTER_USE.findall(region))
            assert 'commafy' not in used, f'{name} uses TA-only |commafy'
            assert 'clean_name' not in used, f'{name} uses TA-only |clean_name'


# --------------------------------------------------------------------------- #
# 3. Structural parse smoke — dep-free stand-in for a Jinja parse. Validates
#    delimiter balance, known tags, and block nesting across every template.
# --------------------------------------------------------------------------- #

# Jinja statement tags these templates may use. Openers pair with an explicit end
# tag; mid tags (elif/else) may only appear inside a matching open block.
_TAG_OPENERS = {'if': 'endif', 'for': 'endfor', 'block': 'endblock',
                'with': 'endwith', 'macro': 'endmacro', 'call': 'endcall',
                'filter': 'endfilter', 'autoescape': 'endautoescape'}
_TAG_CLOSERS = {v: k for k, v in _TAG_OPENERS.items()}
_TAG_MIDS = {'elif': {'if'}, 'else': {'if', 'for'}}
_SELF_CONTAINED_TAGS = {'extends', 'include', 'import', 'from', 'set', 'do'}
_KNOWN_TAGS = (set(_TAG_OPENERS) | set(_TAG_CLOSERS) | set(_TAG_MIDS)
               | _SELF_CONTAINED_TAGS)

_STMT = re.compile(r'{%(.*?)%}', re.DOTALL)
_STMT_KW = re.compile(r'-?\s*(\w+)')


def _template_files():
    return [n for n in sorted(os.listdir(TEMPLATES_DIR)) if n.endswith('.html')]


def _structural_errors(src):
    errs = []
    # Delimiter balance — an unclosed {% or {{ is a hard parse error.
    for opener, closer in (('{%', '%}'), ('{{', '}}')):
        if src.count(opener) != src.count(closer):
            errs.append(f'{opener}/{closer} imbalance '
                        f'{src.count(opener)}/{src.count(closer)}')
    # Known tags + correct block nesting/closing order.
    stack = []
    for stmt in _STMT.findall(src):
        m = _STMT_KW.match(stmt)
        if not m:
            errs.append('empty {% %} statement')
            continue
        kw = m.group(1)
        if kw not in _KNOWN_TAGS:
            errs.append(f'unknown tag {{% {kw} %}}')
        elif kw in _TAG_OPENERS:
            stack.append(kw)
        elif kw in _TAG_CLOSERS:
            want = _TAG_CLOSERS[kw]
            if not stack or stack[-1] != want:
                errs.append(f'{{% {kw} %}} without open {{% {want} %}} (stack={stack})')
            else:
                stack.pop()
        elif kw in _TAG_MIDS:
            if not stack or stack[-1] not in _TAG_MIDS[kw]:
                errs.append(f'{{% {kw} %}} outside {_TAG_MIDS[kw]} (stack={stack})')
    if stack:
        errs.append(f'unclosed blocks {stack}')
    return errs


@pytest.mark.parametrize('name', _template_files())
def test_template_structurally_valid(name):
    src = open(os.path.join(TEMPLATES_DIR, name), 'r', encoding='utf-8').read()
    errs = _structural_errors(src)
    assert not errs, f'{name} has Jinja structural errors: {errs}'


# --------------------------------------------------------------------------- #
# 4. Render-path contract — each key page: 200, links style.css, has its class.
# --------------------------------------------------------------------------- #
def test_landing_styled(client):
    resp = client.get('/')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    assert 'hero' in body


def test_login_styled(client):
    resp = client.get('/auth/login')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    assert 'card' in body


def test_calendar_styled(client):
    with patch('brevethub.models.get_brevet_weather_for_events', return_value={}), \
         patch('brevethub.models.get_events_cache_freshness',
               return_value=datetime.now(timezone.utc)), \
         patch('brevethub.routes.calendar.get_rusa_events'), \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT]):
        resp = client.get('/calendar')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    assert 'events-grid' in body and 'event-card' in body


def test_plan_styled(client):
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT):
        resp = client.get('/plan/11?speed=20')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    # The plan-page classes that were undefined before this mission now render.
    assert 'plan-page' in body and 'plan-schedule' in body


def test_dashboard_styled(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_strava_connection', return_value=None), \
         patch('brevethub.models.get_rider_signups', return_value=[]):
        resp = client.get('/dashboard')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    assert 'card' in body
    # No sign-ups -> the empty-state component renders (text preserved).
    assert 'empty-state' in body
    assert "haven't signed up for any upcoming brevets yet" in body


def test_profile_styled(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_strava_connection', return_value=None):
        resp = client.get('/profile')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    assert 'card' in body and 'profile' in body


def test_analysis_list_styled(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_strava_connection', return_value=None):
        resp = client.get('/analysis')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    assert 'card' in body


def test_analysis_detail_styled(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride_analysis',
               return_value={'analysis': _ANALYSIS, 'activity_streams': b'x',
                             'computed_at': None}):
        resp = client.get('/analysis/555')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    # The segment/stop tables + map container use the newly-defined classes.
    assert 'analysis-legs' in body and 'analysis-stops' in body
    assert 'analysis-map' in body
    # The inline <style> block was removed — the map is sized from style.css now.
    assert '<style>' not in body


def test_live_list_styled(client):
    with patch('brevethub.models.get_public_rides', return_value=[]):
        resp = client.get('/live')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    # Empty state renders for no public rides.
    assert 'empty-state' in body


def test_live_map_styled(client):
    ride = {'id': 1, 'name': 'SFR Point Reyes 200k', 'club_name': None,
            'distance_km': 200, 'start_at': None, 'status': 'live'}
    with patch('brevethub.models.get_public_ride', return_value=ride):
        resp = client.get('/live/1')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    assert 'live-map' in body and 'live-timeline' in body
    # The inline <style> block was moved into style.css.
    assert '<style>' not in body
