"""Public rider profile URLs use the RUSA member id, not the internal database id."""
import contextlib
import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

os.environ.setdefault('GOOGLE_CLIENT_ID', 'test-client-id')
os.environ.setdefault('GOOGLE_CLIENT_SECRET', 'test-client-secret')
os.environ.setdefault('DATABASE_URL', 'postgresql://test:test@localhost/test')
os.environ.setdefault('BREVETHUB_SECRET_KEY', 'test-secret-key-that-is-long-enough')

_UTC = timezone.utc
_MADE = datetime(2024, 3, 1, tzinfo=_UTC)
_CACHE = [
    {'date': '2025-12-06', 'distance_km': 200, 'finish_time': '09:43',
     'route_name': 'Estero', 'ride_kind': 'brevet'},
]

_ALICE = {
    'id': 2, 'email': 'alice@ex.com', 'rusa_id': '14832',
    'club_id': 3, 'profile_completed': True, 'created_at': _MADE,
    'rusa_cache': _CACHE,
}


def _fake_get_rider_by_id(rider_id, **kwargs):
    return _ALICE if rider_id == _ALICE['id'] else None


def _fake_get_rider_by_rusa_id(rusa_id, **kwargs):
    return _ALICE if str(rusa_id) == _ALICE['rusa_id'] else None


def _fake_club_rider(club_id, rider_id, **kwargs):
    if rider_id == _ALICE['id'] and club_id == _ALICE['club_id']:
        return _ALICE
    return None


def _fake_public_rider(rider_id, **kwargs):
    return _ALICE if rider_id == _ALICE['id'] else None


@pytest.fixture
def app():
    from brevethub.app import create_app
    application = create_app()
    application.config['TESTING'] = True
    application.config['SECRET_KEY'] = 'test-secret-key-that-is-long-enough'
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@contextlib.contextmanager
def _mocked():
    with patch('brevethub.models.get_rider_by_id', side_effect=_fake_get_rider_by_id), \
         patch('brevethub.models.get_rider_by_rusa_id', side_effect=_fake_get_rider_by_rusa_id), \
         patch('brevethub.models.get_club_rider', side_effect=_fake_club_rider), \
         patch('brevethub.models.get_public_rider', side_effect=_fake_public_rider), \
         patch('brevethub.models.get_rider_rusa_cache',
               return_value={'rusa_cache': _CACHE}), \
         patch('brevethub.models.get_strava_connection', return_value=None):
        yield


def test_rider_profile_by_rusa_id(client):
    with _mocked():
        resp = client.get('/riders/14832')
    assert resp.status_code == 200
    assert 'alice' in resp.get_data(as_text=True)
    assert 'RUSA# 14832' in resp.get_data(as_text=True)


def test_legacy_database_id_url_redirects_to_rusa_id(client):
    """Old links keyed on internal database id redirect to the RUSA member id."""
    with _mocked():
        resp = client.get('/riders/2', follow_redirects=False)
    assert resp.status_code in (301, 302)
    assert resp.headers['Location'].endswith('/riders/14832')
