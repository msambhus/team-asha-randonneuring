"""Private Garmin additions to the existing Team Asha brevet Stats contract."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import models


def test_garmin_metrics_lookup_is_owned_and_contains_no_raw_payload():
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "garmin_activity_id": 123,
        "normalized_power": 177,
        "device_name": "Edge",
    }
    with patch("models._execute", return_value=cursor) as execute:
        result = models.get_garmin_metrics_for_brevet(42, 10)

    sql, params = execute.call_args.args
    assert params == (42, 10)
    assert "srm.rider_id=%s AND srm.ride_id=%s" in sql
    assert "raw_ciphertext" not in sql
    assert result["normalized_power"] == 177


def test_stats_template_labels_garmin_without_replacing_strava_contract():
    template = (
        Path(__file__).parents[1] / "templates" / "strava_ride_analysis.html"
    ).read_text()
    assert "Garmin Device Metrics" in template
    assert "Supplemental device-recorded values" in template
    assert "Strava remains the source for the route, stops, and segment" in template
    assert "{% if is_own_profile and garmin_metrics %}" in template
    assert "{% if is_own_profile and provider_comparison %}" in template
    assert "Recording Source Comparison" in template
    assert "View on Strava" in template
    assert "Route Map" in template
    assert "Plan vs Actual Summary" in template
