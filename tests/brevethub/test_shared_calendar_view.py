from pathlib import Path

from shared.calendar_view import calendar_event, completed_event, finisher_row


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
