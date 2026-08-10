"""Club-branded home page for per-deployment BrevetHub instances."""
import os
from datetime import date, timedelta
from unittest.mock import patch

import pytest

os.environ.setdefault('GOOGLE_CLIENT_ID', 'test-client-id')
os.environ.setdefault('GOOGLE_CLIENT_SECRET', 'test-client-secret')
os.environ.setdefault('DATABASE_URL', 'postgresql://test:test@localhost/test')
os.environ.setdefault('BREVETHUB_SECRET_KEY', 'test-secret-key-that-is-long-enough')

_CLUB = {'id': 3, 'name': 'San Francisco Randonneurs', 'city': 'San Francisco', 'state': 'CA'}
_EVENT = {
    'id': 10,
    'name': 'Laguna Lake 200K',
    'date': date(2026, 8, 29),
    'distance_km': 200,
    'region': 'CA: San Francisco',
    'ride_type': 'ACP brevet',
    'start_location': 'Crissy Field',
    'start_time': '07:00',
    'time_limit_hours': 13.5,
    'fee_cents': 2500,
    'registration_enabled': True,
    'signup_count': 2,
    'registration_deadline': date(2026, 8, 22),
}


@pytest.fixture
def app():
    from brevethub.app import create_app
    application = create_app()
    application.config['TESTING'] = True
    application.config['SECRET_KEY'] = 'test-secret-key-that-is-long-enough'
    application.config['HOST_CLUB_ID'] = 3
    application.config['HOST_REGION_PREFIX'] = 'CA: San Francisco'
    application.config['HOST_CLUB_ABBREV'] = 'SFR'
    return application


_CLUB_WITH_FILTER = {
    **_CLUB,
    'abbrev': 'SFR',
    'region_prefix': 'CA: San Francisco',
    'filter_state': 'CA',
    'filter_area': 'San Francisco',
    'hero_headline': 'Long rides out of San Francisco, all year.',
    'hero_body': 'Test body',
    'new_rider_guide_url': '',
    'about_url': '',
}


@pytest.fixture
def client(app):
    return app.test_client()


def test_club_home_renders_branded_page(client):
    with patch('brevethub.models.get_club', return_value=_CLUB), \
         patch('brevethub.services.club_site.models.get_club', return_value=_CLUB), \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT]), \
         patch('brevethub.models.get_club_riders_with_rusa', return_value=[]), \
         patch('brevethub.services.club_site.host_club_from_config', return_value=_CLUB_WITH_FILTER):
        resp = client.get('/')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'San Francisco Randonneurs' in body
    assert 'Laguna Lake' in body
    assert 'See the schedule' in body
    assert 'Resources' in body
    assert 'Merge GPS Files' in body
    assert '/merge-fit' in body
    assert 'Riders' in body
    assert '/login' in body
    assert 'register%3D10' in body or 'register=10' in body
    assert 'state=CA' in body
    assert 'area=San+Francisco' in body or 'area=San%20Francisco' in body
    assert 'Riding right now' in body
    assert 'No ride today.' in body


def test_featured_shows_registration_countdown(client):
    deadline = date.today() + timedelta(days=12)
    event = {**_EVENT, 'registration_deadline': deadline}
    with patch('brevethub.models.get_club', return_value=_CLUB), \
         patch('brevethub.services.club_site.models.get_club', return_value=_CLUB), \
         patch('brevethub.models.get_upcoming_events', return_value=[event]), \
         patch('brevethub.models.get_club_riders_with_rusa', return_value=[]), \
         patch('brevethub.services.club_site.host_club_from_config', return_value=_CLUB_WITH_FILTER):
        resp = client.get('/')
    body = resp.get_data(as_text=True)
    assert 'data-deadline="' + deadline.isoformat() + '"' in body
    assert 'club-featured-countdown-num' in body
    assert 'Register by' in body or 'Closes' in body


def test_club_home_shows_today_event(client):
    today_event = {**_EVENT, 'date': date.today()}
    with patch('brevethub.models.get_club', return_value=_CLUB), \
         patch('brevethub.services.club_site.models.get_club', return_value=_CLUB), \
         patch('brevethub.models.get_upcoming_events', return_value=[today_event]), \
         patch('brevethub.models.get_club_riders_with_rusa', return_value=[]), \
         patch('brevethub.models.get_live_ride_ids_for_event', return_value=[99]), \
         patch('brevethub.services.club_site.host_club_from_config', return_value=_CLUB_WITH_FILTER):
        resp = client.get('/')
    body = resp.get_data(as_text=True)
    assert 'Riding right now' in body
    assert 'Laguna Lake' in body
    assert '/live/99' in body
    assert 'No ride today.' not in body


def test_generic_landing_without_host_club(client, app):
    app.config['HOST_CLUB_ID'] = None
    resp = client.get('/')
    assert resp.status_code == 200
    assert 'Sign in with Google' in resp.get_data(as_text=True)
