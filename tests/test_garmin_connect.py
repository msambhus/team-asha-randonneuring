"""Credential-free contract tests for Team Asha's read-only Garmin client."""
from datetime import date

import pytest

from services.garmin_connect import GarminPerformanceClient
from vendor.python_garminconnect import GarminConnectNotFoundError


class FakeAuth:
    def __init__(self):
        self.calls = []

    def connectapi(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if path == "/userprofile-service/socialProfile":
            return {"displayName": "Rider"}
        if "dailyHeartRate" in path:
            return {"restingHeartRate": 47}
        if "dailySleepData" in path:
            return {"dailySleepDTO": {
                "sleepScores": {"overall": {"value": 82}}}}
        if "/hrv-service/" in path:
            return {"hrvSummary": {"status": "BALANCED"}}
        if "bodyBattery" in path:
            return [{"charged": 61}]
        if "trainingreadiness" in path:
            return [
                {"score": 62, "inputContext": "MIDDAY_UPDATE"},
                {
                    "score": 74,
                    "level": "HIGH",
                    "feedbackShort": "READY",
                    "recoveryTime": 90,
                    "sleepScoreFactorPercent": 85,
                    "acwrFactorPercent": 90,
                    "hrvFactorPercent": 80,
                    "inputContext": "AFTER_WAKEUP_RESET",
                },
            ]
        if "maxmet" in path:
            return {"cycling": {"vo2Max": 52}}
        if "trainingstatus" in path:
            return {"mostRecentTrainingStatus": {
                "latestTrainingStatusData": {"device": {
                    "trainingStatusFeedbackPhrase": "PRODUCTIVE",
                    "dailyTrainingLoadAcute": 642,
                    "loadLevelTrend": "MAINTAINING",
                }}}}
        if "endurancescore" in path:
            return {"enduranceScore": 7120}
        if "activities/search" in path:
            return [{"activityId": 123, "activityType": {"typeKey": "cycling"}}]
        return {}


def test_performance_snapshot_normalizes_cycling_headlines():
    auth = FakeAuth()
    snapshot = GarminPerformanceClient(auth).performance_snapshot("2026-07-27")

    assert snapshot["resting_heart_rate"] == 47
    assert snapshot["hrv_status"] == "BALANCED"
    assert snapshot["sleep_score"] == 82
    assert snapshot["body_battery"] == 61
    assert snapshot["training_readiness"] == 74
    assert snapshot["vo2_max_cycling"] == 52
    assert snapshot["training_status"] == "PRODUCTIVE"
    assert snapshot["readiness_level"] == "HIGH"
    assert snapshot["readiness_feedback"] == "READY"
    assert snapshot["recovery_time_minutes"] == 90
    assert snapshot["sleep_factor_percent"] == 85
    assert snapshot["acwr_factor_percent"] == 90
    assert snapshot["hrv_factor_percent"] == 80
    assert snapshot["acute_training_load"] == 642
    assert snapshot["load_level_trend"] == "MAINTAINING"
    assert snapshot["endurance_score"] == 7120
    assert len(snapshot["raw"]) == 10
    assert all(call[0].startswith("/") for call in auth.calls)


def test_missing_endurance_score_does_not_block_other_garmin_metrics():
    class NoEnduranceAuth(FakeAuth):
        def connectapi(self, path, **kwargs):
            if "endurancescore" in path:
                raise GarminConnectNotFoundError("not supported")
            return super().connectapi(path, **kwargs)

    snapshot = GarminPerformanceClient(
        NoEnduranceAuth()).performance_snapshot("2026-07-27")

    assert snapshot["endurance_score"] is None
    assert snapshot["training_readiness"] == 74
    assert snapshot["training_status"] == "PRODUCTIVE"


def test_activity_read_is_bounded_and_cycling_only_by_default():
    auth = FakeAuth()
    activities = GarminPerformanceClient(auth).activities(limit=25)

    assert activities[0]["activityId"] == 123
    path, kwargs = auth.calls[-1]
    assert "activities/search/activities" in path
    assert kwargs["params"] == {
        "start": "0", "limit": "25", "activityType": "cycling"}


def test_activity_history_pages_until_one_year_boundary():
    class PagingAuth:
        def __init__(self):
            self.calls = []

        def connectapi(self, path, **kwargs):
            self.calls.append((path, kwargs))
            start = int(kwargs["params"]["start"])
            if start == 0:
                return [
                    {"activityId": index,
                     "startTimeGMT": f"2026-07-{index:02d}T08:00:00Z"}
                    for index in range(1, 4)
                ]
            return []

    auth = PagingAuth()
    pages = list(GarminPerformanceClient(auth).activity_pages_since(
        date(2025, 7, 27), page_size=3))

    assert [[row["activityId"] for row in page] for page in pages] == [
        [1, 2, 3]]
    assert auth.calls[0][1]["params"] == {
        "start": "0", "limit": "3", "activityType": "cycling"}
    assert auth.calls[1][1]["params"]["start"] == "3"


def test_activity_history_stops_and_filters_at_date_boundary():
    class BoundaryAuth:
        def connectapi(self, path, **kwargs):
            return [
                {"activityId": 1, "startTimeGMT": "2025-07-28T08:00:00Z"},
                {"activityId": 2, "startTimeGMT": "2025-07-27T08:00:00Z"},
                {"activityId": 3, "startTimeGMT": "2025-07-26T08:00:00Z"},
            ]

    pages = list(GarminPerformanceClient(
        BoundaryAuth()).activity_pages_since(
            date(2025, 7, 27), page_size=3))

    assert [[row["activityId"] for row in page] for page in pages] == [[1, 2]]


def test_activity_history_has_explicit_safety_bound():
    client = GarminPerformanceClient(FakeAuth())
    with pytest.raises(ValueError):
        list(client.activity_pages_since(
            date(2025, 7, 27), max_activities=5001))


def test_activity_history_batch_returns_resumable_cursor():
    class BatchAuth:
        def __init__(self):
            self.starts = []

        def connectapi(self, path, **kwargs):
            start = int(kwargs["params"]["start"])
            self.starts.append(start)
            return [
                {"activityId": start + index,
                 "startTimeGMT": "2026-07-27T08:00:00Z"}
                for index in range(100)
            ]

    auth = BatchAuth()
    batch = GarminPerformanceClient(auth).activity_history_batch(
        date(2025, 7, 27), start=200, max_pages=2)

    assert auth.starts == [200, 300]
    assert len(batch["pages"]) == 2
    assert batch["next_start"] == 400
    assert batch["complete"] is False


def test_activity_history_batch_completes_at_date_boundary():
    class BoundaryAuth:
        def connectapi(self, path, **kwargs):
            return [
                {"activityId": 1, "startTimeGMT": "2025-07-28T08:00:00Z"},
                {"activityId": 2, "startTimeGMT": "2025-07-26T08:00:00Z"},
            ]

    batch = GarminPerformanceClient(BoundaryAuth()).activity_history_batch(
        date(2025, 7, 27), page_size=100)

    assert [[row["activityId"] for row in page]
            for page in batch["pages"]] == [[1]]
    assert batch["next_start"] == 0
    assert batch["complete"] is True


def test_activity_summary_normalizes_cycling_performance_fields():
    normalized = GarminPerformanceClient.normalize_activity({
        "activityId": 123,
        "activityName": "Morning Ride",
        "activityType": {"typeKey": "road_biking"},
        "startTimeGMT": "2026-07-27T14:00:00",
        "distance": 201168.0,
        "duration": 36000.0,
        "movingDuration": 32400.0,
        "elevationGain": 2300.0,
        "averageHR": 132,
        "maxHR": 171,
        "avgPower": 154,
        "maxPower": 712,
        "normPower": 177,
        "aerobicTrainingEffect": 4.2,
        "anaerobicTrainingEffect": 1.1,
        "averageBikingCadenceInRevPerMinute": 78,
        "deviceName": "Edge 1050",
    })

    assert normalized["garmin_activity_id"] == 123
    assert normalized["activity_type"] == "road_biking"
    assert normalized["normalized_power"] == 177
    assert normalized["aerobic_training_effect"] == 4.2
    assert normalized["average_cadence"] == 78


def test_activity_details_is_bounded_and_read_only():
    auth = FakeAuth()
    GarminPerformanceClient(auth).activity_details(
        123, max_chart=1000, max_polyline=0)

    path, kwargs = auth.calls[-1]
    assert path == "/activity-service/activity/123/details"
    assert kwargs["params"] == {
        "maxChartSize": "1000", "maxPolylineSize": "0"}


def test_client_exposes_no_garmin_write_operations():
    public = set(dir(GarminPerformanceClient))
    assert not any(name.startswith((
        "upload", "delete", "update", "set_", "schedule", "create"))
        for name in public)
