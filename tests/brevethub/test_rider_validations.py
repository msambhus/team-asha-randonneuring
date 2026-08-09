from datetime import date

from brevethub.app import app


def _rider_login(client, monkeypatch):
    rider = {'id': 1, 'email': 'mihir@example.com', 'profile_completed': True, 'club_id': None}
    monkeypatch.setattr('brevethub.models.get_rider_by_id', lambda _id: rider)
    with client.session_transaction() as session:
        session['rider_id'] = 1
        session['email'] = rider['email']
    return rider


def _event():
    return {'event_id': 12, 'id': 12, 'name': 'Test 200K', 'date': date(2026, 8, 1),
            'distance_km': 200, 'region': 'CA: Test', 'rwgps_url': None,
            'start_time': '06:00', 'time_limit_hours': 13.5}


def test_rider_sees_only_own_completed_events(monkeypatch):
    client = app.test_client()
    _rider_login(client, monkeypatch)
    monkeypatch.setattr('brevethub.models.get_rider_completed_validation_events', lambda _id: [_event()])
    response = client.get('/my/validations')
    assert response.status_code == 200
    assert 'Submit completion evidence' not in response.get_data(as_text=True)
    assert 'Test 200K' in response.get_data(as_text=True)


def test_rider_cannot_submit_for_another_event(monkeypatch):
    client = app.test_client()
    _rider_login(client, monkeypatch)
    monkeypatch.setattr('brevethub.models.get_rider_completed_validation_events', lambda _id: [])
    assert client.get('/my/validations/999/new').status_code == 404


def test_rider_submission_is_marked_as_rider_owned(monkeypatch):
    client = app.test_client()
    _rider_login(client, monkeypatch)
    event = _event()
    monkeypatch.setattr('brevethub.models.get_rider_completed_validation_events', lambda _id: [event])
    monkeypatch.setattr('brevethub.models.get_brevet_event_full', lambda _id: event)
    monkeypatch.setattr('brevethub.models.get_brevet_route_plan_with_stops', lambda _id: None)
    monkeypatch.setattr('brevethub.models.find_validation_evidence_conflicts', lambda *a, **k: [])
    saved = {}
    monkeypatch.setattr('brevethub.models.create_validation_submission', lambda **kwargs: saved.update(kwargs) or {'id': 77})
    monkeypatch.setattr('brevethub.models.add_validation_evidence', lambda *a, **k: {'id': 1})
    monkeypatch.setattr('brevethub.models.replace_validation_checks', lambda *a, **k: None)
    response = client.post('/my/validations/12/new', data={'proof_description': 'Signed brevet card'})
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/my/validations')
    assert saved['rider_id'] == 1
    assert saved['submitted_by'] == 'rider'
