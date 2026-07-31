import datetime

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.profile_type import (
    Event,
    EventType,
    FileType,
    Manufacturer,
)

from services.garmin_fit import (
    decode_fit_gearing,
    derive_garmin_fit_segment_metrics,
)


def _fit_bytes():
    builder = FitFileBuilder(auto_define=True)
    timestamp = round(datetime.datetime(
        2026, 1, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    file_id = FileIdMessage()
    file_id.type = FileType.ACTIVITY
    file_id.manufacturer = Manufacturer.GARMIN
    file_id.time_created = timestamp
    builder.add(file_id)

    for seconds, power, distance, rear_teeth in (
        (0, 100, 0, 33),
        (10, 200, 1000, 28),
        (20, 300, 2000, None),
    ):
        record = RecordMessage()
        record.timestamp = timestamp + seconds * 1000
        record.power = power
        record.distance = distance
        record.cadence = 80
        builder.add(record)
        if rear_teeth:
            event = EventMessage()
            event.event = Event.REAR_GEAR_CHANGE
            event.event_type = EventType.MARKER
            event.timestamp = timestamp + seconds * 1000
            event.front_gear_num = 2
            event.rear_gear_num = 1 if seconds == 0 else 2
            event.front_gear = 48
            event.rear_gear = rear_teeth
            builder.add(event)
    return builder.build().to_bytes()


def test_fit_gearing_extracts_full_events_usage_and_power():
    result = decode_fit_gearing(_fit_bytes())

    assert result["events"][0]["rear_teeth"] == 33
    assert result["events"][1]["distance_m"] == 1000
    assert result["summary"]["gear_events"] == 2
    assert result["summary"]["rear_time_seconds"] == {
        "33": 10, "28": 10}
    assert result["summary"]["rear_average_power"] == {
        "33": 100, "28": 250}
    assert result["summary"]["coverage_seconds"] == 20


def test_fit_gearing_segments_use_actual_tooth_counts():
    gearing = decode_fit_gearing(_fit_bytes())
    result = derive_garmin_fit_segment_metrics(
        [{"gear_events": gearing["events"]}],
        [{
            "location": "Control",
            "distance_miles": 2000 / 1609.344,
            "actual_avg_cadence": 80,
            "actual_climb_ft_per_mi": 20,
        }],
    )

    segment = result["Control"]
    assert segment["source"] == "garmin_fit"
    assert segment["rear"]["start_teeth"] == 33
    assert segment["rear"]["end_teeth"] == 28
    assert segment["rear"]["dominant_teeth"] in (33, 28)
    assert segment["total_shifts"] == 1
    assert segment["advice"] == []
