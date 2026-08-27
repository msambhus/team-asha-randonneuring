from brevethub.shared.rwgps import build_ride_plan


def test_plan_stop_elevation_reconciles_to_corrected_route_total():
    """Rounded intervals must still add up to RWGPS's authoritative climb."""
    route = {
        'name': 'Test 600k',
        'id': 'test',
        'distance': 160934.4,
        'elevation_gain': 4572,  # metres, 15,000 ft after conversion
        'track_points': [
            {'d': 0, 'e': 100},
            {'d': 40000, 'e': 300},
            {'d': 80000, 'e': 100},
            {'d': 120000, 'e': 500},
            {'d': 160934.4, 'e': 200},
        ],
    }
    controls = [
        {'name': 'Start', 'distance_m': 0, 'stop_type': 'start'},
        {'name': 'Control 1', 'distance_m': 80000, 'stop_type': 'control'},
        # A rest stop between controls is intentionally included: its climb
        # must remain part of the following control interval in validation.
        {'name': 'Rest', 'distance_m': 120000, 'stop_type': 'rest'},
        {'name': 'Finish', 'distance_m': 160934.4, 'stop_type': 'finish'},
    ]

    result = build_ride_plan(route, controls)
    gains = [stop['elevation_gain'] for stop in result['stops']]

    assert result['plan']['total_elevation_ft'] == 15000
    assert sum(gains) == result['plan']['total_elevation_ft']
