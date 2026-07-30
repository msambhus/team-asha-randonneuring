"""Tests for services.ride_coach — LLM ride coach.

Mocks the OpenAI client via the patchable _get_client seam (mirrors
tests/test_chat_service.py). Covers: happy path, no-key degradation, API
error, non-JSON content, cache hit, and the prompt-injection guard (system
message must contain no rider data).
"""
import json
import pytest
from unittest.mock import patch, MagicMock

import services.ride_coach as ride_coach


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts with an empty module cache."""
    ride_coach._cache.clear()
    yield
    ride_coach._cache.clear()


def _mock_completion(content):
    """Build a mock OpenAI chat completion whose message.content is `content`."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    return resp


def _mock_client(content):
    """Mock OpenAI client whose chat.completions.create returns `content`."""
    client = MagicMock()
    client.chat.completions.create.return_value = _mock_completion(content)
    return client


_ACTIVITY = {'strava_activity_id': 999, 'start_date_local': '2026-06-01T06:00:00'}

_ROWS = [
    {'location': 'Start', 'stop_type': 'start', 'distance_miles': 0.0,
     'actual_avg_watts': None, 'actual_avg_hr': None, 'actual_speed_mph': None,
     'actual_grade_pct': None, 'actual_np_watts': None, 'actual_avg_cadence': None,
     'actual_elev_gain_ft': None, 'vs_prev': None, 'is_extra': False},
    {'location': 'Pescadero Control', 'stop_type': 'control', 'distance_miles': 42.0,
     'actual_avg_watts': 182, 'actual_avg_hr': 148, 'actual_speed_mph': 15.2,
     'actual_grade_pct': 3.1, 'actual_np_watts': 195, 'actual_avg_cadence': 82,
     'actual_elev_gain_ft': 2400, 'vs_prev': {'watts_pct': 8}, 'is_extra': False},
    {'location': 'Finish', 'stop_type': 'finish', 'distance_miles': 84.0,
     'actual_avg_watts': 150, 'actual_avg_hr': 152, 'actual_speed_mph': 13.1,
     'actual_grade_pct': 2.0, 'actual_np_watts': 168, 'actual_avg_cadence': 74,
     'actual_elev_gain_ft': 1800, 'vs_prev': {'watts_pct': -18}, 'is_extra': False},
]

_SUMMARY = {
    'plan_distance_miles': 84.0, 'actual_distance_miles': 84.3,
    'plan_elevation_ft': 4200, 'actual_elevation_ft': 4300,
    'plan_total_time_min': 360, 'actual_elapsed_time_min': 400,
    'actual_moving_time_min': 340, 'actual_stopped_time_min': 60,
    'plan_break_time_min': 40, 'plan_avg_speed_mph': 14.0,
    'actual_avg_speed_mph': 14.9, 'stops_planned': 2, 'stops_detected': 3,
    'stops_extra': 1, 'distance_delta_miles': 0.3, 'elevation_delta_ft': 100,
    'time_delta_min': 40, 'speed_delta_mph': 0.9, 'break_delta_min': 20,
}

_HR_POWER = {'avg_hr': 150, 'max_hr': 172, 'avg_watts': 165, 'weighted_avg_watts': 180}

_STOP_WIND = {'Pescadero Control': {'wind_speed_mph': 12, 'wind_relative': 'headwind'}}

_RIDE_BASELINE = {'typical_avg_watts': 170, 'typical_avg_hr': 145}
_BAND_BASELINE = {'0-3%': {'avg_watts': 160}, '3-6%': {'avg_watts': 175}}
_SEGMENT_NARRATIVES = {'Pescadero Control': 'Strong climbing segment.'}

_VALID_JSON = json.dumps({
    'per_segment': {
        'Pescadero Control': 'You held 182W on the 3.1% grade, above your 175W norm.',
        'Finish': 'Power dropped 18% — you faded late; fuel earlier next time.',
    },
    'overall': {
        'summary': 'Solid ride with a late fade in the last segment.',
        'recommendations': [
            'Start fueling within the first hour.',
            'Cap early-segment power to avoid the late fade.',
            'Cut stopped time from 60 to 40 minutes.',
        ],
    },
})


