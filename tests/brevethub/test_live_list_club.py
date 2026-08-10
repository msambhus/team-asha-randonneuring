"""Club-branded /live list shows only today's rides."""
import os
from unittest.mock import patch

import pytest

os.environ.setdefault('GOOGLE_CLIENT_ID', 'test-client-id')
os.environ.setdefault('GOOGLE_CLIENT_SECRET', 'test-client-secret')
os.environ.setdefault('DATABASE_URL', 'postgresql://test:test@localhost/test')
os.environ.setdefault('BREVETHUB_SECRET_KEY', 'test-secret-key-that-is-long-enough')


@pytest.fixture
def app():
    from brevethub.app import create_app
    application = create_app()
    application.config['TESTING'] = True
    application.config['HOST_CLUB_ID'] = 3
    application.config['HOST_REGION_PREFIX'] = 'CA: San Francisco'
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def test_live_list_uses_today_filter_for_host_club(client):
    host = {'id': 3, 'name': 'SFR', 'region_prefix': 'CA: San Francisco',
            'filter_state': 'CA', 'filter_area': 'San Francisco', 'abbrev': 'SFR',
            'hero_headline': '', 'hero_body': '', 'new_rider_guide_url': '', 'about_url': ''}
    with patch('brevethub.models.get_club', return_value={'id': 3, 'name': 'SFR', 'city': 'SF', 'state': 'CA'}), \
         patch('brevethub.routes.live.host_club_from_config', return_value=host), \
         patch('brevethub.services.club_site.host_club_from_config', return_value=host), \
         patch('brevethub.models.get_public_rides_today', return_value=[]) as today_fn, \
         patch('brevethub.models.get_public_rides', return_value=[{'id': 99}]) as all_fn:
        resp = client.get('/live')
    assert resp.status_code == 200
    today_fn.assert_called_once_with(club_id=3, region_prefix='CA: San Francisco')
    all_fn.assert_not_called()
    body = resp.get_data(as_text=True)
    assert 'Live today' in body
    assert 'No rides today.' in body
