from services.activity_matching import build_provider_metric_comparison


def test_provider_comparison_preserves_sources_and_flags_material_gap():
    rows = build_provider_metric_comparison(
        {
            "actual_distance_miles": 100,
            "actual_elapsed_time_min": 600,
            "actual_moving_time_min": 500,
            "actual_elevation_ft": 5000,
        },
        {
            "distance_m": 105 * 1609.344,
            "duration_s": 660 * 60,
            "moving_duration_s": 505 * 60,
            "elevation_gain_m": 6000 / 3.28084,
        },
    )
    by_key = {row["key"]: row for row in rows}
    assert by_key["distance"]["strava"] == 100
    assert by_key["distance"]["garmin"] == 105
    assert by_key["distance"]["material"] is True
    assert by_key["elapsed_time"]["material"] is True
    assert by_key["moving_time"]["material"] is False
    assert by_key["elevation"]["material"] is True


def test_provider_comparison_ignores_small_rounding_differences():
    rows = build_provider_metric_comparison(
        {
            "actual_distance_miles": 200,
            "actual_elapsed_time_min": 800,
            "actual_moving_time_min": 700,
            "actual_elevation_ft": 8000,
        },
        {
            "distance_m": 200.3 * 1609.344,
            "duration_s": 802 * 60,
            "moving_duration_s": 704 * 60,
            "elevation_gain_m": 8100 / 3.28084,
        },
    )
    assert rows
    assert not any(row["material"] for row in rows)


def test_provider_comparison_omits_metrics_missing_from_either_source():
    rows = build_provider_metric_comparison(
        {"actual_distance_miles": 50},
        {"distance_m": 50 * 1609.344},
    )
    assert [row["key"] for row in rows] == ["distance"]
