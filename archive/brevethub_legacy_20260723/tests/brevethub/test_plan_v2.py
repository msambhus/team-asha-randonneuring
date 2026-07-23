"""Render tests for the rpv2 3-tab plan view (Plan / Strategies / Weather).

Follows the BrevetHub test pattern: monkeypatch `brevethub.models.*`, use the `client`
fixture, never touch a real DB or network. A stored real plan now renders the rich
3-tab layout driven by the promoted shared functions. First-class contracts proven
here as full render-path assertions (so a missing filter or bad macro would surface as
a 500, not a silent pass):

  * the Plan tab renders the 11-column itinerary, the journey SVG, the info trio, a
    risks callout, and the snapshot/share card with a de-branded `product_name · /plan/<id>`
    footer (never "Team Asha"),
  * the Strategies tab renders three Comfort/Standard/Push cards with a per-card save
    button (data-pace-pick / "Choose this plan") for a signed-in rider and a sign-in
    prompt for a guest; the saved card flips to "✓ Saved"; once a pace is saved a
    share-to-community toggle appears; and other riders' publicly-shared, club-scoped
    strategies render in a PII-safe (local-part only) "Community plans" block,
  * the save/share route (POST /plan/<id>/strategy) enforces the save_plan auth ladder
    (401+login_url guest, 400 bad pace, 404 unknown event) and echoes the is_public the
    upsert actually persisted,
  * the Weather tab renders the lean per-stop forecast list from the cached route
    weather via compute_stop_winds (the fallback when no Mapbox token is set), NO Mapbox,
  * a guest sees rider local-parts only — no full email, no google_id,
  * all three tabs return 200 (no missing-filter 500).
"""
from unittest.mock import patch

import pytest

from brevethub import models


_EVENT = {
    'id': 11, 'rusa_route_id': '1234', 'name': 'Cascade Lakes 200',
    'date': '2026-08-15', 'distance_km': 200, 'region': 'OR: Bend',
    'ride_type': 'ACP brevet', 'elevation_ft': 3280, 'rwgps_url': None,
    'start_location': None, 'start_time': '06:00', 'time_limit_hours': 13.5,
}

# A stored real plan (native miles / mph / feet) with a control, a meal break, and a
# finish — the same shape rp_brevet_route_plan[_stop] returns.
_PLAN = {
    'id': 5, 'event_id': 11, 'variant': 'conservative', 'name': 'Cascade Lakes 200',
    'slug': 'cascade-lakes-200', 'total_distance_miles': 124.3, 'total_elevation_ft': 3280,
    'rwgps_url': 'https://ridewithgps.com/routes/1', 'rwgps_route_id': '1',
    'distance_km': 200, 'cutoff_hours': 13.5, 'start_time': '06:00',
    'avg_moving_speed': 12.0, 'avg_elapsed_speed': 11.5,
    'total_moving_time_min': 534, 'total_elapsed_time_min': 564,
    'total_break_time_min': 30, 'overall_ft_per_mile': 26,
}
_STOPS = [
    {'stop_order': 1, 'location': 'Downtown Start', 'stop_type': 'start',
     'distance_miles': 0.0, 'seg_dist': 0.0, 'elevation_gain': 0, 'ft_per_mi': None,
     'avg_speed': None, 'segment_time_min': 0, 'cum_time_min': 0, 'time_bank_min': None,
     'difficulty_score': 0.0, 'notes': None},
    {'stop_order': 2, 'location': 'Midway Control', 'stop_type': 'control',
     'distance_miles': 62.1, 'seg_dist': 62.1, 'elevation_gain': 1600, 'ft_per_mi': 26,
     'avg_speed': 12.0, 'segment_time_min': 266, 'cum_time_min': 266, 'time_bank_min': 120,
     'difficulty_score': 2.6, 'notes': None},
    {'stop_order': 3, 'location': 'Lunch Stop', 'stop_type': 'meal',
     'distance_miles': 62.1, 'seg_dist': 0.0, 'elevation_gain': 0, 'ft_per_mi': None,
     'avg_speed': None, 'segment_time_min': 30, 'cum_time_min': 296, 'time_bank_min': None,
     'difficulty_score': 0.0, 'notes': 'Lunch — sit-down refuel'},
    {'stop_order': 4, 'location': 'Downtown Finish', 'stop_type': 'finish',
     'distance_miles': 124.3, 'seg_dist': 62.2, 'elevation_gain': 1680, 'ft_per_mi': 27,
     'avg_speed': 11.9, 'segment_time_min': 268, 'cum_time_min': 564, 'time_bank_min': 150,
     'difficulty_score': 2.7, 'notes': None},
]
_BUNDLE = {'plan': _PLAN, 'stops': _STOPS}

