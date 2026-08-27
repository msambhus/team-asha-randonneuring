"""Small, server-only adapter for private Supabase Storage evidence files."""
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class EvidenceStorageError(RuntimeError):
    pass


def _request(config, method, path, *, body=None, content_type=None):
    base = (config.get('SUPABASE_URL') or '').rstrip('/')
    key = config.get('SUPABASE_SERVICE_ROLE_KEY')
    if not base or not key:
        raise EvidenceStorageError('Evidence image storage is not configured.')
    headers = {'Authorization': f'Bearer {key}', 'apikey': key}
    if content_type:
        headers['Content-Type'] = content_type
    request = Request(f'{base}/storage/v1{path}', data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=25) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except (HTTPError, URLError, TimeoutError) as exc:
        raise EvidenceStorageError(f'Supabase Storage request failed: {exc}') from exc


def upload(config, bucket, path, data, content_type):
    _request(config, 'POST', f'/object/{bucket}/{path}', body=data, content_type=content_type)


def signed_url(config, bucket, path, expires=300):
    result = _request(config, 'POST', f'/object/sign/{bucket}/{path}',
                      body=json.dumps({'expiresIn': expires}).encode(),
                      content_type='application/json')
    signed = result.get('signedURL') or result.get('signedUrl')
    if not signed:
        raise EvidenceStorageError('Supabase Storage did not return a signed URL.')
    return signed if signed.startswith('http') else f"{config['SUPABASE_URL'].rstrip('/')}/storage/v1{signed}"


def remove(config, bucket, paths):
    if paths:
        _request(config, 'DELETE', f'/object/{bucket}',
                 body=json.dumps({'prefixes': paths}).encode(),
                 content_type='application/json')
