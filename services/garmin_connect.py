"""Read-only Garmin Connect performance client for Team Asha.

Authentication is adapted from the vendored MIT-licensed
``cyberjunky/python-garminconnect`` client. This module deliberately exposes only
cycling-relevant reads. It contains no upload, edit, delete, goal, hydration,
weight, workout, or device-write method.

Garmin Connect is an unofficial web API. Response fields vary by device and
account, so ``performance_snapshot`` retains raw endpoint payloads behind stable
keys and derives only conservative headline values.
"""
from datetime import date, datetime
from typing import Any

from vendor.python_garminconnect import Client, GarminConnectNotFoundError

MAX_ACTIVITY_LIMIT = 100
_DATE_FORMAT = "%Y-%m-%d"

_PROFILE = "/userprofile-service/socialProfile"
_DAILY_SUMMARY = "/usersummary-service/usersummary/daily"
_HEART_RATE = "/wellness-service/wellness/dailyHeartRate"
_SLEEP = "/wellness-service/wellness/dailySleepData"
_STRESS = "/wellness-service/wellness/dailyStress"
_BODY_BATTERY = "/wellness-service/wellness/bodyBattery/reports/daily"
_HRV = "/hrv-service/hrv"
_MAX_METRICS = "/metrics-service/metrics/maxmet/daily"
_TRAINING_READINESS = "/metrics-service/metrics/trainingreadiness"
_TRAINING_STATUS = "/metrics-service/metrics/trainingstatus/aggregated"
_ENDURANCE_SCORE = "/metrics-service/metrics/endurancescore"
_ACTIVITIES = "/activitylist-service/activities/search/activities"
_ACTIVITY = "/activity-service/activity"


def _date(value: str | date) -> str:
    rendered = value.isoformat() if isinstance(value, date) else str(value).strip()
    datetime.strptime(rendered, _DATE_FORMAT)
    return rendered


def _dig(payload: Any, *paths: tuple[str, ...]) -> Any:
    """Return the first non-None value found at one of several nested paths."""
    for path in paths:
        value = payload
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if value is not None:
            return value
    return None


def _find_key(payload: Any, *keys: str) -> Any:
    """Find the first named non-None field in nested Garmin payloads."""
    if isinstance(payload, dict):
        for key in keys:
            if payload.get(key) is not None:
                return payload[key]
        for value in payload.values():
            found = _find_key(value, *keys)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_key(value, *keys)
            if found is not None:
                return found
    return None


def _morning_readiness(payload: Any) -> dict[str, Any]:
    """Select Garmin's post-wakeup readiness record when available."""
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, list):
        return {}
    morning = next((
        item for item in payload
        if isinstance(item, dict)
        and item.get("inputContext") == "AFTER_WAKEUP_RESET"
    ), None)
    if morning is not None:
        return morning
    return next((item for item in payload if isinstance(item, dict)), {})


