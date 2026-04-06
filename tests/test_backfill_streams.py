"""Tests for /api/cron/backfill-strava-streams endpoint."""
import pytest
from datetime import date
from unittest.mock import patch, MagicMock


class TestBackfillStravaStreams:
    def test_requires_auth(self, app, client):
        app.config['CRON_SECRET'] = 'test-secret'
        resp = client.post('/api/cron/backfill-strava-streams')
        assert resp.status_code == 401

    @patch('routes.cron._verify_cron_auth', return_value=None)
    @patch('models._execute')
    def test_empty_run_returns_summary(self, mock_execute, mock_auth, client):
        """When no unmatched rides or missing streams, returns empty summary."""
        mock_execute.return_value.fetchall.return_value = []

        resp = client.post('/api/cron/backfill-strava-streams')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'summary' in data
        assert data['summary']['rides_matched'] == 0
        assert data['summary']['streams_cached'] == 0

    @patch('routes.cron._verify_cron_auth', return_value=None)
    @patch('services.strava_analysis.fetch_and_analyze')
    @patch('services.strava_analysis.find_matching_activity')
    @patch('models.create_strava_ride_match')
    @patch('models._execute')
    def test_match_and_streams(self, mock_execute, mock_create_match,
                               mock_find_match, mock_fetch, mock_auth, client):
        """Default run does matching + stream fetch."""
        phase2_rows = [{'rider_id': 1, 'ride_id': 10, 'date': date(2025, 10, 15),
                        'distance_km': 200, 'ride_name': 'Test 200k', 'first_name': 'Test'}]
        phase3_rows = [{'match_id': 100, 'rider_id': 1, 'strava_activity_id': 999,
                        'ride_name': 'Test 200k', 'date': date(2025, 10, 15), 'first_name': 'Test'}]

        mock_execute.return_value.fetchall.side_effect = [phase2_rows, phase3_rows]
        mock_find_match.return_value = {'strava_activity_id': 999}
        mock_fetch.return_value = {'detected_stops': [], 'stream_summary': {}, 'error': None}

        resp = client.post('/api/cron/backfill-strava-streams')
        assert resp.status_code == 200
        data = resp.get_json()

        assert data['summary']['rides_matched'] == 1
        assert data['summary']['streams_cached'] == 1
        assert data['summary']['rate_limited'] is False

    @patch('routes.cron._verify_cron_auth', return_value=None)
    @patch('services.strava_analysis.fetch_and_analyze')
    @patch('models._execute')
    def test_stops_on_rate_limit(self, mock_execute, mock_fetch, mock_auth, client):
        """Stops processing when Strava rate limit is hit."""
        mock_execute.return_value.fetchall.side_effect = [
            [],  # matching phase
            [   # streams phase: two matches
                {'match_id': 1, 'rider_id': 1, 'strava_activity_id': 100,
                 'ride_name': 'Ride A', 'date': date(2025, 10, 1), 'first_name': 'A'},
                {'match_id': 2, 'rider_id': 2, 'strava_activity_id': 200,
                 'ride_name': 'Ride B', 'date': date(2025, 10, 2), 'first_name': 'B'},
            ],
        ]
        mock_fetch.return_value = {'error': 'Strava rate limit reached. Try again in 15 minutes.'}

        resp = client.post('/api/cron/backfill-strava-streams')
        data = resp.get_json()

        assert data['summary']['rate_limited'] is True
        assert len(data['details']['streams']) == 1

    @patch('routes.cron._verify_cron_auth', return_value=None)
    @patch('services.strava_analysis.find_matching_activity', return_value=None)
    @patch('models._execute')
    def test_match_phase_only(self, mock_execute, mock_find, mock_auth, client):
        """?phase=match only runs matching, no stream fetch."""
        mock_execute.return_value.fetchall.return_value = [
            {'rider_id': 1, 'ride_id': 10, 'date': date(2025, 10, 15),
             'distance_km': 200, 'ride_name': 'Mystery Ride', 'first_name': 'Test'},
        ]

        resp = client.post('/api/cron/backfill-strava-streams?phase=match')
        data = resp.get_json()

        assert data['phase'] == 'match'
        assert data['unmatched'] == 1
        assert 'streams' not in data.get('details', {})

    @patch('routes.cron._verify_cron_auth', return_value=None)
    @patch('services.strava_analysis.fetch_and_analyze')
    @patch('models._execute')
    def test_streams_phase_only(self, mock_execute, mock_fetch, mock_auth, client):
        """?phase=streams skips matching, only fetches streams."""
        mock_execute.return_value.fetchall.return_value = [
            {'match_id': 1, 'rider_id': 1, 'strava_activity_id': 100,
             'ride_name': 'Ride A', 'date': date(2025, 10, 1), 'first_name': 'A'},
        ]
        mock_fetch.return_value = {'error': None}

        resp = client.post('/api/cron/backfill-strava-streams?phase=streams')
        data = resp.get_json()

        assert data['summary']['streams_cached'] == 1
        assert 'matching' not in data.get('details', {})

    @patch('routes.cron._verify_cron_auth', return_value=None)
    def test_sync_phase_requires_rider_id(self, mock_auth, client):
        """?phase=sync without rider_id returns 400."""
        resp = client.post('/api/cron/backfill-strava-streams?phase=sync')
        assert resp.status_code == 400
        assert 'rider_id' in resp.get_json()['error']
