"""Unit tests for the pure shared season / SR / R-12 logic (shared/seasons.py).

Deterministic and framework-free: every date-relative call takes an explicit
``today``, so there is no clock/DB/network to mock. Proves the Nov 1 season
boundary (the calendar-year→randonneuring-season fix), per-season SR true/false,
career totals, and R-12 awards + current-streak activity.
"""
from datetime import date

from shared import seasons


# --------------------------------------------------------------------------- #
# Season boundary (Nov 1 – Oct 31)
# --------------------------------------------------------------------------- #
def test_season_name_uses_nov_1_boundary():
    # November opens the new season named for the Nov→Oct window it spans.
    assert seasons.season_name_for_date('2024-11-15') == '2024-2025'
    # Spring of the following calendar year stays in the same season.
    assert seasons.season_name_for_date('2025-03-01') == '2024-2025'


def test_season_boundary_is_exact_oct_31_vs_nov_1():
    assert seasons.season_name_for_date('2025-10-31') == '2024-2025'
    assert seasons.season_name_for_date('2025-11-01') == '2025-2026'


def test_season_name_accepts_date_objects_and_bad_input():
    assert seasons.season_name_for_date(date(2024, 11, 1)) == '2024-2025'
    assert seasons.season_name_for_date(None) is None
    assert seasons.season_name_for_date('not-a-date') is None


def test_current_season_name_from_today():
    assert seasons.current_season_name(date(2025, 7, 15)) == '2024-2025'
    assert seasons.current_season_name(date(2025, 12, 1)) == '2025-2026'


# --------------------------------------------------------------------------- #
# Grouping + per-season SR
# --------------------------------------------------------------------------- #
def _brevet(iso, dist):
    return {'date': iso, 'distance_km': dist, 'finish_time': '', 'route_name': f'{dist}'}


def test_group_brevets_groups_across_calendar_year_into_one_season():
    brevets = [
        _brevet('2024-11-15', 200),
        _brevet('2025-03-01', 300),
        _brevet('2025-04-01', 400),
        _brevet('2025-06-01', 600),
    ]
    groups = seasons.group_brevets_by_season(brevets)
    assert len(groups) == 1
    assert groups[0]['season'] == '2024-2025'
    assert len(groups[0]['brevets']) == 4
    # newest ride first within the season
    assert groups[0]['brevets'][0]['date'] == '2025-06-01'


def test_group_splits_across_the_boundary():
    brevets = [_brevet('2025-10-31', 200), _brevet('2025-11-01', 300)]
    groups = seasons.group_brevets_by_season(brevets)
    assert [g['season'] for g in groups] == ['2025-2026', '2024-2025']  # newest first


def test_sr_progress_true_when_all_four_legs_present():
    brevets = [_brevet('2024-11-15', 200), _brevet('2025-03-01', 300),
               _brevet('2025-04-01', 400), _brevet('2025-06-01', 600)]
    sr = seasons.sr_progress(brevets)
    assert sr['is_sr'] is True
    assert sr['legs'] == {200: True, 300: True, 400: True, 600: True}


def test_sr_progress_false_when_missing_600():
    brevets = [_brevet('2025-03-01', 200), _brevet('2025-04-01', 300),
               _brevet('2025-05-01', 400)]
    sr = seasons.sr_progress(brevets)
    assert sr['is_sr'] is False
    assert sr['legs'][600] is False


def test_sr_1000_satisfies_the_600_leg():
    brevets = [_brevet('2025-03-01', 200), _brevet('2025-04-01', 300),
               _brevet('2025-05-01', 400), _brevet('2025-06-01', 1000)]
    assert seasons.sr_progress(brevets)['is_sr'] is True


# --------------------------------------------------------------------------- #
# Per-season summary + seasons_with_summaries
# --------------------------------------------------------------------------- #
def test_season_summary_totals_and_bands():
    brevets = [_brevet('2025-01-01', 200), _brevet('2025-02-01', 600),
               _brevet('2025-03-01', 1000)]
    s = seasons.season_summary(brevets)
    assert s['total_km'] == 1800
    assert s['count'] == 3
    assert s['bands'] == {'200': 1, '300': 0, '400': 0, '600': 1, '1000': 1}


def test_seasons_with_summaries_flags_current():
    brevets = [_brevet('2025-06-01', 200), _brevet('2024-06-01', 300)]
    out = seasons.seasons_with_summaries(brevets, date(2025, 7, 15))
    current = {s['season']: s['is_current'] for s in out}
    assert current == {'2024-2025': True, '2023-2024': False}


# --------------------------------------------------------------------------- #
# R-12 awards + current streak
# --------------------------------------------------------------------------- #
def _twelve_consecutive(start_year, start_month):
    """Twelve 200 km brevets, one per consecutive calendar month."""
    out = []
    y, m = start_year, start_month
    for _ in range(12):
        out.append(_brevet(f'{y}-{m:02d}-15', 200))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def test_r12_award_for_twelve_consecutive_months():
    awards = seasons.r12_awards(_twelve_consecutive(2024, 1))
    assert len(awards) == 1
    assert awards[0] == {'start_month': '2024-01', 'end_month': '2024-12'}


def test_r12_no_award_when_a_month_is_missing():
    brevets = _twelve_consecutive(2024, 1)
    del brevets[5]  # drop June 2024 — breaks the consecutive run
    assert seasons.r12_awards(brevets) == []


def test_r12_two_awards_for_twenty_four_consecutive_months():
    brevets = _twelve_consecutive(2023, 1) + _twelve_consecutive(2024, 1)
    assert len(seasons.r12_awards(brevets)) == 2


def test_r12_sub_200_rides_do_not_qualify_a_month():
    brevets = _twelve_consecutive(2024, 1)
    brevets[3] = _brevet('2024-04-15', 100)  # too short to count for April
    assert seasons.r12_awards(brevets) == []


def test_r12_current_streak_active_when_recent():
    brevets = _twelve_consecutive(2025, 1)  # ends Dec 2025
    streak = seasons.r12_current_streak(brevets, date(2026, 1, 10))
    assert streak['months'] == 12
    assert streak['active'] is True  # last month Dec 2025 is 1 month before today


def test_r12_current_streak_inactive_when_stale():
    brevets = _twelve_consecutive(2024, 1)  # ends Dec 2024
    streak = seasons.r12_current_streak(brevets, date(2026, 7, 15))
    assert streak['months'] == 12
    assert streak['active'] is False


def test_r12_current_streak_empty():
    assert seasons.r12_current_streak([], date(2026, 7, 15)) == {'months': 0, 'active': False}


# --------------------------------------------------------------------------- #
# Career summary
# --------------------------------------------------------------------------- #
def test_career_summary_totals_and_current_sr():
    brevets = [_brevet('2024-11-15', 200), _brevet('2025-03-01', 300),
               _brevet('2025-04-01', 400), _brevet('2025-06-01', 600)]
    career = seasons.career_summary(brevets, date(2025, 7, 15))
    assert career['total_km'] == 1500
    assert career['count'] == 4
    assert career['current_season'] == '2024-2025'
    assert career['current_sr']['is_sr'] is True
    assert career['sr_seasons'] == ['2024-2025']
    assert career['is_sr'] is True


def test_career_summary_empty_history_is_graceful():
    career = seasons.career_summary([], date(2025, 7, 15))
    assert career['total_km'] == 0
    assert career['count'] == 0
    assert career['is_sr'] is False
    assert career['current_sr']['is_sr'] is False
    assert career['r12_streak'] == {'months': 0, 'active': False}
    assert career['r12_awards'] == []
