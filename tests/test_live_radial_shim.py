"""services.live_radial is a pure re-export shim of shared.live_radial.

The Radial live-view builders (rich telemetry composition + privacy-shaped roster +
altitude profile) live in exactly ONE place (shared/live_radial.py). services/
live_radial.py must alias that module via sys.modules so every public and
module-private name is the SAME object. This guard fails the build if the shim ever
grows its own logic or stops aliasing, either of which would let the two copies
drift.
"""
import os
import re

import services.live_radial as shim
import shared.live_radial as canon

_SURFACE = [
    'compose_rider_telemetry', 'build_radial_roster', 'build_elevation_profile',
    'roster_key', 'place_x', 'gradient_legend',
    'ROSTER_DISTANCE_UNIT', 'MARKER_AHEAD_COLOR', 'MARKER_BEHIND_COLOR',
    'MARKER_UNKNOWN_COLOR',
]

_SHIM_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'services', 'live_radial.py')
_SHARED_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'shared', 'live_radial.py')


def test_shim_is_the_same_module_object():
    """sys.modules aliasing makes services.live_radial BE shared.live_radial."""
    assert shim is canon


def test_from_services_import_is_aliased():
    from services import live_radial as via_services
    assert via_services is canon


def test_shim_reexports_every_name_as_same_object():
    for name in _SURFACE:
        assert hasattr(shim, name), f"services.live_radial is missing {name}"
        assert getattr(shim, name) is getattr(canon, name), (
            f"services.live_radial.{name} is not the SAME object as "
            f"shared.live_radial.{name}")


def test_shim_defines_no_logic():
    """The shim must be a pure re-export: no def/class of its own."""
    with open(_SHIM_PATH, 'r', encoding='utf-8') as fh:
        source = fh.read()
    body = re.sub(r'^\s*""".*?"""', '', source, count=1, flags=re.DOTALL)
    assert not re.search(r'^\s*def\s', body, re.MULTILINE), \
        "services/live_radial.py must define no function (pure re-export shim)"
    assert not re.search(r'^\s*class\s', body, re.MULTILINE), \
        "services/live_radial.py must define no class (pure re-export shim)"


def test_shared_engine_is_flask_free():
    """shared/live_radial.py must carry no literal 'flask'/'current_app' — it runs
    inside BOTH apps and outside any request context (the shared/ isolation
    contract)."""
    with open(_SHARED_PATH, 'r', encoding='utf-8') as fh:
        source = fh.read().lower()
    assert 'flask' not in source, "shared/live_radial.py must be Flask-free"
    assert 'current_app' not in source, "shared/live_radial.py must not use current_app"


def test_imports_only_stdlib_and_live_telemetry():
    """The engine's only non-stdlib dependency is the sibling live_telemetry."""
    import ast
    with open(_SHARED_PATH, 'r', encoding='utf-8') as fh:
        tree = ast.parse(fh.read())
    allowed_stdlib = {'hashlib', 'math', 'datetime'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split('.')[0] in allowed_stdlib, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                # relative sibling import (`from . import live_telemetry`): module is
                # None, the sibling is the imported name — must be live_telemetry.
                names = {a.name for a in node.names}
                assert node.module in (None, 'live_telemetry'), node.module
                assert names <= {'live_telemetry'}, names
            else:
                assert node.module.split('.')[0] in allowed_stdlib, node.module
