from datetime import date

from brevethub.app import app


def _login(client, monkeypatch):
    monkeypatch.setitem(app.config, 'ADMIN_PASSWORD', 'operator-secret')
    client.post('/admin/login', data={'password': 'operator-secret'})


def test_validation_queue_is_operator_only(monkeypatch):
    client = app.test_client()
    assert client.get('/admin/validations').status_code == 302
    _login(client, monkeypatch)
    monkeypatch.setattr('brevethub.models.list_validation_submissions', lambda: [])
    response = client.get('/admin/validations')
    assert response.status_code == 200
    assert 'Automated evidence checks support organizer judgment' in response.get_data(as_text=True)


def test_traditional_submission_persists_advisory_review(monkeypatch):
    client = app.test_client()
    _login(client, monkeypatch)
    candidate = {'event_id': 12, 'rider_id': 9, 'date': date(2026, 8, 7),
                 'event_name': 'Test 200K', 'distance_km': 200, 'rider_name': 'rider'}
    event = dict(candidate, id=12, region='CA: Test', start_time='06:00',
                 time_limit_hours=13.5, rwgps_url=None)
    monkeypatch.setattr('brevethub.models.get_validation_candidates', lambda: [candidate])
    monkeypatch.setattr('brevethub.models.get_brevet_event_full', lambda _id: event)
    monkeypatch.setattr('brevethub.models.get_brevet_route_plan_with_stops', lambda _id: None)
    monkeypatch.setattr('brevethub.models.find_validation_evidence_conflicts', lambda *a, **k: [])
    monkeypatch.setattr('brevethub.models.create_validation_submission', lambda **k: {'id': 44})
    monkeypatch.setattr('brevethub.models.add_validation_evidence', lambda *a, **k: {'id': 1})
    saved = {}
    monkeypatch.setattr('brevethub.models.replace_validation_checks',
                        lambda submission_id, decision, checks: saved.update(id=submission_id, decision=decision, checks=checks))

    response = client.post('/admin/validations/new', data={
        'event_id': '12', 'rider_id': '9', 'proof_description': 'Signed brevet card',
    })
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/admin/validations/44')
    assert saved['decision'] == 'needs_review'


def test_organizer_decision_is_distinct_from_machine_result(monkeypatch):
    client = app.test_client()
    _login(client, monkeypatch)
    monkeypatch.setattr('brevethub.models.get_validation_submission', lambda _id: {'id': 44})
    saved = {}
    monkeypatch.setattr('brevethub.models.set_validation_organizer_decision',
                        lambda sid, decision, notes, reviewed_by='operator': saved.update(
                            sid=sid, decision=decision, notes=notes))
    response = client.post('/admin/validations/44/decision', data={
        'organizer_decision': 'approved', 'organizer_notes': 'Safety detour accepted',
    })
    assert response.status_code == 302
    assert saved == {'sid': 44, 'decision': 'approved', 'notes': 'Safety detour accepted'}


def test_private_evidence_requires_operator_session(monkeypatch):
    client = app.test_client()
    path = '/admin/validations/44/evidence/8'
    assert client.get(path).status_code == 302
    _login(client, monkeypatch)
    monkeypatch.setattr('brevethub.models.get_validation_evidence_content', lambda sid, eid: {
        'id': eid, 'original_filename': 'receipt.jpg', 'content_type': 'image/jpeg',
        'private_content': b'private-proof',
    })
    response = client.get(path)
    assert response.status_code == 200
    assert response.data == b'private-proof'
    assert response.headers['Cache-Control'].startswith('no-cache')
