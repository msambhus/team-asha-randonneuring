"""services.live_telemetry is a pure re-export shim of shared.live_telemetry.

The pure per-rider telemetry math (haversine / route projection / plan delta /
next control / required speed / OTL margin / ascent split) was promoted to
shared/live_telemetry.py so both the parent web app and BrevetHub import the SAME
engine. services/live_telemetry.py must alias the old import path to the new
module via sys.modules, so every public and module-private name is the SAME object
(the parent app patches services.live_telemetry.<name> and imports module-privates).
This guard fails the build if the shim ever grows its own logic or stops aliasing,
either of which would let the two copies drift.
"""
import os
import re

import services.live_telemetry as shim
import shared.live_telemetry as canon

# A representative slice of the surface parent-app callers/tests reach for.
_SURFACE = [
    'haversine_m', 'bearing_deg', 'course_over_ground', 'project_to_route',
    'project_history_to_route', 'remaining_distance_m', 'route_start_offset_m',
    'distance_progressed_m', 'ascent_split', 'ascent_progressed_split',
    'plan_time_at', 'rebase_plan_stops', 'plan_delta', 'next_control',
    'finish_stop', 'required_speed_mph', 'time_banked_cutoff_min', 'grade_at',
    'moving_stopped', 'build_trail', 'build_actual_trail', 'latest_speed_ms',
    'toughness_remaining',
    'ON_ROUTE_MAX_M', 'START_OFFSET_MIN_M',
]

_SHIM_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'services', 'live_telemetry.py')
_SHARED_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'shared', 'live_telemetry.py')


def test_shim_is_the_same_module_object():
    """sys.modules aliasing makes services.live_telemetry BE shared.live_telemetry,
    so nothing can diverge."""
    assert shim is canon


def test_from_services_import_is_aliased():
    """`from services import live_telemetry` resolves to the shared module too."""
    from services import live_telemetry as via_services
    assert via_services is canon


def test_shim_reexports_every_name_as_same_object():
    for name in _SURFACE:
        assert hasattr(shim, name), f"services.live_telemetry is missing {name}"
        assert getattr(shim, name) is getattr(canon, name), (
            f"services.live_telemetry.{name} is not the SAME object as "
            f"shared.live_telemetry.{name}")


def test_shim_defines_no_logic():
    """The shim must be a pure re-export: no def/class of its own."""
    with open(_SHIM_PATH, 'r', encoding='utf-8') as fh:
        source = fh.read()
    body = re.sub(r'^\s*""".*?"""', '', source, count=1, flags=re.DOTALL)
    assert not re.search(r'^\s*def\s', body, re.MULTILINE), \
        "services/live_telemetry.py must define no function (pure re-export shim)"
    assert not re.search(r'^\s*class\s', body, re.MULTILINE), \
        "services/live_telemetry.py must define no class (pure re-export shim)"


def test_shared_engine_is_flask_free():
    """shared/live_telemetry.py must carry no literal 'flask'/'current_app' — it runs
    inside BOTH apps and outside any request context (the shared/ isolation contract)."""
    with open(_SHARED_PATH, 'r', encoding='utf-8') as fh:
        source = fh.read().lower()
    assert 'flask' not in source, "shared/live_telemetry.py must be Flask-free"
    assert 'current_app' not in source, "shared/live_telemetry.py must not use current_app"


# --------------------------------------------------------------------------- #
# Purity — representative functions return correct values on fixed inputs, with
# no DB or network (pure math).
# --------------------------------------------------------------------------- #
_STOPS = [
    {'distance_miles': 0.0, 'cum_time_min': 0, 'location': 'Start', 'stop_type': 'start'},
    {'distance_miles': 60.0, 'cum_time_min': 240, 'location': 'Halfway', 'stop_type': 'control'},
    {'distance_miles': 120.0, 'cum_time_min': 480, 'location': 'Finish', 'stop_type': 'finish'},
]


def test_plan_delta_banked_vs_plan():
    # At mile 60 the plan expects 240 min; a rider there in 210 min is 30 min ahead.
    assert canon.plan_delta(60.0, 210, _STOPS) == 30
    # 20 min slow at mile 30 (plan expects 120 min): behind by 20.
    assert canon.plan_delta(30.0, 140, _STOPS) == -20


def test_next_control_and_required_speed():
    nc = canon.next_control(30.0, _STOPS)
    assert nc['location'] == 'Halfway'
    assert nc['distance_miles'] == 60.0
    assert nc['dist_to_go_mi'] == 30.0
    # 30 mi to go, plan arrival 240 min, elapsed 120 min → 30 / (120/60) = 15 mph.
    req, behind = canon.required_speed_mph(nc['dist_to_go_mi'], nc['arrival_time_min'], 120)
    assert req == 15.0 and behind is False
    # Past the arrival time → behind, no negative speed.
    req2, behind2 = canon.required_speed_mph(30.0, 240, 260)
    assert req2 is None and behind2 is True


def test_time_banked_cutoff_otl_margin():
    # cutoff 8h over 120 mi; at mile 60 the pro-rata cutoff clock is 240 min. A rider
    # there in 200 min has 40 min of OTL margin.
    assert canon.time_banked_cutoff_min(60.0, 200, 120.0, 8) == 40
    # No cutoff / no distance → None.
    assert canon.time_banked_cutoff_min(60.0, 200, 120.0, None) is None
