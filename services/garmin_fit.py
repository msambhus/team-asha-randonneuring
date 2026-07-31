"""Bounded decoding of electronic-shifting data from Garmin Activity FIT files."""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

import fitdecode


MAX_FIT_BYTES = 30 * 1024 * 1024
MAX_RECORDS = 250_000
MAX_GEAR_EVENTS = 20_000


def _epoch(value: Any) -> float | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    numeric = _number(value)
    return int(numeric) if numeric is not None and numeric > 0 else None


def _message_values(frame) -> dict[str, Any]:
    return {field.name: field.value for field in frame.fields}


def _validate_fit(data: bytes) -> None:
    if not isinstance(data, bytes) or len(data) < 14:
        raise ValueError("Garmin activity FIT is empty")
    if len(data) > MAX_FIT_BYTES:
        raise ValueError("Garmin activity FIT exceeds the safe parsing limit")
    if data[8:12] != b".FIT":
        raise ValueError("Garmin activity download is not a FIT file")


def _gear_event(values: dict[str, Any]) -> dict[str, Any] | None:
    event_name = str(values.get("event") or "").lower()
    if event_name not in {"front_gear_change", "rear_gear_change"}:
        return None
    timestamp = _epoch(values.get("timestamp"))
    if timestamp is None:
        return None
    return {
        "timestamp": round(timestamp, 3),
        "event": event_name,
        "front_gear_num": _integer(values.get("front_gear_num")),
        "rear_gear_num": _integer(values.get("rear_gear_num")),
        "front_teeth": _integer(values.get("front_gear")),
        "rear_teeth": _integer(values.get("rear_gear")),
    }


def _nearest_record(records, timestamps, timestamp):
    index = bisect_right(timestamps, timestamp) - 1
    return records[index] if index >= 0 else None


def decode_fit_gearing(data: bytes) -> dict[str, Any]:
    """Decode normalized gear events and full-ride usage/power aggregates."""
    _validate_fit(data)
    records = []
    events = []
    with fitdecode.FitReader(BytesIO(data)) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            values = _message_values(frame)
            if frame.name == "record":
                timestamp = _epoch(values.get("timestamp"))
                if timestamp is None:
                    continue
                if len(records) >= MAX_RECORDS:
                    raise ValueError("Garmin FIT has too many records")
                records.append({
                    "timestamp": timestamp,
                    "distance_m": _number(values.get("distance")),
                    "power": _number(values.get("power")),
                    "cadence": _number(values.get("cadence")),
                    "altitude_m": _number(
                        values.get("enhanced_altitude")
                        if values.get("enhanced_altitude") is not None
                        else values.get("altitude")),
                })
            elif frame.name == "event":
                event = _gear_event(values)
                if event:
                    if len(events) >= MAX_GEAR_EVENTS:
                        raise ValueError("Garmin FIT has too many gear events")
                    events.append(event)

    records.sort(key=lambda row: row["timestamp"])
    events.sort(key=lambda row: row["timestamp"])
    if not events:
        return {
            "events": [],
            "summary": {
                "source": "garmin_fit",
                "gear_events": 0,
                "has_gearing": False,
            },
        }

    record_timestamps = [row["timestamp"] for row in records]
    activity_start = (
        records[0]["timestamp"] if records else events[0]["timestamp"])
    activity_end = (
        records[-1]["timestamp"] if records else events[-1]["timestamp"])
    normalized = []
    for event in events:
        record = _nearest_record(
            records, record_timestamps, event["timestamp"])
        normalized.append({
            **event,
            "elapsed_s": round(event["timestamp"] - activity_start, 3),
            "distance_m": (
                round(record["distance_m"], 2)
                if record and record.get("distance_m") is not None else None),
            "power": (
                round(record["power"])
                if record and record.get("power") is not None else None),
            "cadence": (
                round(record["cadence"], 1)
                if record and record.get("cadence") is not None else None),
        })

    # Each gear-change event reports both current gears. Treat the interval
    # until the next event as time in that combination.
    front_seconds = defaultdict(float)
    rear_seconds = defaultdict(float)
    for index, event in enumerate(normalized):
        interval_end = (
            normalized[index + 1]["timestamp"]
            if index + 1 < len(normalized) else activity_end)
        seconds = max(0.0, interval_end - event["timestamp"])
        if event.get("front_teeth"):
            front_seconds[event["front_teeth"]] += seconds
        if event.get("rear_teeth"):
            rear_seconds[event["rear_teeth"]] += seconds

    # Attribute each power record to the most recent gear event. These are
    # sample averages, not normalized power or time-weighted estimates.
    event_timestamps = [event["timestamp"] for event in normalized]
    front_power = defaultdict(list)
    rear_power = defaultdict(list)
    for record in records:
        power = record.get("power")
        if power is None:
            continue
        index = bisect_right(event_timestamps, record["timestamp"]) - 1
        if index < 0:
            continue
        gear = normalized[index]
        if gear.get("front_teeth"):
            front_power[gear["front_teeth"]].append(power)
        if gear.get("rear_teeth"):
            rear_power[gear["rear_teeth"]].append(power)

    def rendered_seconds(rows):
        return {
            str(teeth): round(seconds)
            for teeth, seconds in sorted(rows.items(), reverse=True)
        }

    def rendered_power(rows):
        return {
            str(teeth): round(sum(values) / len(values))
            for teeth, values in sorted(rows.items(), reverse=True)
            if values
        }

    max_front_seconds = max(front_seconds.values(), default=0)
    max_rear_seconds = max(rear_seconds.values(), default=0)

    def usage_rows(seconds_by_gear, power_by_gear, maximum):
        return [
            {
                "teeth": teeth,
                "seconds": round(seconds),
                "bar_percentage": (
                    round(seconds * 100.0 / maximum, 1)
                    if maximum else 0),
                "average_power": (
                    round(sum(power_by_gear[teeth])
                          / len(power_by_gear[teeth]))
                    if power_by_gear.get(teeth) else None),
            }
            for teeth, seconds in sorted(
                seconds_by_gear.items(), reverse=True)
        ]

    covered_seconds = max(0.0, activity_end - normalized[0]["timestamp"])
    return {
        "events": normalized,
        "summary": {
            "source": "garmin_fit",
            "has_gearing": True,
            "gear_events": len(normalized),
            "front_shift_events": sum(
                event["event"] == "front_gear_change"
                for event in normalized),
            "rear_shift_events": sum(
                event["event"] == "rear_gear_change"
                for event in normalized),
            "front_teeth": sorted({
                event["front_teeth"] for event in normalized
                if event.get("front_teeth")
            }, reverse=True),
            "rear_teeth": sorted({
                event["rear_teeth"] for event in normalized
                if event.get("rear_teeth")
            }, reverse=True),
            "front_time_seconds": rendered_seconds(front_seconds),
            "rear_time_seconds": rendered_seconds(rear_seconds),
            "front_average_power": rendered_power(front_power),
            "rear_average_power": rendered_power(rear_power),
            "front_usage": usage_rows(
                front_seconds, front_power, max_front_seconds),
            "rear_usage": usage_rows(
                rear_seconds, rear_power, max_rear_seconds),
            "coverage_start_elapsed_s": round(
                normalized[0]["timestamp"] - activity_start),
            "coverage_seconds": round(covered_seconds),
            "record_count": len(records),
        },
    }


