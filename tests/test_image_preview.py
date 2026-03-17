"""Tests for image preview service and /api/image-preview endpoint.

Covers: SSRF defenses (HTTPS-only, domain allowlist, timeout, body limit),
OG metadata extraction, caching, auth gating, error handling.
"""
import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# _is_safe_url unit tests
# ---------------------------------------------------------------------------

class TestIsSafeUrl:
    """Domain allowlist and HTTPS enforcement."""

    def test_allowlisted_domain_accepted(self):
        from services.image_preview import _is_safe_url
        assert _is_safe_url('https://www.competitivecyclist.com/product') is True

    def test_allowlisted_domain_without_www(self):
        from services.image_preview import _is_safe_url
        assert _is_safe_url('https://competitivecyclist.com/product') is True

    def test_non_allowlisted_domain_rejected(self):
        from services.image_preview import _is_safe_url
        assert _is_safe_url('https://evil.com/product') is False

    def test_http_url_rejected(self):
        from services.image_preview import _is_safe_url
        assert _is_safe_url('http://www.competitivecyclist.com/product') is False

    def test_ftp_scheme_rejected(self):
        from services.image_preview import _is_safe_url
        assert _is_safe_url('ftp://file.local') is False

    def test_empty_string_rejected(self):
        from services.image_preview import _is_safe_url
        assert _is_safe_url('') is False

    def test_invalid_url_rejected(self):
        from services.image_preview import _is_safe_url
        assert _is_safe_url('not-a-url') is False

    def test_other_allowlisted_domains(self):
        from services.image_preview import _is_safe_url
        domains = [
            'trekbikes.com', 'bike24.com', 'wiggle.com',
            'chainreactioncycles.com', 'jensonusa.com',
            'revelatedesigns.com', 'ortlieb.com',
            'shimano.com', 'sramco.com',
            'ridewithgps.com', 'strava.com',
        ]
        for domain in domains:
            assert _is_safe_url(f'https://www.{domain}/page') is True, f'{domain} should be allowed'
            assert _is_safe_url(f'https://{domain}/page') is True, f'{domain} (no www) should be allowed'


# ---------------------------------------------------------------------------
# fetch_og_image unit tests
# ---------------------------------------------------------------------------

class TestFetchOgImage:
    """OG metadata extraction with mocked HTTP."""

    @patch('services.image_preview.requests.get')
    def test_extracts_og_image_and_title(self, mock_get):
        from services.image_preview import fetch_og_image
        html = '''<html><head>
            <meta property="og:image" content="https://cdn.example.com/img.jpg" />
            <meta property="og:title" content="Cool Bike Light" />
        </head></html>'''
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content.return_value = [html.encode()]
        mock_get.return_value = mock_resp

        result = fetch_og_image('https://www.competitivecyclist.com/product')
        assert result is not None
        assert result['image_url'] == 'https://cdn.example.com/img.jpg'
        assert result['title'] == 'Cool Bike Light'
        assert result['domain'] == 'www.competitivecyclist.com'

    @patch('services.image_preview.requests.get')
    def test_falls_back_to_twitter_image(self, mock_get):
        from services.image_preview import fetch_og_image
        html = '''<html><head>
            <meta name="twitter:image" content="https://cdn.example.com/twitter.jpg" />
            <title>Twitter Product</title>
        </head></html>'''
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content.return_value = [html.encode()]
        mock_get.return_value = mock_resp

        result = fetch_og_image('https://www.bike24.com/product')
        assert result is not None
        assert result['image_url'] == 'https://cdn.example.com/twitter.jpg'
        assert result['title'] == 'Twitter Product'

    @patch('services.image_preview.requests.get')
    def test_resolves_relative_og_image_url(self, mock_get):
        from services.image_preview import fetch_og_image
        html = '''<html><head>
            <meta property="og:image" content="/images/product.jpg" />
            <meta property="og:title" content="Relative Image" />
        </head></html>'''
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content.return_value = [html.encode()]
        mock_get.return_value = mock_resp

        result = fetch_og_image('https://www.shimano.com/product')
        assert result is not None
        assert result['image_url'] == 'https://www.shimano.com/images/product.jpg'

    @patch('services.image_preview.requests.get')
    def test_resolves_protocol_relative_og_image(self, mock_get):
        from services.image_preview import fetch_og_image
        html = '''<html><head>
            <meta property="og:image" content="//cdn.example.com/img.jpg" />
            <meta property="og:title" content="Protocol Relative" />
        </head></html>'''
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content.return_value = [html.encode()]
        mock_get.return_value = mock_resp

        result = fetch_og_image('https://www.ortlieb.com/product')
        assert result is not None
        assert result['image_url'] == 'https://cdn.example.com/img.jpg'

    @patch('services.image_preview.requests.get')
    def test_returns_none_when_no_og_or_twitter_image(self, mock_get):
        from services.image_preview import fetch_og_image
        html = '''<html><head><title>No Images</title></head></html>'''
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content.return_value = [html.encode()]
        mock_get.return_value = mock_resp

        result = fetch_og_image('https://www.wiggle.com/page')
        assert result is None

    @patch('services.image_preview.requests.get')
    def test_returns_none_when_og_image_is_http(self, mock_get):
        from services.image_preview import fetch_og_image
        html = '''<html><head>
            <meta property="og:image" content="http://insecure.com/img.jpg" />
        </head></html>'''
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content.return_value = [html.encode()]
        mock_get.return_value = mock_resp

        result = fetch_og_image('https://www.trekbikes.com/product')
        assert result is None

    @patch('services.image_preview.requests.get')
    def test_returns_none_on_timeout(self, mock_get):
        import requests as req_lib
        from services.image_preview import fetch_og_image
        mock_get.side_effect = req_lib.Timeout('Connection timed out')

        result = fetch_og_image('https://www.bike24.com/product')
        assert result is None

    @patch('services.image_preview.requests.get')
    def test_returns_none_on_connection_error(self, mock_get):
        import requests as req_lib
        from services.image_preview import fetch_og_image
        mock_get.side_effect = req_lib.ConnectionError('Failed to connect')

        result = fetch_og_image('https://www.bike24.com/product')
        assert result is None

    @patch('services.image_preview.requests.get')
    def test_returns_none_on_non_200_response(self, mock_get):
        from services.image_preview import fetch_og_image
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_get.return_value = mock_resp

        result = fetch_og_image('https://www.bike24.com/product')
        assert result is None

    @patch('services.image_preview.requests.get')
    def test_truncates_title_to_120_chars(self, mock_get):
        from services.image_preview import fetch_og_image
        long_title = 'A' * 200
        html = f'''<html><head>
            <meta property="og:image" content="https://cdn.example.com/img.jpg" />
            <meta property="og:title" content="{long_title}" />
        </head></html>'''
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content.return_value = [html.encode()]
        mock_get.return_value = mock_resp

        result = fetch_og_image('https://www.competitivecyclist.com/product')
        assert result is not None
        assert len(result['title']) == 120

    @patch('services.image_preview.requests.get')
    def test_reads_max_100kb_of_html(self, mock_get):
        from services.image_preview import fetch_og_image
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        # Simulate iter_content returning chunks that total over 100KB
        chunk_size = 50 * 1024  # 50KB each
        mock_resp.iter_content.return_value = [b'x' * chunk_size, b'x' * chunk_size, b'x' * chunk_size]
        mock_get.return_value = mock_resp

        # Should still return None since garbage HTML has no og:image
        result = fetch_og_image('https://www.bike24.com/product')
        assert result is None
        # Verify iter_content was called (streaming mode)
        mock_resp.iter_content.assert_called_once()


