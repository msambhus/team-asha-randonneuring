"""The shared open-redirect guard used by both the OAuth callback and signup.

Only a same-host relative path may be honored; absolute and scheme-relative
URLs (and the backslash variant browsers rewrite to scheme-relative) must be
rejected so a planted `next` value can't bounce a signed-in user off-site.
"""
import pytest

from brevethub.redirects import is_safe_relative_url, safe_redirect


@pytest.mark.parametrize('url', [
    '/dashboard',
    '/signup/',
    '/rides?ride_id=5',
    '/a/b/c',
])
def test_relative_paths_are_safe(url):
    assert is_safe_relative_url(url) is True


@pytest.mark.parametrize('url', [
    '',
    None,
    '//evil.example',
    '//evil.example/phish',
    'https://evil.example',
    'http://evil.example/x',
    'javascript:alert(1)',
    r'/\evil.example',       # browsers rewrite \ → / → protocol-relative
    r'\\evil.example',
])
def test_absolute_and_scheme_relative_are_unsafe(url):
    assert is_safe_relative_url(url) is False


def test_safe_redirect_honors_relative_path(app):
    with app.test_request_context():
        resp = safe_redirect('/dashboard', 'main.landing')
    assert resp.status_code in (301, 302)
    assert resp.headers['Location'] == '/dashboard'


def test_safe_redirect_falls_back_on_unsafe_url(app):
    with app.test_request_context():
        resp = safe_redirect('//evil.example', 'main.landing')
    assert resp.status_code in (301, 302)
    # Fell back to the named endpoint, not the attacker host.
    assert 'evil.example' not in resp.headers['Location']
    assert resp.headers['Location'].endswith('/')
