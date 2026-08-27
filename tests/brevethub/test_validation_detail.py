from brevethub.app import app
from brevethub.routes.admin import _validation_visualization


def test_validation_visualization_keeps_template_contract_without_route(monkeypatch):
    monkeypatch.setattr('brevethub.models.get_rp_route_elevation_track', lambda _route_id: [])

    visualization = _validation_visualization({
        'rwgps_url': 'https://ridewithgps.com/routes/1771223',
        'normalized_track': [[38.4, -122.7, '2026-02-28T08:00:00+00:00']],
    })

    assert visualization['samples'] == []
    assert visualization['segments'] == []


def test_validation_detail_renders_when_route_is_unavailable(monkeypatch):
    client = app.test_client()
    with client.session_transaction() as session:
        session['brevethub_operator_club_id'] = '__all__'
    monkeypatch.setattr('brevethub.models.get_validation_submission', lambda _id: {
        'id': 6, 'event_id': 20040, 'rider_id': 1, 'rider_name': 'Mihir',
        'event_name': 'Healdsburg 300k', 'event_date': '2026-02-28',
        'distance_km': 300, 'source_type': 'strava', 'machine_decision': 'needs_review',
        'organizer_decision': None, 'rwgps_url': 'https://ridewithgps.com/routes/1771223',
        'normalized_track': [[38.4, -122.7, '2026-02-28T08:00:00+00:00']],
        'rider_explanation': None, 'strava_activity_id': None,
    })
    monkeypatch.setattr('brevethub.models.get_validation_checks', lambda _id: [])
    monkeypatch.setattr('brevethub.models.get_validation_evidence', lambda _id: [])
    monkeypatch.setattr('brevethub.models.get_rp_route_elevation_track', lambda _route_id: [])

    response = client.get('/admin/validations/6')

    assert response.status_code == 200
    assert b'Healdsburg 300k' in response.data
