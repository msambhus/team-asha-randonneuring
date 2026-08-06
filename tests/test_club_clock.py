from datetime import datetime, timezone

from services.club_clock import club_today, instant_time_labels, schedule_time_labels


def test_club_today_stays_on_pacific_day_after_utc_midnight():
    assert club_today(datetime(2026, 7, 31, 0, 30, tzinfo=timezone.utc)).isoformat() == (
        "2026-07-30"
    )


def test_upcoming_calendar_uses_club_day_boundary():
    from pathlib import Path

    root = Path(__file__).parents[1]
    models = (root / "models.py").read_text()
    riders = (root / "routes/riders.py").read_text()

    assert "def get_all_upcoming_events" in models
    assert "today = club_today()" in models
    assert "cutoff = club_today() + timedelta(days=28)" in riders


def test_schedule_time_labels_show_event_clock_then_pacific():
    ride = {'date': '2026-08-06', 'region': 'Minnesota'}

    start = schedule_time_labels(ride, '05:00', 0)
    overnight = schedule_time_labels(ride, '05:00', 24 * 60)

    assert start == {
        'event': '05:00', 'event_zone': 'CT', 'pacific': '03:00',
        'show_pacific': True,
    }
    assert overnight['event'] == '05:00+1'
    assert overnight['pacific'] == '03:00+1'


def test_schedule_time_labels_do_not_duplicate_pacific_clock():
    labels = schedule_time_labels(
        {'date': '2026-08-06', 'region': 'California'}, '05:00', 130)

    assert labels['event'] == '07:10'
    assert labels['event_zone'] == 'PT'
    assert labels['show_pacific'] is False


def test_instant_time_labels_convert_to_event_and_pacific():
    value = datetime(2026, 8, 6, 16, 30, tzinfo=timezone.utc)
    labels = instant_time_labels(value, {'region': 'Minnesota'})

    assert labels['event'] == '11:30 AM'
    assert labels['event_zone'] == 'CT'
    assert labels['pacific'] == '9:30 AM'
    assert labels['show_pacific'] is True