def _segment_gear(events, start_m, end_m, prefix):
    teeth_key = f"{prefix}_teeth"
    event_name = f"{prefix}_gear_change"
    known = [
        event for event in events
        if event.get("distance_m") is not None and event.get(teeth_key)
    ]
    if not known:
        return None
    before = [event for event in known if event["distance_m"] <= start_m]
    within = [
        event for event in known
        if start_m < event["distance_m"] <= end_m
    ]
    active = (before[-1:] + within)
    if not active:
        return None
    duration_by_teeth = defaultdict(float)
    for index, event in enumerate(active):
        interval_start = max(start_m, event["distance_m"])
        next_distance = (
            active[index + 1]["distance_m"]
            if index + 1 < len(active) else end_m)
        interval_end = min(end_m, next_distance)
        if interval_end > interval_start:
            duration_by_teeth[event[teeth_key]] += (
                interval_end - interval_start)
    dominant = (
        max(duration_by_teeth, key=duration_by_teeth.get)
        if duration_by_teeth else active[-1][teeth_key])
    return {
        "start_teeth": active[0][teeth_key],
        "end_teeth": active[-1][teeth_key],
        "dominant_teeth": dominant,
        "positions_used": len({
            event[teeth_key] for event in active}),
        "shift_count": sum(
            event.get("event") == event_name for event in within),
    }


def derive_garmin_fit_segment_metrics(recordings, comparison_rows):
    """Summarize full-ride FIT gear events within planned brevet segments."""
    events = sorted(
        (
            event
            for recording in (recordings or [])
            for event in (recording.get("gear_events") or [])
            if isinstance(event, dict)
        ),
        key=lambda event: (
            float(event.get("distance_m") or 0),
            float(event.get("timestamp") or 0),
        ),
    )
    if not events or not comparison_rows:
        return {}
    result = {}
    previous_m = 0.0
    rows = sorted(
        (row for row in comparison_rows if not row.get("is_extra")),
        key=lambda row: float(row.get("distance_miles") or 0),
    )
    for row in rows:
        end_m = float(row.get("distance_miles") or 0) * 1609.344
        if end_m <= previous_m:
            previous_m = end_m
            continue
        segment_events = [
            event for event in events
            if previous_m < float(event.get("distance_m") or -1) <= end_m
        ]
        rear = _segment_gear(events, previous_m, end_m, "rear")
        front = _segment_gear(events, previous_m, end_m, "front")
        if not rear and not front:
            previous_m = end_m
            continue
        shifts = len(segment_events)
        distance_miles = (end_m - previous_m) / 1609.344
        shift_rate = shifts / distance_miles if distance_miles > 0 else None
        cadence = _number(row.get("actual_avg_cadence"))
        climb_rate = _number(row.get("actual_climb_ft_per_mi"))
        advice = []
        if (
            cadence is not None and cadence < 65
            and climb_rate is not None and climb_rate >= 40
            and rear and rear.get("dominant_teeth")
        ):
            advice.append(
                f"Cadence averaged {cadence:g} rpm on this climbing segment "
                f"while the dominant recorded rear cog was "
                f"{rear['dominant_teeth']}T. Shift earlier or reduce effort "
                "if cadence continues to fall.")
        if shifts >= 10 and shift_rate is not None and shift_rate >= 2:
            advice.append(
                f"{shifts} FIT-recorded shifts ({shift_rate:.1f}/mi) suggest "
                "frequent gear searching; anticipate grade changes and settle "
                "into a sustainable cadence sooner.")
        result[row.get("location") or str(end_m)] = {
            "source": "garmin_fit",
            "start_mi": round(previous_m / 1609.344, 1),
            "end_mi": round(end_m / 1609.344, 1),
            "rear": rear,
            "front": front,
            "total_shifts": shifts,
            "shifts_per_mile": (
                round(shift_rate, 1) if shift_rate is not None else None),
            "average_cadence": cadence,
            "sample_count": len(segment_events),
            "advice": advice,
        }
        previous_m = end_m
    return result
