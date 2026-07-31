from datetime import datetime, timezone

from services.club_clock import club_today


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
