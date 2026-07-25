from unittest.mock import patch

from flask import Flask

from routes.cron import warm_ride_plans


def _app():
    app = Flask(__name__)
    app.config.update(
        CRON_SECRET='test-secret',
        RWGPS_API_KEY='key',
        RWGPS_AUTH_TOKEN='token',
    )
    return app


def _request(app, authorized=True):
    headers = {
        'Authorization': 'Bearer test-secret' if authorized else 'Bearer wrong'
    }
    return app.test_request_context('/api/cron/warm-ride-plans', headers=headers)


def test_warm_ride_plans_requires_cron_secret():
    app = _app()
    with _request(app, authorized=False):
        response, status = warm_ride_plans()
    assert status == 401
    assert response.get_json()['error'] == 'Unauthorized'


def test_warm_ride_plans_generates_and_links_new_plan():
    app = _app()
    target = {
        'id': 17, 'name': 'ACP 300K', 'start_time': '06:30',
        'rwgps_url': 'https://ridewithgps.com/routes/12345',
    }
    built = {'plan': {'name': 'ACP 300K'}, 'stops': [{'location': 'Start'}]}

    with _request(app), \
         patch('models.get_ride_plan_warm_targets', return_value=[target]), \
         patch('models.get_ride_plan_by_rwgps_route_id', return_value=None), \
         patch('shared.rwgps.fetch_route', return_value={'id': 12345}) as fetch, \
         patch('shared.rwgps.extract_controls', return_value=[{'name': 'Start'}]), \
         patch('shared.rwgps.build_ride_plan', return_value=built) as build, \
         patch('models.create_ride_plan_from_rwgps', return_value=99) as create, \
         patch('models.update_ride_details') as link:
        response, status = warm_ride_plans()

    assert status == 200
    assert response.get_json() == {
        'ok': True, 'considered': 1, 'generated': 1,
        'linked_existing': 0, 'skipped': 0, 'failed': 0,
    }
    fetch.assert_called_once_with('12345', 'key', 'token')
    assert build.call_args.kwargs['start_time'] == '06:30'
    assert build.call_args.kwargs['insert_meals'] is True
    create.assert_called_once_with(built['plan'], built['stops'])
    link.assert_called_once_with(17, ride_plan_id=99)


def test_warm_ride_plans_reuses_existing_plan_and_skips_bad_url():
    app = _app()
    targets = [
        {'id': 1, 'rwgps_url': 'https://ridewithgps.com/routes/456'},
        {'id': 2, 'rwgps_url': 'not-a-route'},
    ]
    with _request(app), \
         patch('models.get_ride_plan_warm_targets', return_value=targets), \
         patch('models.get_ride_plan_by_rwgps_route_id',
               return_value={'id': 88}), \
         patch('models.update_ride_details') as link:
        response, status = warm_ride_plans()

    assert status == 200
    assert response.get_json()['linked_existing'] == 1
    assert response.get_json()['skipped'] == 1
    link.assert_called_once_with(1, ride_plan_id=88)


def test_warm_ride_plans_keeps_processing_after_one_failure():
    app = _app()
    targets = [
        {'id': 1, 'rwgps_url': 'https://ridewithgps.com/routes/111'},
        {'id': 2, 'rwgps_url': 'https://ridewithgps.com/routes/222'},
    ]
    with _request(app), \
         patch('models.get_ride_plan_warm_targets', return_value=targets), \
         patch('models.get_ride_plan_by_rwgps_route_id',
               side_effect=[RuntimeError('db'), {'id': 44}]), \
         patch('models.update_ride_details') as link:
        response, status = warm_ride_plans()

    assert status == 200
    assert response.get_json()['failed'] == 1
    assert response.get_json()['linked_existing'] == 1
    link.assert_called_once_with(2, ride_plan_id=44)
