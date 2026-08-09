from pathlib import Path

from shared.calendar_view import calendar_event, completed_event, finisher_row, group_events_by_month


ROOT = Path(__file__).resolve().parents[2]


def test_vendored_calendar_view_stays_identical():
    assert (
        ROOT / 'shared' / 'calendar_view.py'
    ).read_bytes() == (
        ROOT / 'brevethub' / 'shared' / 'calendar_view.py'
    ).read_bytes()


def test_calendar_contract_is_lossless_and_supplies_defaults():
    event = calendar_event({
        'id': 12,
        'name': 'ACP 300K',
        'date': '2026-03-14',
        'custom_team_field': 'preserved',
    })

    assert event['id'] == 12
    assert event['date_str'] == '2026-03-14'
    assert event['signup_count'] == 0
    assert event['rwgps_url'] is None
    assert event['custom_team_field'] == 'preserved'


def test_completed_event_and_finisher_contract():
    event = completed_event({'id': 4, 'finisher_count': 7})
    finisher = finisher_row({
        'rider_id': 9,
        'first_name': 'Asha',
        'last_name': 'Rider',
        'finish_time': '12:34',
        'status': 'FINISHED',
    })

    assert event['finisher_count'] == 7
    assert event['finishers_url'] is None
    assert finisher['display_name'] == 'Asha Rider'
    assert finisher['finish_time'] == '12:34'
    assert finisher['status'] == 'FINISHED'


def test_group_events_by_month():
    events = [
        calendar_event({'id': 1, 'date': '2026-03-14', 'name': 'A'}),
        calendar_event({'id': 2, 'date': '2026-03-28', 'name': 'B'}),
        calendar_event({'id': 3, 'date': '2026-04-05', 'name': 'C'}),
    ]
    groups = group_events_by_month(events)
    assert [label for label, _ in groups] == ['March 2026', 'April 2026']
    assert [ev['id'] for _, bucket in groups for ev in bucket] == [1, 2, 3]


def test_vendored_calendar_table_partial_stays_identical():
    assert (
        ROOT / 'templates' / 'partials' / '_calendar_upcoming_table.html'
    ).read_bytes() == (
        ROOT / 'brevethub' / 'templates' / 'partials' / '_calendar_upcoming_table.html'
    ).read_bytes()