# A PII-free roster (email local-part only) as get_event_going_riders returns.
_ROSTER = [{'name': 'alice', 'status': 'going'},
           {'name': 'bob', 'status': 'interested'}]


def _cached_weather():
    """A minimal cached rp_brevet_route_weather row that compute_stop_winds can resolve
    to per-stop wind (same keys the cron warms): a per-sample Open-Meteo hourly forecast
    plus the aligned sample points."""
    times = [f"2026-08-15T{h:02d}:00" for h in range(24)]

    def sample(ws, wd, tc):
        return {'hourly': {'time': times, 'wind_speed_10m': [ws] * 24,
                           'wind_direction_10m': [wd] * 24, 'temperature_2m': [tc] * 24}}

    return {
        'event_id': 11, 'forecast_date': '2026-08-15',
        'weather_data': [sample(20, 270, 28), sample(25, 90, 30), sample(10, 180, 24)],
        'sample_points': [{'lat': 44.0, 'lng': -121.0, 'distance_m': 0},
                          {'lat': 44.1, 'lng': -121.2, 'distance_m': 100000},
                          {'lat': 44.2, 'lng': -121.4, 'distance_m': 200000}],
    }


def _get(client, url, *, weather=None):
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT), \
         patch('brevethub.models.get_brevet_route_plan_with_stops', return_value=_BUNDLE), \
         patch('brevethub.models.get_brevet_route_weather', return_value=weather), \
         patch('brevethub.models.get_event_going_riders', return_value=_ROSTER):
        return client.get(url)


# A signed-in rider (email carries a full address; only the local-part may ever render).
# club_id scopes the community read.
_RIDER = {'id': 7, 'email': 'dave@example.com', 'google_id': 'g-dave',
          'club_id': 3, 'profile_completed': True}


def _get_as_rider(client, url, *, rider=None, saved=None, community=None, weather=None):
    """GET a plan URL as a signed-in rider: seed the session, resolve current_rider via
    a mocked get_rider_by_id, and mock the saved-plan + community reads. All mocks accept
    **kwargs so a signature tweak never silently breaks the fake."""
    rider = rider if rider is not None else _RIDER

    def _rider_by_id(*a, **k):
        return rider

    def _rider_plan(*a, **k):
        return saved

    def _public(*a, **k):
        return community or []

    with client.session_transaction() as sess:
        sess['rider_id'] = rider['id']
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT), \
         patch('brevethub.models.get_brevet_route_plan_with_stops', return_value=_BUNDLE), \
         patch('brevethub.models.get_brevet_route_weather', return_value=weather), \
         patch('brevethub.models.get_event_going_riders', return_value=_ROSTER), \
         patch('brevethub.models.get_rider_by_id', side_effect=_rider_by_id), \
         patch('brevethub.models.get_rider_brevet_plan', side_effect=_rider_plan), \
         patch('brevethub.models.get_public_strategies', side_effect=_public):
        return client.get(url)


# --------------------------------------------------------------------------- #
# Plan tab
# --------------------------------------------------------------------------- #
def test_plan_tab_renders_itinerary_and_snapshot(client):
    resp = _get(client, '/plan/11', weather=_cached_weather())
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # 2-tab shell (Strategies moved inline into the Plan tab).
    assert 'data-tab="plan"' in body and 'data-tab="weather"' in body
    assert 'data-tab="strategies"' not in body
    # 11-column itinerary headers.
    for head in ('>Stop<', '>Seg<', '>Cumul<', '>Climb<', '>Pace<', '>Elapsed<',
                 '>ETA<', '>Bank<', '>Wind<'):
        assert head in body, head
    assert 'rpv2-itinerary' in body
    # Real control name + journey chart + info trio.
    assert 'Midway Control' in body
    assert 'rpv2-journey-svg' in body
    assert 'Fuel + breaks' in body and 'Riders' in body
    # Snapshot/share card, de-branded footer.
    assert 'rpv2-snap' in body
    assert 'BrevetHub · /plan/11' in body
    assert 'team asha' not in body.lower()


