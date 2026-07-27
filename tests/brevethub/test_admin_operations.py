from brevethub.app import app


def test_operations_dashboard_requires_separate_admin_password(monkeypatch):
    monkeypatch.setitem(app.config, 'ADMIN_PASSWORD', 'test-operator-password')
    client = app.test_client()

    response = client.get('/admin/')
    assert response.status_code == 302
    assert '/admin/login' in response.headers['Location']

    bad = client.post('/admin/login', data={'password': 'wrong'})
    assert bad.status_code == 200
    assert 'Incorrect admin password' in bad.get_data(as_text=True)

    good = client.post('/admin/login', data={'password': 'test-operator-password'})
    assert good.status_code == 302
    assert good.headers['Location'].endswith('/admin/')


def test_operator_action_reuses_cron_pipeline_core(monkeypatch):
    monkeypatch.setitem(app.config, 'ADMIN_PASSWORD', 'test-operator-password')
    calls = []

    def fake_runner():
        calls.append('ran')
        return {'ok': True, 'filled': 3, 'remaining': 7}

    monkeypatch.setattr(
        'brevethub.routes.cron.run_backfill_rwgps_urls', fake_runner)
    client = app.test_client()
    client.post('/admin/login', data={'password': 'test-operator-password'})

    response = client.post('/admin/run/backfill-rwgps')

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/admin/')
    assert calls == ['ran']


def test_unknown_operator_action_is_not_dispatched(monkeypatch):
    monkeypatch.setitem(app.config, 'ADMIN_PASSWORD', 'test-operator-password')
    client = app.test_client()
    client.post('/admin/login', data={'password': 'test-operator-password'})

    assert client.post('/admin/run/not-real').status_code == 404


def test_operator_can_dispatch_both_weather_warmers(monkeypatch):
    monkeypatch.setitem(app.config, 'ADMIN_PASSWORD', 'test-operator-password')
    calls = []

    monkeypatch.setattr(
        'brevethub.routes.cron.run_fetch_brevet_weather',
        lambda: calls.append('point') or {'ok': True, 'fetched': 2})
    monkeypatch.setattr(
        'brevethub.routes.cron.run_warm_brevet_route_weather',
        lambda: calls.append('route') or {'ok': True, 'warmed': 2})
    client = app.test_client()
    client.post('/admin/login', data={'password': 'test-operator-password'})

    assert client.post('/admin/run/fetch-weather').status_code == 302
    assert client.post('/admin/run/warm-route-weather').status_code == 302
    assert calls == ['point', 'route']