# ---------------------------------------------------------------------------
# /api/image-preview endpoint integration tests
# ---------------------------------------------------------------------------

class TestImagePreviewEndpoint:
    """Route-level tests for /api/image-preview."""

    def test_unauthenticated_returns_401(self, client):
        resp = client.get('/api/image-preview?url=https://www.bike24.com/product')
        assert resp.status_code == 401
        data = resp.get_json()
        assert data['error'] == 'Authentication required'

    def test_blocked_domain_returns_403(self, client):
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        resp = client.get('/api/image-preview?url=https://evil.com/page')
        assert resp.status_code == 403

    def test_missing_url_param_returns_400(self, client):
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        resp = client.get('/api/image-preview')
        assert resp.status_code == 400

    @patch('routes.chat.fetch_og_image')
    def test_valid_url_returns_200_with_json(self, mock_fetch, client):
        mock_fetch.return_value = {
            'image_url': 'https://cdn.example.com/img.jpg',
            'title': 'Cool Product',
            'domain': 'www.competitivecyclist.com',
        }
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        resp = client.get('/api/image-preview?url=https://www.competitivecyclist.com/product')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['image_url'] == 'https://cdn.example.com/img.jpg'
        assert data['title'] == 'Cool Product'
        assert data['domain'] == 'www.competitivecyclist.com'

    @patch('routes.chat.fetch_og_image')
    def test_no_og_image_returns_404(self, mock_fetch, client):
        mock_fetch.return_value = None
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        resp = client.get('/api/image-preview?url=https://www.competitivecyclist.com/no-og')
        assert resp.status_code == 404
        data = resp.get_json()
        assert 'error' in data
        assert 'no preview available' in data['error'].lower()

    @patch('routes.chat.fetch_og_image')
    @patch('routes.chat.cache')
    def test_cache_hit_skips_outbound_fetch(self, mock_cache, mock_fetch, client):
        cached_result = {
            'image_url': 'https://cdn.example.com/cached.jpg',
            'title': 'Cached Product',
            'domain': 'www.bike24.com',
        }
        mock_cache.get.return_value = cached_result
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        resp = client.get('/api/image-preview?url=https://www.bike24.com/product')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['image_url'] == 'https://cdn.example.com/cached.jpg'
        # fetch_og_image should NOT have been called since cache hit
        mock_fetch.assert_not_called()