def test_plan_tab_wind_and_toughness_from_forecast(client):
    resp = _get(client, '/plan/11', weather=_cached_weather())
    body = resp.get_data(as_text=True)
    # A per-stop wind arrow renders (forecast resolved through compute_stop_winds).
    assert 'rpv2-wind-arrow' in body
    # A toughness score chip renders on a moving segment.
    assert 'rpv2-tough' in body


def test_plan_tab_survives_no_forecast(client):
    """A wind-cache miss must still render the itinerary (wind column blank), never 500."""
    resp = _get(client, '/plan/11', weather=None)
    assert resp.status_code == 200
    assert 'Midway Control' in resp.get_data(as_text=True)


# --------------------------------------------------------------------------- #
# Strategies tab
# --------------------------------------------------------------------------- #
def test_strategies_tab_cards_for_rider_and_guest(client):
    """Signed-in rider (no saved pace yet) sees a per-card save button; a guest sees a
    sign-in prompt instead and no Community block. The pace cards now live inline on the
    Plan tab (the separate Strategies panel is gone); ?tab=strategies resolves to Plan."""
    # The inline "Choose your pace" card + three pace cards render in both states.
    guest_body = _get(client, '/plan/11?tab=strategies', weather=None).get_data(as_text=True)
    assert 'id="rpv2-panel-strategies"' not in guest_body
    assert 'id="rpv2-choose-pace"' in guest_body
    assert 'rpv2-pc-grid' in guest_body
    for name in ('>Comfort<', '>Standard<', '>Push<'):
        assert name in guest_body, name

    # Signed-in rider with no saved pace: per-card save button present. (Key off the
    # rendered attribute form `data-pace-pick="…"`, since the bare `[data-pace-pick]`
    # selector also appears in the progressive-enhancement JS on every real plan.)
    rider_resp = _get_as_rider(client, '/plan/11?tab=strategies', saved=None)
    assert rider_resp.status_code == 200
    rider_body = rider_resp.get_data(as_text=True)
    assert 'data-pace-pick="' in rider_body
    assert 'Choose this plan' in rider_body
    assert 'Sign in to save' not in rider_body

    # Guest: NO save button — a sign-in prompt instead — and no Community block.
    assert 'data-pace-pick="' not in guest_body
    assert 'Choose this plan' not in guest_body
    assert 'Sign in to save' in guest_body
    assert 'Community plans' not in guest_body


# --------------------------------------------------------------------------- #
# Save / share route — the auth ladder + the resolved-flag echo
# --------------------------------------------------------------------------- #
def _post_strategy(client, event_id, payload, *, rider=_RIDER, upsert_returns=False,
                   event=_EVENT):
    """POST the save/share route with the model layer mocked. Returns (resp, upsert_mock).
    `rider=None` posts as a guest; `event=None` simulates an unknown event."""
    with patch('brevethub.models.get_rider_by_id', return_value=rider), \
         patch('brevethub.models.get_brevet_event_full', return_value=event), \
         patch('brevethub.models.upsert_rider_brevet_strategy',
               return_value=upsert_returns) as upsert:
        if rider is not None:
            with client.session_transaction() as sess:
                sess['rider_id'] = rider['id']
        resp = client.post(f'/plan/{event_id}/strategy', json=payload)
    return resp, upsert


def test_save_strategy_guest_401_with_login_url(client):
    resp, upsert = _post_strategy(client, 11, {'pace_id': 'standard'}, rider=None)
    assert resp.status_code == 401
    data = resp.get_json()
    assert 'login_url' in data and data['login_url']
    upsert.assert_not_called()


def test_save_strategy_bad_pace_400(client):
    resp, upsert = _post_strategy(client, 11, {'pace_id': 'sprint'})
    assert resp.status_code == 400
    upsert.assert_not_called()


def test_save_strategy_unknown_event_404(client):
    resp, upsert = _post_strategy(client, 999, {'pace_id': 'standard'}, event=None)
    assert resp.status_code == 404
    upsert.assert_not_called()


