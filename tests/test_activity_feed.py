from shared.activity_feed import build_private_activity_feed


def test_explicit_garmin_strava_match_renders_one_logical_ride():
    feed = build_private_activity_feed(
        strava_activities=[
            {"strava_activity_id": 11, "name": "Strava name",
             "start_date_local": "2026-07-27T06:00:00", "distance": 100000},
        ],
        garmin_activities=[
            {"garmin_activity_id": 22, "strava_activity_id": 11,
             "activity_name": "Garmin name", "strava_name": "Strava name",
             "started_at": "2026-07-27T06:00:02", "distance_m": 100050},
        ],
    )

    assert len(feed) == 1
    assert feed[0]["providers"] == ["Strava", "Garmin"]
    assert feed[0]["name"] == "Strava name"


def test_unmatched_provider_rides_stay_separate_and_sort_newest_first():
    feed = build_private_activity_feed(
        strava_activities=[
            {"strava_activity_id": 11, "name": "Older",
             "start_date_local": "2026-07-25T06:00:00"},
        ],
        garmin_activities=[
            {"garmin_activity_id": 22, "activity_name": "Newer",
             "started_at": "2026-07-27T06:00:00"},
        ],
    )

    assert [card["name"] for card in feed] == ["Newer", "Older"]
    assert [card["providers"] for card in feed] == [["Garmin"], ["Strava"]]


def test_activity_type_survives_provider_normalization():
    feed = build_private_activity_feed(
        strava_activities=[
            {"strava_activity_id": 11, "name": "Lunch Run",
             "activity_type": "Run",
             "start_date_local": "2026-07-27T12:00:00"},
        ],
        garmin_activities=[
            {"garmin_activity_id": 22, "activity_name": "Pool Swim",
             "activity_type": "LapSwimming",
             "started_at": "2026-07-26T06:00:00"},
        ],
    )

    assert [card["activity_type"] for card in feed] == ["Run", "LapSwimming"]


def test_brevethub_shared_copy_stays_identical():
    from pathlib import Path

    root = Path(__file__).parents[1]
    assert (root / "shared/activity_feed.py").read_bytes() == (
        root / "brevethub/shared/activity_feed.py").read_bytes()


def test_team_asha_profile_does_not_embed_private_activity_feed():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "templates/my_profile.html").read_text()
    assert "Recent Training" not in source
    assert "Recent Garmin rides" not in source
    assert "strava_activity_id:" not in source
    assert "auth.my_rides" in source
