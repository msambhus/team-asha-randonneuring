"""Vendored live_telemetry import-path pin.

Vercel deploys BrevetHub with Root Directory = brevethub/, so the telemetry engine
must import as brevethub.shared.live_telemetry and its single cross-shared
dependency (the difficulty helper) must bind to the VENDORED brevethub.shared.rwgps
— not a sibling shared/ tree that would be unreachable in the deploy bundle. The
promoted module uses a relative sibling import (from .rwgps import ...) so the
binding follows the package it is loaded from. This test proves the vendored copy
imports and binds to the vendored rwgps, so it would import on a brevethub/-rooted
deploy. The byte-identical-to-canonical guarantee is covered by
test_vendored_shared_sync.py.
"""
import brevethub.shared.live_telemetry as vendored
import brevethub.shared.rwgps as vendored_rwgps


def test_vendored_live_telemetry_imports():
    assert hasattr(vendored, 'plan_delta')
    assert hasattr(vendored, 'project_history_to_route')


def test_vendored_telemetry_binds_to_vendored_rwgps():
    """The difficulty helper the telemetry engine imports is the SAME object as the
    vendored rwgps engine exposes — proving the relative import binds inside
    brevethub/shared/, so the deploy bundle is self-contained."""
    assert vendored._compute_difficulty_score is vendored_rwgps._compute_difficulty_score


def test_vendored_math_is_pure():
    """A representative pure computation works with no DB or network."""
    stops = [
        {'distance_miles': 0.0, 'cum_time_min': 0},
        {'distance_miles': 100.0, 'cum_time_min': 400},
    ]
    # At mile 50 the plan expects 200 min; a rider there in 180 min banks 20.
    assert vendored.plan_delta(50.0, 180, stops) == 20
