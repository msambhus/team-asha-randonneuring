"""BrevetHub's Leaflet live map is retired — the live path is ONE shared Mapbox map.

The #1 requirement of the Radial unification: exactly one map implementation, reused
by both apps and both audiences. BrevetHub used to ship a SEPARATE Leaflet map for
guests (live_map.html) alongside a Mapbox map for members. That Leaflet template is
deleted and every live host now includes the shared Mapbox partial. This test fails
if Leaflet creeps back onto any live template.
"""
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATES = os.path.join(REPO_ROOT, 'brevethub', 'templates')

# The live-path host templates (+ the shared partial). analysis_detail.html keeps its
# own Leaflet trace map — it is NOT a live surface and is out of scope here.
_LIVE_TEMPLATES = ['live_public.html', 'live_ride_map.html', '_radial_live.html']

# Concrete Leaflet USAGE markers (not the mere word "leaflet", which appears in the
# partial's docstring explaining the retirement).
_LEAFLET_MARKERS = ['unpkg.com/leaflet', 'leaflet.js', 'leaflet.css',
                    'l.map(', '.leaflet-container', 'l.tilelayer']


def test_leaflet_live_template_is_deleted():
    assert not os.path.exists(os.path.join(TEMPLATES, 'live_map.html')), (
        'brevethub/templates/live_map.html (the Leaflet guest map) must be deleted')


def test_live_templates_load_no_leaflet():
    offenders = {}
    for name in _LIVE_TEMPLATES:
        path = os.path.join(TEMPLATES, name)
        assert os.path.exists(path), f'missing live template {name}'
        low = open(path, 'r', encoding='utf-8').read().lower()
        hits = [m for m in _LEAFLET_MARKERS if m in low]
        if hits:
            offenders[name] = hits
    assert not offenders, f'Leaflet usage still present on the live path: {offenders}'


def test_live_templates_include_shared_mapbox_partial():
    """Both live hosts include the ONE shared partial and load Mapbox GL."""
    for name in ('live_public.html', 'live_ride_map.html'):
        src = open(os.path.join(TEMPLATES, name), 'r', encoding='utf-8').read()
        assert "_radial_live.html" in src, f'{name} must include the shared partial'
        assert 'mapbox-gl' in src, f'{name} must load Mapbox GL'
