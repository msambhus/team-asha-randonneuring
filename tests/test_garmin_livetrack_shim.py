"""services.garmin_livetrack is a pure re-export shim of shared.garmin_livetrack.

The Garmin LiveTrack implementation lives in exactly ONE place
(shared/garmin_livetrack.py). services/garmin_livetrack.py must re-export that
engine's whole surface — every public name AND the module-privates/constants that
callers and tests depend on (tests/test_live_tracking.py reaches for
_extract_trackpoints_html; tests/test_live_metrics.py for _extract_point) — each
the SAME object as in shared.garmin_livetrack. This guard fails the build if the
shim ever grows its own logic or drops a name, either of which would let the two
copies drift (or break a test that patches services.garmin_livetrack.<name>).
"""
import os
import re

import services.garmin_livetrack as shim
import shared.garmin_livetrack as canon

# The full surface every enumerated caller/test imports from services.garmin_livetrack.
_SURFACE = [
    'parse_session', 'fetch_positions',
    '_extract_point', '_extract_trackpoints_html', '_parse_timestamp', '_num',
    '_SESSION_URL_RE', '_SHARE_PAGE_URL', '_BROWSER_UA', '_BROWSER_HEADERS',
    '_REQUEST_TIMEOUT',
]

_SHIM_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'services', 'garmin_livetrack.py')


def test_shim_reexports_every_name_as_same_object():
    for name in _SURFACE:
        assert hasattr(shim, name), f"services.garmin_livetrack is missing {name}"
        assert getattr(shim, name) is getattr(canon, name), (
            f"services.garmin_livetrack.{name} is not the SAME object as "
            f"shared.garmin_livetrack.{name} — the shim has diverged from the "
            "canonical engine")


def test_shim_defines_no_logic():
    """The shim must be a pure re-export: no def/class of its own, so it can't drift."""
    with open(_SHIM_PATH, 'r', encoding='utf-8') as fh:
        source = fh.read()
    # Strip the module docstring so its prose can't trip the scan.
    body = re.sub(r'^\s*""".*?"""', '', source, count=1, flags=re.DOTALL)
    assert not re.search(r'^\s*def\s', body, re.MULTILINE), \
        "services/garmin_livetrack.py must define no function (pure re-export shim)"
    assert not re.search(r'^\s*class\s', body, re.MULTILINE), \
        "services/garmin_livetrack.py must define no class (pure re-export shim)"


def test_shared_engine_is_flask_free():
    """shared/garmin_livetrack.py must not import Flask — it runs inside BOTH apps
    and outside any request context (the shared/ isolation contract)."""
    canon_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'shared', 'garmin_livetrack.py')
    with open(canon_path, 'r', encoding='utf-8') as fh:
        source = fh.read()
    assert 'flask' not in source.lower(), \
        "shared/garmin_livetrack.py must be Flask-free (use stdlib logging)"