class GarminPerformanceClient:
    """Narrow read-only client backed by a Garmin DI-auth session."""

    def __init__(self, auth_client: Client | None = None):
        self.auth = auth_client or Client()
        self._profile: dict[str, Any] | None = None

    def load_tokens(self, token_json: str) -> None:
        """Load serialized DI tokens; callers must decrypt storage first."""
        self.auth.loads(token_json)

    def dump_tokens(self) -> str:
        """Serialize DI tokens; callers must encrypt before persistence."""
        return self.auth.dumps()

    def profile(self) -> dict[str, Any]:
        if self._profile is None:
            self._profile = self.auth.connectapi(_PROFILE) or {}
        return self._profile

    def _display_name(self) -> str:
        value = self.profile().get("displayName")
        if not value:
            raise ValueError("Garmin profile has no displayName")
        return str(value)

    def activities(self, *, start: int = 0, limit: int = 20,
                   activity_type: str = "cycling") -> list[dict[str, Any]]:
        if start < 0 or limit < 1 or limit > MAX_ACTIVITY_LIMIT:
            raise ValueError("invalid Garmin activity page")
        params = {"start": str(start), "limit": str(limit)}
        if activity_type:
            params["activityType"] = activity_type
        result = self.auth.connectapi(_ACTIVITIES, params=params)
        return result if isinstance(result, list) else []

    def activity_pages_since(
            self, since: date, *, page_size: int = 100,
            max_activities: int = 2000):
        """Yield newest-first cycling pages until the requested date boundary."""
        if not isinstance(since, date):
            raise ValueError("Garmin activity history requires a date")
        if page_size < 1 or page_size > MAX_ACTIVITY_LIMIT:
            raise ValueError("invalid Garmin activity page size")
        if max_activities < 1 or max_activities > 5000:
            raise ValueError("invalid Garmin activity history bound")

        start = 0
        while start < max_activities:
            limit = min(page_size, max_activities - start)
            page = self.activities(start=start, limit=limit)
            if not page:
                break

            included = []
            reached_boundary = False
            for activity in page:
                raw_started = (
                    activity.get("startTimeGMT")
                    or activity.get("startTimeLocal")
                )
                activity_date = None
                if raw_started:
                    try:
                        activity_date = datetime.fromisoformat(
                            str(raw_started).replace("Z", "+00:00")).date()
                    except ValueError:
                        pass
                if activity_date is not None and activity_date < since:
                    reached_boundary = True
                else:
                    included.append(activity)

            if included:
                yield included
            start += len(page)
            # Garmin returns this endpoint newest-first. Once a page crosses
            # the cutoff, subsequent pages are older and need not be fetched.
            if reached_boundary or len(page) < limit:
                break

    def activity(self, activity_id: int) -> dict[str, Any]:
        """Return one activity summary using the upstream read endpoint."""
        if not isinstance(activity_id, int) or activity_id < 1:
            raise ValueError("invalid Garmin activity id")
        result = self.auth.connectapi(f"{_ACTIVITY}/{activity_id}")
        return result if isinstance(result, dict) else {}

    def activity_details(self, activity_id: int, *, max_chart: int = 2000,
                         max_polyline: int = 4000) -> dict[str, Any]:
        """Return bounded chart/polyline details for one Garmin activity."""
        if not isinstance(activity_id, int) or activity_id < 1:
            raise ValueError("invalid Garmin activity id")
        if not 0 < max_chart <= 5000 or not 0 <= max_polyline <= 10000:
            raise ValueError("invalid Garmin activity detail bounds")
        result = self.auth.connectapi(
            f"{_ACTIVITY}/{activity_id}/details",
            params={"maxChartSize": str(max_chart),
                    "maxPolylineSize": str(max_polyline)},
        )
        return result if isinstance(result, dict) else {}

    @staticmethod
    def normalize_activity(activity: dict[str, Any]) -> dict[str, Any]:
        """Normalize stable cycling fields while retaining raw data separately."""
        activity_id = activity.get("activityId")
        if not isinstance(activity_id, int) or activity_id < 1:
            raise ValueError("Garmin activity has no valid activityId")
        activity_type = activity.get("activityType") or {}
        return {
            "garmin_activity_id": activity_id,
            "activity_name": activity.get("activityName"),
            "activity_type": (
                activity_type.get("typeKey")
                if isinstance(activity_type, dict) else str(activity_type)),
            "started_at": (
                activity.get("startTimeGMT")
                or activity.get("startTimeLocal")),
            "distance_m": activity.get("distance"),
            "duration_s": activity.get("duration"),
            "moving_duration_s": activity.get("movingDuration"),
            "elevation_gain_m": activity.get("elevationGain"),
            "average_hr": activity.get("averageHR"),
            "max_hr": activity.get("maxHR"),
            "average_power": activity.get("avgPower"),
            "max_power": activity.get("maxPower"),
            "normalized_power": activity.get("normPower"),
            "aerobic_training_effect": activity.get("aerobicTrainingEffect"),
            "anaerobic_training_effect": activity.get(
                "anaerobicTrainingEffect"),
            "calories": activity.get("calories"),
            "average_cadence": (
                activity.get("averageBikingCadenceInRevPerMinute")
                or activity.get("averageCadence")),
            "device_name": activity.get("deviceName"),
        }

    def performance_snapshot(self, on_date: str | date) -> dict[str, Any]:
        """Fetch one private, read-only daily performance snapshot."""
        day = _date(on_date)
        display_name = self._display_name()
        summary = self.auth.connectapi(
            f"{_DAILY_SUMMARY}/{display_name}",
            params={"calendarDate": day},
        ) or {}
        heart_rate = self.auth.connectapi(
            f"{_HEART_RATE}/{display_name}", params={"date": day}) or {}
        sleep = self.auth.connectapi(
            f"{_SLEEP}/{display_name}",
            params={"date": day, "nonSleepBufferMinutes": 60},
        ) or {}
        stress = self.auth.connectapi(f"{_STRESS}/{day}") or {}
        body_battery = self.auth.connectapi(
            _BODY_BATTERY, params={"startDate": day, "endDate": day}) or []
        hrv = self.auth.connectapi(f"{_HRV}/{day}") or {}
        max_metrics = self.auth.connectapi(
            f"{_MAX_METRICS}/{day}/{day}") or {}
        readiness = self.auth.connectapi(
            f"{_TRAINING_READINESS}/{day}") or []
        training_status = self.auth.connectapi(
            f"{_TRAINING_STATUS}/{day}") or {}
        try:
            endurance_score = self.auth.connectapi(
                _ENDURANCE_SCORE, params={"calendarDate": day}) or {}
        except GarminConnectNotFoundError:
            # Not every Garmin device/account exposes Endurance Score.
            endurance_score = {}
        morning_readiness = _morning_readiness(readiness)

        return {
            "date": day,
            "resting_heart_rate": _dig(
                heart_rate, ("restingHeartRate",), ("restingHeartRateValue",)),
            "hrv_status": _dig(
                hrv, ("hrvSummary", "status"), ("hrvSummary", "weeklyAvg")),
            "sleep_score": _dig(
                sleep, ("dailySleepDTO", "sleepScores", "overall", "value"),
                ("sleepScores", "overall", "value")),
            "body_battery": _dig(
                body_battery[0] if isinstance(body_battery, list) and body_battery
                else body_battery,
                ("charged",), ("bodyBatteryMostRecentValue",)),
            "training_readiness": _dig(
                morning_readiness,
                ("score",), ("trainingReadinessScore",)),
            "readiness_level": _dig(morning_readiness, ("level",)),
            "readiness_feedback": _dig(
                morning_readiness, ("feedbackShort",), ("feedbackLong",)),
            "recovery_time_minutes": _dig(
                morning_readiness, ("recoveryTime",)),
            "sleep_factor_percent": _dig(
                morning_readiness, ("sleepScoreFactorPercent",)),
            "acwr_factor_percent": _dig(
                morning_readiness, ("acwrFactorPercent",)),
            "hrv_factor_percent": _dig(
                morning_readiness, ("hrvFactorPercent",)),
            "vo2_max_cycling": _dig(
                max_metrics, ("cycling", "vo2Max"), ("generic", "vo2Max"),
                ("vo2MaxPreciseValue",)),
            "training_status": _find_key(
                training_status, "trainingStatusFeedbackPhrase",
                "trainingStatus"),
            "acute_training_load": _find_key(
                training_status, "dailyTrainingLoadAcute",
                "acuteTrainingLoad", "weeklyTrainingLoad"),
            "load_level_trend": _find_key(
                training_status, "loadLevelTrend"),
            "endurance_score": _find_key(
                endurance_score, "enduranceScore", "overallScore", "score"),
            "raw": {
                "summary": summary,
                "heart_rate": heart_rate,
                "sleep": sleep,
                "stress": stress,
                "body_battery": body_battery,
                "hrv": hrv,
                "max_metrics": max_metrics,
                "training_readiness": readiness,
                "training_status": training_status,
                "endurance_score": endurance_score,
            },
        }
