"""Eddington Number calculation — pure re-export shim.

The single Eddington implementation now lives in ``shared/eddington.py``
(club-agnostic, framework-agnostic), so the parent web app and BrevetHub share
ONE engine that cannot drift. This module re-exports that engine's ENTIRE surface
— every public function AND the constant (``CYCLING_TYPES``) and module-privates
(``_get_daily_distances``, ``_split_multiday``, ``_eddington_from_distances``) —
each the *same object* as in ``shared.eddington``. So every existing
``from services.eddington import ...`` caller (services.strava, routes.admin,
routes.riders, routes.live, ...) keeps working unchanged.

This shim holds NO logic of its own (it defines no function/class), so it can never
diverge from the canonical engine. ``tests/test_eddington_shim.py`` enforces the
same-object re-export of the full surface and that the shim stays def-free. Mirrors
the RWGPS promotion (``services/rwgps.py`` -> ``shared/rwgps.py``).
"""
from shared.eddington import (  # noqa: F401  — re-export, names used by callers
    CYCLING_TYPES,
    _get_daily_distances,
    _split_multiday,
    _eddington_from_distances,
    calculate_eddington_number,
    calculate_eddington_by_year,
    get_eddington_progress,
    get_eddington_targets,
    get_eddington_badge_level,
)
