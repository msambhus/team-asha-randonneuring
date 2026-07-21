"""services.eddington is a pure re-export shim of the canonical shared.eddington engine.

The Eddington implementation lives in exactly ONE place (shared/eddington.py).
services/eddington.py must re-export that engine's whole surface — every public
function AND the constant/module-privates callers depend on — each the SAME object
as in shared.eddington. This guard fails the build if the shim ever grows its own
logic or drops a name, either of which would let the two copies drift. Mirrors
tests/test_rwgps_shim.py (the RWGPS promotion).
"""
import os
import re

import services.eddington as shim
import shared.eddington as canon

# The full surface every enumerated caller imports from services.eddington.
_SURFACE = [
    'calculate_eddington_number', 'calculate_eddington_by_year',
    'get_eddington_progress', 'get_eddington_targets', 'get_eddington_badge_level',
    'CYCLING_TYPES',
    '_get_daily_distances', '_split_multiday', '_eddington_from_distances',
]

_SHIM_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'services', 'eddington.py')


def test_shim_reexports_every_name_as_same_object():
    for name in _SURFACE:
        assert hasattr(shim, name), f"services.eddington is missing {name}"
        assert getattr(shim, name) is getattr(canon, name), (
            f"services.eddington.{name} is not the SAME object as shared.eddington.{name} — "
            "the shim has diverged from the canonical engine")


def test_shim_defines_no_logic():
    """The shim must be a pure re-export: no def/class of its own, so it can't drift."""
    with open(_SHIM_PATH, 'r', encoding='utf-8') as fh:
        source = fh.read()
    # Strip the module docstring so its prose can't trip the scan.
    body = re.sub(r'^\s*""".*?"""', '', source, count=1, flags=re.DOTALL)
    assert not re.search(r'^\s*def\s', body, re.MULTILINE), \
        "services/eddington.py must define no function (pure re-export shim)"
    assert not re.search(r'^\s*class\s', body, re.MULTILINE), \
        "services/eddington.py must define no class (pure re-export shim)"
