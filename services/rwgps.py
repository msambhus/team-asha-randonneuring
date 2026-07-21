"""RideWithGPS API service — pure re-export shim.

The single RWGPS implementation now lives in ``shared/rwgps.py`` (club-agnostic,
framework-agnostic), so Team Asha and BrevetHub share ONE engine that cannot drift.
This module re-exports that engine's ENTIRE surface — every public name AND every
module-private (``_compute_difficulty_score``, ``_compute_segment_elevation``,
``_get_cutoff_hours``, ``_extract_distance_km``) and constant
(``_CUTOFF_HOURS`` / ``_RWGPS_TYPE_MAP`` / ``_CONTROL_TYPES``) — each the *same
object* as in ``shared.rwgps``. So every existing ``from services.rwgps import ...``
caller keeps working unchanged, including the ones that import module-privates
(``services.live_telemetry``) which a naive ``import *`` would drop.

This shim holds NO logic of its own (it defines no function/class), so it can never
diverge from the canonical engine. ``tests/test_rwgps_shim.py`` enforces the
same-object re-export of the full surface and that the shim stays def-free.
"""
from shared.rwgps import (  # noqa: F401  — re-export, names used by callers
    METERS_TO_MILES,
    METERS_TO_FEET,
    _CUTOFF_HOURS,
    _RWGPS_TYPE_MAP,
    _CONTROL_TYPES,
    extract_rwgps_route_id,
    slugify,
    detect_stop_type,
    _get_cutoff_hours,
    _extract_distance_km,
    _compute_difficulty_score,
    fetch_route,
    extract_controls,
    _compute_segment_elevation,
    calculate_segment_speed,
    profile_segment_speed,
    PACING_PROFILES,
    build_ride_plan,
)