def test_save_strategy_valid_preserves_flag_new_private_rider(client):
    """A flagless save (pace only) calls upsert with is_public=None (preserve) and echoes
    the resolved flag the upsert returns — here False for a new/private rider."""
    resp, upsert = _post_strategy(client, 11, {'pace_id': 'standard'},
                                  upsert_returns=False)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data == {'ok': True, 'event_id': 11, 'pace_id': 'standard', 'is_public': False}
    upsert.assert_called_once_with(7, 11, 'standard', is_public=None)


def test_save_strategy_flagless_repick_preserves_true(client):
    """The corrected assertion: a flagless re-pick by an already-sharing rider echoes the
    upsert's returned is_public=True (not a hardcoded false), so the toggle stays in sync."""
    resp, upsert = _post_strategy(client, 11, {'pace_id': 'push'},
                                  upsert_returns=True)
    assert resp.status_code == 200
    assert resp.get_json()['is_public'] is True
    upsert.assert_called_once_with(7, 11, 'push', is_public=None)


def test_save_strategy_publish_and_unpublish(client):
    """Explicit is_public true/false flows to upsert and is echoed from its return."""
    resp, upsert = _post_strategy(client, 11, {'pace_id': 'standard', 'is_public': True},
                                  upsert_returns=True)
    assert resp.status_code == 200 and resp.get_json()['is_public'] is True
    upsert.assert_called_once_with(7, 11, 'standard', is_public=True)

    resp2, upsert2 = _post_strategy(client, 11, {'pace_id': 'standard', 'is_public': False},
                                    upsert_returns=False)
    assert resp2.status_code == 200 and resp2.get_json()['is_public'] is False
    upsert2.assert_called_once_with(7, 11, 'standard', is_public=False)


def test_upsert_strategy_returns_persisted_is_public(client):
    """Model unit: upsert_rider_brevet_strategy returns the RETURNING is_public value the
    DB reports (the resolved flag), proving it flows back to the route echo."""
    with patch('brevethub.db.execute', return_value={'is_public': True}) as ex:
        got = models.upsert_rider_brevet_strategy(7, 11, 'standard', is_public=None)
    assert got is True
    # It runs one upsert with RETURNING is_public, binding pace + tri-state flag twice.
    sql, params = ex.call_args[0][0], ex.call_args[0][1]
    assert 'RETURNING is_public' in sql
    assert params == (7, 11, 'standard', None, None)
    assert ex.call_args[1].get('returning') is True


# --------------------------------------------------------------------------- #
# Saved-state + share-toggle render
# --------------------------------------------------------------------------- #
def test_saved_state_renders_saved_on_one_card_only(client):
    """strategy_pace='standard' flips ONLY the Standard card to '✓ Saved'; the others show
    an enabled 'Switch to this plan' button."""
    saved = {'strategy_pace': 'standard', 'is_public': False}
    body = _get_as_rider(client, '/plan/11?tab=strategies', saved=saved).get_data(as_text=True)
    # Exactly one card is the saved card ('✓ Saved' also appears in the snapshot-share JS,
    # so key off the save-button class, which is unique to the picked card).
    assert body.count('rpv2-pc-btn-saved') == 1
    assert '✓ Saved' in body
    assert 'Switch to this plan' in body
    # A picked pace means no card offers the first-time "Choose this plan" label.
    assert 'Choose this plan' not in body


def test_share_toggle_render_states(client):
    """The share toggle appears once a pace is saved and reflects the stored is_public via
    aria-pressed. It is absent with no saved pace and for a guest. (Key off the
    `rpv2-public-toggle` class + `aria-pressed="…"` attribute — the toggle glyph text and
    the `[data-share-toggle]` selector also live in the always-present enhancement JS.)"""
    unshared = {'strategy_pace': 'standard', 'is_public': False}
    body = _get_as_rider(client, '/plan/11?tab=strategies', saved=unshared).get_data(as_text=True)
    assert 'rpv2-public-toggle' in body
    assert 'aria-pressed="false"' in body
    assert 'aria-pressed="true"' not in body

    shared = {'strategy_pace': 'standard', 'is_public': True}
    body2 = _get_as_rider(client, '/plan/11?tab=strategies', saved=shared).get_data(as_text=True)
    assert 'rpv2-public-toggle' in body2
    assert 'aria-pressed="true"' in body2

    # No saved pace -> no toggle.
    body3 = _get_as_rider(client, '/plan/11?tab=strategies', saved=None).get_data(as_text=True)
    assert 'rpv2-public-toggle' not in body3
    # Guest -> no toggle. Clear the signed-in session first — the prior _get_as_rider
    # calls left rider_id in it, so without this the "guest" render resolves a rider and
    # hits the unmocked get_rider_by_id (a real DB call).
    with client.session_transaction() as sess:
        sess.pop('rider_id', None)
    guest_body = _get(client, '/plan/11?tab=strategies', weather=None).get_data(as_text=True)
    assert 'rpv2-public-toggle' not in guest_body