def _call(**overrides):
    """Invoke generate_ride_coaching with default args, allowing overrides."""
    kwargs = dict(
        rider_id=5, ride_id=11, match_id=77, activity=_ACTIVITY, rows=_ROWS,
        summary=_SUMMARY, hr_power=_HR_POWER, stop_wind=_STOP_WIND,
        ride_baseline=_RIDE_BASELINE, band_baseline=_BAND_BASELINE,
        segment_narratives=_SEGMENT_NARRATIVES,
    )
    kwargs.update(overrides)
    return ride_coach.generate_ride_coaching(**kwargs)


# --------------------------------------------------------------------------
# (a) Happy path
# --------------------------------------------------------------------------
def test_happy_path_returns_per_segment_and_overall(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    client = _mock_client(_VALID_JSON)
    with patch('services.ride_coach._get_client', return_value=client):
        result = _call()

    assert 'per_segment' in result
    assert 'overall' in result
    assert result['per_segment']['Pescadero Control'].startswith('You held 182W')
    assert result['overall']['summary'].startswith('Solid ride')
    assert isinstance(result['overall']['recommendations'], list)
    assert 3 <= len(result['overall']['recommendations']) <= 6
    client.chat.completions.create.assert_called_once()


def test_happy_path_strips_markdown_fences(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    fenced = "```json\n" + _VALID_JSON + "\n```"
    client = _mock_client(fenced)
    with patch('services.ride_coach._get_client', return_value=client):
        result = _call()
    assert result['per_segment']['Finish'].startswith('Power dropped 18%')


# --------------------------------------------------------------------------
# Injection guard: SYSTEM message must contain NO rider data
# --------------------------------------------------------------------------
def test_system_message_contains_no_rider_data(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    client = _mock_client(_VALID_JSON)
    with patch('services.ride_coach._get_client', return_value=client):
        _call()

    messages = client.chat.completions.create.call_args[1]['messages']
    system_msg = next(m for m in messages if m['role'] == 'system')['content']
    user_msg = next(m for m in messages if m['role'] == 'user')['content']

    # Rider data must appear ONLY in the user message.
    assert 'Pescadero Control' not in system_msg
    assert '182' not in system_msg
    # The system prompt describes wind as a concept (may say "headwind"); what must
    # NOT leak is the rider's actual wind DATA (e.g. the specific mph value).
    assert 'weather=' not in system_msg
    assert str(_ACTIVITY['strava_activity_id']) not in system_msg
    # And it must actually be present in the user (data) message.
    assert 'Pescadero Control' in user_msg
    assert '<segments>' in user_msg
    assert '<ride_summary>' in user_msg
    # Data-not-instructions guard note present.
    assert 'not' in user_msg and 'instructions' in user_msg


# --------------------------------------------------------------------------
# (b) No OPENAI_API_KEY → {}
# --------------------------------------------------------------------------
def test_no_api_key_returns_empty(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    # Use the real _get_client (not patched) so the missing key path runs.
    result = _call()
    assert result == {}


# --------------------------------------------------------------------------
# (c) create() raises → {}
# --------------------------------------------------------------------------
def test_create_raises_returns_empty(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    client = MagicMock()
    client.chat.completions.create.side_effect = Exception('API down')
    with patch('services.ride_coach._get_client', return_value=client):
        result = _call()
    assert result == {}


# --------------------------------------------------------------------------
# (d) Non-JSON content → {}
# --------------------------------------------------------------------------
def test_non_json_content_returns_empty(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    client = _mock_client('Sorry, I cannot produce JSON right now.')
    with patch('services.ride_coach._get_client', return_value=client):
        result = _call()
    assert result == {}


def test_wrong_shape_returns_empty(monkeypatch):
    """Valid JSON but missing per_segment/overall → {}."""
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    client = _mock_client(json.dumps({'foo': 'bar'}))
    with patch('services.ride_coach._get_client', return_value=client):
        result = _call()
    assert result == {}


# --------------------------------------------------------------------------
# Empty inputs → {} (no client call)
# --------------------------------------------------------------------------
def test_empty_rows_returns_empty(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    client = _mock_client(_VALID_JSON)
    with patch('services.ride_coach._get_client', return_value=client):
        result = _call(rows=[])
    assert result == {}
    client.chat.completions.create.assert_not_called()


# --------------------------------------------------------------------------
# (e) Cache hit avoids a second create() call
# --------------------------------------------------------------------------
def test_cache_hit_avoids_second_call(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    client = _mock_client(_VALID_JSON)
    with patch('services.ride_coach._get_client', return_value=client):
        first = _call()
        second = _call()

    assert first == second
    assert first  # non-empty
    # Same inputs → cache hit → exactly one API call total.
    client.chat.completions.create.assert_called_once()


def test_cache_key_composition():
    """Cache key changes when segment inputs change, stable otherwise."""
    k1 = ride_coach._cache_key(5, 11, 77, _ACTIVITY, _ROWS)
    k2 = ride_coach._cache_key(5, 11, 77, _ACTIVITY, _ROWS)
    assert k1 == k2  # deterministic

    changed_rows = [dict(r) for r in _ROWS]
    changed_rows[1]['actual_avg_watts'] = 300
    k3 = ride_coach._cache_key(5, 11, 77, _ACTIVITY, changed_rows)
    assert k1 != k3  # segment change busts the key

    # Different activity id also busts the key.
    k4 = ride_coach._cache_key(5, 11, 77, {'strava_activity_id': 1, 'start_date_local': ''}, _ROWS)
    assert k1 != k4

    # Bumping the prompt-version token busts every cached key so a prompt
    # change refreshes coaching immediately instead of serving stale text.
    original_version = ride_coach._PROMPT_VERSION
    try:
        ride_coach._PROMPT_VERSION = original_version + "-bumped"
        k5 = ride_coach._cache_key(5, 11, 77, _ACTIVITY, _ROWS)
        assert k1 != k5
    finally:
        ride_coach._PROMPT_VERSION = original_version


# --------------------------------------------------------------------------
# Rider notes (TA-213): per-segment + overall notes feed the coach and bust
# only that ride's cache
# --------------------------------------------------------------------------
_SEGMENT_NOTES = {'Pescadero Control': 'legs cramped badly, way too long a taco stop'}
_OVERALL_NOTE = 'undertrained for the distance, felt it after 180k'
_STOP_NOTES = [{'label': 'Unplanned stop @ 148.0mi', 'note': 'flat tire, 20 min fix'}]


def test_notes_appear_in_user_message(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    client = _mock_client(_VALID_JSON)
    with patch('services.ride_coach._get_client', return_value=client):
        _call(segment_notes=_SEGMENT_NOTES, overall_note=_OVERALL_NOTE, stop_notes=_STOP_NOTES)

    messages = client.chat.completions.create.call_args[1]['messages']
    user_msg = next(m for m in messages if m['role'] == 'user')['content']
    system_msg = next(m for m in messages if m['role'] == 'system')['content']

    # Notes are present as delimited DATA blocks in the USER message only.
    assert '<segment_notes>' in user_msg and '<overall_note>' in user_msg
    assert '<stop_notes>' in user_msg
    assert 'legs cramped badly' in user_msg
    assert 'undertrained for the distance' in user_msg
    assert 'flat tire, 20 min fix' in user_msg
    # They must NOT leak into the system (instructions) message (injection guard).
    assert 'legs cramped badly' not in system_msg
    assert 'flat tire, 20 min fix' not in system_msg


def test_no_notes_omits_note_blocks(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    client = _mock_client(_VALID_JSON)
    with patch('services.ride_coach._get_client', return_value=client):
        _call()  # no notes

    messages = client.chat.completions.create.call_args[1]['messages']
    user_msg = next(m for m in messages if m['role'] == 'user')['content']
    assert '<segment_notes>' not in user_msg and '<overall_note>' not in user_msg
    assert '<stop_notes>' not in user_msg


def test_build_user_message_includes_notes_directly():
    msg = ride_coach._build_user_message(
        _ACTIVITY, _ROWS, _SUMMARY, _HR_POWER, _STOP_WIND,
        _RIDE_BASELINE, _BAND_BASELINE, _SEGMENT_NARRATIVES,
        segment_notes=_SEGMENT_NOTES, overall_note=_OVERALL_NOTE, stop_notes=_STOP_NOTES)
    assert '<segment_notes>' in msg and 'Pescadero Control' in msg and 'taco stop' in msg
    assert '<overall_note>' in msg and 'undertrained' in msg
    assert '<stop_notes>' in msg and 'Unplanned stop @ 148.0mi' in msg and 'flat tire' in msg


def test_build_user_message_includes_only_compact_drivetrain_aggregates():
    metrics = {
        "shifts_per_hour": 12.5,
        "dominant_rear_position": 4,
        "rear_position_changed_late": True,
    }
    msg = ride_coach._build_user_message(
        _ACTIVITY, _ROWS, _SUMMARY, _HR_POWER, _STOP_WIND,
        _RIDE_BASELINE, _BAND_BASELINE, _SEGMENT_NARRATIVES,
        drivetrain_metrics=metrics)

    assert "<drivetrain>" in msg
    assert '"shifts_per_hour": 12.5' in msg
    assert "raw_ciphertext" not in msg
    assert "rear_gears" not in msg


def test_notes_bust_cache_key_for_that_ride():
    base = ride_coach._cache_key(5, 11, 77, _ACTIVITY, _ROWS)

    with_seg = ride_coach._cache_key(
        5, 11, 77, _ACTIVITY, _ROWS, segment_notes=_SEGMENT_NOTES)
    assert base != with_seg

    with_overall = ride_coach._cache_key(
        5, 11, 77, _ACTIVITY, _ROWS, overall_note=_OVERALL_NOTE)
    assert base != with_overall and with_seg != with_overall

    with_stop = ride_coach._cache_key(
        5, 11, 77, _ACTIVITY, _ROWS, stop_notes=_STOP_NOTES)
    assert base != with_stop and with_seg != with_stop

    # Editing a note changes the key again.
    edited = {'Pescadero Control': 'felt great, quick stop'}
    assert with_seg != ride_coach._cache_key(
        5, 11, 77, _ACTIVITY, _ROWS, segment_notes=edited)

    # Whitespace-only / empty notes are treated as no notes.
    assert base == ride_coach._cache_key(
        5, 11, 77, _ACTIVITY, _ROWS,
        segment_notes={'Pescadero Control': '   '}, overall_note='  ',
        stop_notes=[{'label': 'x', 'note': '  '}])


def test_note_change_forces_new_api_call(monkeypatch):
    """A saved note must refresh coaching, not serve the pre-note cached text."""
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    client = _mock_client(_VALID_JSON)
    with patch('services.ride_coach._get_client', return_value=client):
        _call()                                     # cache miss #1
        _call(overall_note=_OVERALL_NOTE)           # different key → miss #2
    assert client.chat.completions.create.call_count == 2


# ── PR3: notes as one equal signal + new signals + same-route cache key ──

def test_notes_framed_as_one_equal_signal_not_heavy():
    """#3: the prompt must weight notes EQUALLY, not 'heavily'."""
    prompt = ride_coach.SYSTEM_PROMPT
    assert 'WEIGHT THEM HEAVILY' not in prompt
    assert 'ONE signal' in prompt and 'EQUALLY' in prompt


def test_prompt_version_bumped_for_new_signals():
    """#3: bumping _PROMPT_VERSION invalidates stale cached coaching on deploy."""
    assert ride_coach._PROMPT_VERSION == 'v7-randonneuring'


# ── v7-randonneuring: evidence-based coaching content in the SYSTEM prompt ──
# Keyword-assertion pattern mirrors tests/test_system_prompt.py. These prove the
# prompt *instructs* the model on each topic; the live coaching text is
# non-deterministic and out of scope for unit tests.

def test_prompt_covers_carbohydrate_fueling():
    """Fueling guidance: carbohydrate g/hr range, start-early framing."""
    prompt = ride_coach.SYSTEM_PROMPT.lower()
    assert 'carbohydrate' in prompt
    assert '60-90' in prompt and 'g' in prompt  # 60-90 g/hr
    assert 'first hour' in prompt               # start fueling early, don't wait


def test_prompt_covers_hydration_and_electrolytes():
    """Hydration guidance: ml/hr range plus electrolytes."""
    prompt = ride_coach.SYSTEM_PROMPT.lower()
    assert '500-750' in prompt and 'ml' in prompt   # 500-750 ml/hr
    assert 'hydration' in prompt
    assert 'electrolyte' in prompt


def test_prompt_covers_pacing_negative_split():
    """Pacing guidance: even / negative-split."""
    prompt = ride_coach.SYSTEM_PROMPT.lower()
    assert 'negative' in prompt and 'split' in prompt
    assert 'even' in prompt


def test_prompt_covers_rest_stop_discipline():
    """Rest-stop discipline: minimize dead time at controls."""
    prompt = ride_coach.SYSTEM_PROMPT.lower()
    assert 'control' in prompt
    assert 'stop' in prompt
    assert 'dead time' in prompt or 'minimize' in prompt


def test_prompt_covers_sleep_strategy_for_600k():
    """Sleep strategy is scoped to 600k+ / overnight efforts."""
    prompt = ride_coach.SYSTEM_PROMPT.lower()
    assert 'sleep' in prompt
    assert '600' in prompt


def test_prompt_covers_heat_and_cold_management():
    """Both heat and cold management are addressed."""
    prompt = ride_coach.SYSTEM_PROMPT.lower()
    assert 'heat' in prompt
    assert 'cold' in prompt


def test_prompt_covers_caffeine_timing():
    """Caffeine timing guidance is present."""
    prompt = ride_coach.SYSTEM_PROMPT.lower()
    assert 'caffeine' in prompt


def test_prompt_has_rest_and_refuel_coverage_mandate():
    """Explicit mandate to cover BOTH rest discipline and refueling/hydration."""
    prompt = ride_coach.SYSTEM_PROMPT
    assert 'COVERAGE MANDATE' in prompt
    lower = prompt.lower()
    assert 'rest' in lower and 'refuel' in lower


def test_prompt_has_no_fabrication_guard():
    """Explicit guard: reason only from the ride's real data, never invent numbers."""
    prompt = ride_coach.SYSTEM_PROMPT
    assert 'NO-FABRICATION GUARD' in prompt
    lower = prompt.lower()
    assert 'never invent' in lower or 'do not' in lower
    # Reference ranges are framed as comparison targets, not ride measurements.
    assert 'comparison target' in lower


def test_prompt_ties_guidance_to_this_rides_data():
    """The mandate ties fueling/rest reasoning to the ride's own break/weather data."""
    lower = ride_coach.SYSTEM_PROMPT.lower()
    assert 'this ride' in lower
    # References the actual per-segment break fields carried in <segments>.
    assert 'stop_here_min' in lower or 'enroute_break_min' in lower


def test_new_signals_appear_in_user_message():
    """#5/#6/#7: ft/mile climb, peak gust, and temp range reach the model."""
    rows = [dict(_ROWS[1], actual_climb_ft_per_mi=57)]
    stop_wind = {'Pescadero Control': {
        'wind_speed_mph': 8, 'wind_type': 'headwind',
        'wind_gust_peak_mph': 21, 'temp_min_f': 52, 'temp_max_f': 74,
    }}
    msg = ride_coach._build_user_message(
        _ACTIVITY, rows, _SUMMARY, _HR_POWER, stop_wind,
        _RIDE_BASELINE, _BAND_BASELINE, _SEGMENT_NARRATIVES)
    assert 'climb_ft_per_mi=57' in msg   # ft/mile climbing rate
    assert 'gusts 21mph' in msg          # peak gust (>= sustained + 8)
    assert '52-74F' in msg               # temperature range across the leg


def test_gust_hidden_when_not_notably_above_sustained():
    """A gust barely above sustained is not surfaced (mirrors the display rule)."""
    stop_wind = {'Pescadero Control': {
        'wind_speed_mph': 15, 'wind_type': 'headwind', 'wind_gust_peak_mph': 18,
    }}
    msg = ride_coach._build_user_message(
        _ACTIVITY, [_ROWS[1]], _SUMMARY, _HR_POWER, stop_wind,
        _RIDE_BASELINE, _BAND_BASELINE, _SEGMENT_NARRATIVES)
    assert 'gusts' not in msg            # 18 < 15 + 8


def test_same_route_baseline_folded_into_cache_key():
    """#4: a change in same-route history busts the cache (self-invalidation)."""
    base = ride_coach._cache_key(5, 11, 77, _ACTIVITY, _ROWS)
    sr_a = {'Pescadero Control': {'avg_segment_min': 88.0, 'n_rides': 1}}
    sr_b = {'Pescadero Control': {'avg_segment_min': 91.0, 'n_rides': 2}}
    k_a = ride_coach._cache_key(5, 11, 77, _ACTIVITY, _ROWS, same_route_baseline=sr_a)
    k_b = ride_coach._cache_key(5, 11, 77, _ACTIVITY, _ROWS, same_route_baseline=sr_b)
    assert base != k_a != k_b and k_a != k_b
    # Empty baseline is identical to the no-baseline case.
    assert base == ride_coach._cache_key(5, 11, 77, _ACTIVITY, _ROWS,
                                         same_route_baseline={})
