from pathlib import Path

from services.route_explorer import (
    explorer_stops,
    route_points,
    track_from_strava_streams,
)
from services.sram_coaching import derive_sram_segment_metrics


def test_strava_streams_build_shared_route_and_elevation_track():
    track = track_from_strava_streams({
        "latlng": [[37.0, -122.0], [37.1, -122.1], [37.2, -122.2]],
        "distance": [0, 1000, 2000],
        "altitude": [10, 20, 15],
    })

    assert track[-1] == {
        "lat": 37.2,
        "lng": -122.2,
        "dist_m": 2000.0,
        "e_m": 15.0,
    }
    assert route_points(track) == [
        [37.0, -122.0], [37.1, -122.1], [37.2, -122.2]]


def test_explorer_stop_payload_excludes_svg_geometry():
    result = explorer_stops([{
        "i": 3,
        "name": "Control",
        "cumul_mi": 62.1,
        "color": "#123456",
        "eta": "2:00pm",
        "break_min": 10,
        "x": 100,
        "y": 50,
    }])

    assert result == [{
        "index": 3,
        "name": "Control",
        "distance_miles": 62.1,
        "color": "#123456",
        "eta": "2:00pm",
        "break_min": 10,
    }]


def test_axs_position_samples_are_aligned_to_planned_segments():
    miles = 1609.344
    result = derive_sram_segment_metrics(
        {
            "components": [{
                "ant_component_id": 2,
                "timestamps": [0, 60, 120, 180, 240],
                "rear_gears": [1, 2, 2, 3, 4],
                "front_gears": [1, 1, 1, 2, 2],
            }],
        },
        {
            "time": [0, 60, 120, 180, 240],
            "distance": [0, 2 * miles, 4 * miles, 6 * miles, 10 * miles],
        },
        [
            {
                "location": "Control 1",
                "distance_miles": 5,
                "actual_avg_cadence": 60,
                "actual_climb_ft_per_mi": 80,
            },
            {
                "location": "Finish",
                "distance_miles": 10,
                "actual_avg_cadence": 82,
                "actual_climb_ft_per_mi": 20,
            },
        ],
    )

    first = result["Control 1"]
    assert first["rear"] == {
        "start_position": 1,
        "end_position": 2,
        "dominant_position": 2,
        "positions_used": 2,
        "shift_count": 1,
    }
    assert first["front"]["shift_count"] == 0
    assert "lower gear range" in first["advice"][0]
    assert result["Finish"]["rear"]["start_position"] == 3
    assert "no clear drivetrain mismatch" in result["Finish"]["advice"][0]


def test_plan_and_stats_use_the_same_route_explorer_partial():
    root = Path(__file__).parents[1]
    partial = (root / "templates/partials/_route_explorer.html").read_text()
    plan = (root / "templates/ride_plan_detail_v2.html").read_text()
    stats = (root / "templates/strava_ride_analysis.html").read_text()
    mobile_live = (root / "mobile/app/ride/[id].tsx").read_text()
    mobile_plan = (root / "mobile/app/ride/plan.tsx").read_text()

    assert "Move across the map or elevation profile" in partial
    assert "route-explorer-stop" in partial
    assert "route-explorer-select-mile" in partial
    assert "route-explorer-mile" in partial
    assert "height:clamp(220px,30vh,300px)" in partial
    assert "var routeColor='#2563eb'" in partial
    assert "color:routeColor" in partial
    assert "partials/_route_explorer.html" in plan
    assert "compact_map=True" in plan
    assert "partials/_route_explorer.html" in stats
    assert "compact_map=True" in stats
    assert "AXS drivetrain:" in stats
    assert "annotations.syncCursor" in stats
    assert "route-explorer-select-mile" in stats
    assert 'strokeColor="#2563eb"' in mobile_live
    assert "stroke={seg.color}" in mobile_plan


def test_brevethub_plan_and_stats_use_the_shared_route_explorer():
    root = Path(__file__).parents[1]
    plan = (root / "brevethub/templates/plan.html").read_text()
    stats = (
        root / "brevethub/templates/strava_ride_analysis.html"
    ).read_text()

    assert "v2.plan_route_points" in plan
    assert "route-explorer-stop" in plan
    assert "partials/_route_explorer.html" in plan
    assert "compact_map=True" in plan
    assert "stats_elevation_profile" in stats
    assert "partials/_route_explorer.html" in stats
    assert "compact_map=True" in stats
    assert "annotations.syncCursor" in stats