# --------------------------------------------------------------------------- #
# Community render — PII-safe + club-scoped
# --------------------------------------------------------------------------- #
def test_community_render_local_part_only(client):
    """A shared strategy renders the local-part name + the pace + the pace total from the
    computed cards — never a full email or a google_id."""
    community = [{'name': 'carol', 'strategy_pace': 'comfort'}]
    body = _get_as_rider(client, '/plan/11?tab=strategies',
                         saved={'strategy_pace': 'standard', 'is_public': True},
                         community=community).get_data(as_text=True)
    assert 'Community plans' in body
    assert 'carol' in body and 'Comfort' in body
    # PII-safe: no full email, no google_id leak.
    assert 'carol@' not in body
    assert 'google_id' not in body


def test_community_absent_for_guest_null_club(client):
    """A guest (NULL viewer club) asks get_public_strategies with club_id=None and gets [],
    so no Community block renders."""
    captured = {}

    def _public(event_id, club_id, *a, **k):
        captured['club_id'] = club_id
        return []

    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT), \
         patch('brevethub.models.get_brevet_route_plan_with_stops', return_value=_BUNDLE), \
         patch('brevethub.models.get_brevet_route_weather', return_value=None), \
         patch('brevethub.models.get_event_going_riders', return_value=_ROSTER), \
         patch('brevethub.models.get_public_strategies', side_effect=_public):
        resp = client.get('/plan/11?tab=strategies')
    assert resp.status_code == 200
    assert captured['club_id'] is None
    assert 'Community plans' not in resp.get_data(as_text=True)


def test_strategies_cards_carry_wind_when_forecast_cached(client):
    """The strategy cards must surface the SAME per-stop wind/toughness the Plan tab
    does — i.e. the route must pass seg_meta into compute_pace_strategies. Without it
    the cards silently blank (wind_known=False), a parity regression: guard it."""
    resp = _get(client, '/plan/11?tab=strategies', weather=_cached_weather())
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # A per-stop wind arrow inside a strategy card (only rendered when seg_meta carries
    # the resolved forecast wind through to the cards).
    assert 'rpv2-pc-wind-arrow' in body


# --------------------------------------------------------------------------- #
# Weather tab
# --------------------------------------------------------------------------- #
def test_weather_tab_per_stop_forecast(client):
    resp = _get(client, '/plan/11?tab=weather', weather=_cached_weather())
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="rpv2-panel-weather"' in body
    # No Mapbox token in the test env → the lean per-stop fallback list renders.
    assert 'Midway Control' in body
    assert '°F' in body                 # per-stop temperature rendered
    # The full map only mounts when a token + warm cache both exist (covered in
    # test_plan_weather_mapbox); with no token there is no Mapbox on the page.
    assert 'mapbox' not in body.lower()


def test_weather_tab_note_when_no_forecast(client):
    resp = _get(client, '/plan/11?tab=weather', weather=None)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'No forecast cached' in body
    assert 'mapbox' not in body.lower()


# --------------------------------------------------------------------------- #
# Guest safety — no rider PII
# --------------------------------------------------------------------------- #
def test_guest_roster_exposes_local_part_only(client):
    resp = _get(client, '/plan/11', weather=None)
    body = resp.get_data(as_text=True)
    # Local-parts show…
    assert 'alice' in body and 'bob' in body
    # …but never a full email or a google_id, and no '@' from a rider address.
    assert 'alice@' not in body and 'bob@' not in body
    assert 'google_id' not in body


@pytest.mark.parametrize('url', ['/plan/11', '/plan/11?tab=strategies', '/plan/11?tab=weather'])
def test_all_three_tabs_render_200(client, url):
    assert _get(client, url, weather=_cached_weather()).status_code == 200
