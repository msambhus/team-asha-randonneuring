"""Credential-free contract tests for Team Asha's read-only Garmin client."""
from services.garmin_connect import GarminPerformanceClient


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
            return [{"score": 74}]
        if "maxmet" in path:
            return {"cycling": {"vo2Max": 52}}
        if "trainingstatus" in path:
            return {"trainingStatus": "PRODUCTIVE"}
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
    assert len(snapshot["raw"]) == 9
    assert all(call[0].startswith("/") for call in auth.calls)


def test_activity_read_is_bounded_and_cycling_only_by_default():
    auth = FakeAuth()
    activities = GarminPerformanceClient(auth).activities(limit=25)

    assert activities[0]["activityId"] == 123
    path, kwargs = auth.calls[-1]
    assert "activities/search/activities" in path
    assert kwargs["params"] == {
        "start": "0", "limit": "25", "activityType": "cycling"}


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
