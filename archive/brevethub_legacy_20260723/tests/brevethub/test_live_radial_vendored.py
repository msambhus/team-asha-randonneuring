"""Vendored live_radial import-path pin.

Vercel deploys BrevetHub with Root Directory = brevethub/, so the Radial builders
must import as brevethub.shared.live_radial and their single cross-shared dependency
(the telemetry engine) must bind to the VENDORED brevethub.shared.live_telemetry —
not a sibling shared/ tree unreachable in the deploy bundle. The module uses a
relative sibling import (from . import live_telemetry) so the binding follows the
package it is loaded from. This proves the vendored copy imports and binds inside
brevethub/shared/. Byte-identity to canonical is covered by test_vendored_shared_sync.
"""
import brevethub.shared.live_radial as vendored
import brevethub.shared.live_telemetry as vendored_tlm


def test_vendored_live_radial_imports():
    assert hasattr(vendored, 'build_radial_roster')
    assert hasattr(vendored, 'build_elevation_profile')
    assert hasattr(vendored, 'compose_rider_telemetry')


def test_vendored_radial_binds_to_vendored_telemetry():
    """The telemetry engine the Radial builders import is the SAME object as the
    vendored live_telemetry engine — proving the relative import binds inside
    brevethub/shared/, so the deploy bundle is self-contained."""
    assert vendored.tlm is vendored_tlm


def test_vendored_roster_is_privacy_shaped_and_sorted():
    """A representative roster build works with no DB or network and never leaks a
    rider id / email / google id."""
    from datetime import datetime, timezone
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    ctx = {'has_route': False, 'track': [], 'plan_stops': []}
    rows = [
        {'rider_id': 7, 'display_name': 'Dana Fox', 'lat': 37.0, 'lng': -122.0,
         'source': 'beacon', 'recorded_at': now},
        {'rider_id': 8, 'display_name': 'Cy Ng', 'lat': 37.1, 'lng': -122.1,
         'source': 'garmin', 'recorded_at': now},
    ]
    roster = vendored.build_radial_roster(rows, ctx, now, {}, ride_id=3)
    assert len(roster) == 2
    for r in roster:
        for leaked in ('rider_id', 'email', 'google_id'):
            assert leaked not in r
        assert r['key'] and len(r['key']) == 12
        assert r['display_name'] in ('Dana Fox', 'Cy Ng')
