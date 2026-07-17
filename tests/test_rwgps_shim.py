"""services.rwgps is a pure re-export shim of the canonical shared.rwgps engine.

The RWGPS implementation lives in exactly ONE place (shared/rwgps.py). services/
rwgps.py must re-export that engine's whole surface — every public name AND the
module-privates/constants that callers depend on (services.live_telemetry imports
_compute_difficulty_score; a naive `import *` would drop it) — each the SAME object
as in shared.rwgps. This guard fails the build if the shim ever grows its own logic
or drops a name, either of which would let the two copies drift.
"""
import os
import re

import services.rwgps as shim
import shared.rwgps as canon

# The full surface every enumerated caller imports from services.rwgps.
_SURFACE = [
    'fetch_route', 'extract_controls', 'build_ride_plan',
    'extract_rwgps_route_id', 'slugify', 'detect_stop_type',
    'calculate_segment_speed',
    'METERS_TO_MILES', 'METERS_TO_FEET',
    '_compute_difficulty_score', '_compute_segment_elevation',
    '_get_cutoff_hours', '_extract_distance_km',
    '_CUTOFF_HOURS', '_RWGPS_TYPE_MAP', '_CONTROL_TYPES',
]

_SHIM_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'services', 'rwgps.py')


def test_shim_reexports_every_name_as_same_object():
    for name in _SURFACE:
        assert hasattr(shim, name), f"services.rwgps is missing {name}"
        assert getattr(shim, name) is getattr(canon, name), (
            f"services.rwgps.{name} is not the SAME object as shared.rwgps.{name} — "
            "the shim has diverged from the canonical engine")


def test_shim_defines_no_logic():
    """The shim must be a pure re-export: no def/class of its own, so it can't drift."""
    with open(_SHIM_PATH, 'r', encoding='utf-8') as fh:
        source = fh.read()
    # Strip the module docstring so its prose can't trip the scan.
    body = re.sub(r'^\s*""".*?"""', '', source, count=1, flags=re.DOTALL)
    assert not re.search(r'^\s*def\s', body, re.MULTILINE), \
        "services/rwgps.py must define no function (pure re-export shim)"
    assert not re.search(r'^\s*class\s', body, re.MULTILINE), \
        "services/rwgps.py must define no class (pure re-export shim)"


def test_fetch_route_credentials_fall_back_to_env(monkeypatch):
    """With no creds passed and none in the environment, fetch_route raises the
    missing-credentials error BEFORE any HTTP call — proving the env fallback path
    (which lets every fetch_route(route_id) caller keep working) is wired up."""
    monkeypatch.delenv('RWGPS_API_KEY', raising=False)
    monkeypatch.delenv('RWGPS_AUTH_TOKEN', raising=False)
    import pytest
    with pytest.raises(Exception) as exc:
        shim.fetch_route('12345')
    assert 'RWGPS_API_KEY' in str(exc.value) or 'credentials' in str(exc.value).lower()
