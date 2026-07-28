from services.sram_matching import build_sram_match


def test_unique_provider_match_inherits_existing_brevet():
    result = build_sram_match({
        "started_at_epoch": 1000,
        "distance_m": 200000,
    }, {
        "strava": [{
            "strava_activity_id": 77,
            "start_date_local": "1970-01-01T00:16:45+00:00",
            "distance": 201000,
            "ride_id": 12,
        }],
        "garmin": [],
        "rides": [],
    })
    assert result["strava_activity_id"] == 77
    assert result["ride_id"] == 12
    assert result["confidence"] >= .90


def test_ambiguous_provider_matches_are_not_auto_linked():
    candidates = [{
        "strava_activity_id": activity_id,
        "start_date_local": "1970-01-01T00:16:40+00:00",
        "distance": 200000,
    } for activity_id in (1, 2)]
    assert build_sram_match({
        "started_at_epoch": 1000,
        "distance_m": 200000,
    }, {"strava": candidates, "garmin": [], "rides": []}) is None
