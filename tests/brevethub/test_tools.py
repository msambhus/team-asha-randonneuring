"""BrevetHub FIT merge route integration."""
import io
from unittest.mock import patch


def test_merge_page_is_public_and_branded(client):
    response = client.get('/tools/merge-fit')
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'Merge GPS Files' in body
    assert 'processed in memory' in body
    assert 'Team Asha' not in body


def test_merge_requires_two_files(client):
    response = client.post('/tools/merge-fit', data={
        'mode': 'overlay',
        'files': (io.BytesIO(b'one'), 'one.fit'),
    }, content_type='multipart/form-data')
    assert response.status_code == 400
    assert 'at least two' in response.get_data(as_text=True)


def test_merge_downloads_engine_output(client):
    with patch('brevethub.routes.tools.merge_fit_files', return_value=b'merged') as merge:
        response = client.post('/tools/merge-fit', data={
            'mode': 'concat',
            'files': [
                (io.BytesIO(b'one'), 'one.fit'),
                (io.BytesIO(b'two'), 'two.fit'),
            ],
        }, content_type='multipart/form-data')
    assert response.status_code == 200
    assert response.data == b'merged'
    assert response.headers['Content-Disposition'].endswith('filename=merged.fit')
    assert response.headers['Cache-Control'] == 'no-store'
    merge.assert_called_once_with([b'one', b'two'], mode='concat')


def test_brevethub_registers_weather_analysis_and_merge_surfaces(app):
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert '/analysis' in rules
    assert '/plan/<int:event_id>/weather-data' in rules
    assert '/tools/merge-fit' in rules
