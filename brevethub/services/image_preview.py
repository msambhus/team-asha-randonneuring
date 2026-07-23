"""Image preview service — fetch OpenGraph metadata from allowlisted domains.

SSRF defenses:
- HTTPS-only URLs (no HTTP, FTP, etc.)
- Domain allowlist (cycling/product sites only)
- 2-second timeout on outbound requests
- No redirect following (allow_redirects=False)
- 100KB max body read (streaming with iter_content)
"""
from __future__ import annotations

import requests
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup

# Cycling/product domains allowed for OG image preview.
# Both bare and www. variants are checked at validation time.
# Excluded: amazon.com, rei.com (block server-side OG fetches).
ALLOWED_PREVIEW_DOMAINS = {
    'competitivecyclist.com',
    'www.competitivecyclist.com',
    'trekbikes.com',
    'www.trekbikes.com',
    'bike24.com',
    'www.bike24.com',
    'wiggle.com',
    'www.wiggle.com',
    'chainreactioncycles.com',
    'www.chainreactioncycles.com',
    'jensonusa.com',
    'www.jensonusa.com',
    'revelatedesigns.com',
    'www.revelatedesigns.com',
    'ortlieb.com',
    'www.ortlieb.com',
    'shimano.com',
    'www.shimano.com',
    'sramco.com',
    'www.sramco.com',
    'ridewithgps.com',
    'www.ridewithgps.com',
    'strava.com',
    'www.strava.com',
}

_USER_AGENT = 'Mozilla/5.0 (compatible; TeamAshaChatbot/1.0)'
_MAX_BODY_BYTES = 100 * 1024  # 100KB
_MAX_TITLE_LEN = 120


def _is_safe_url(url: str) -> bool:
    """Validate that url uses HTTPS and targets an allowlisted domain."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme != 'https':
        return False
    hostname = parsed.hostname
    if not hostname:
        return False
    return hostname in ALLOWED_PREVIEW_DOMAINS


def fetch_og_image(url: str, timeout: float = 2.0) -> dict | None:
    """Fetch OpenGraph image metadata from a URL.

    Returns dict with {image_url, title, domain} or None on failure.
    """
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            allow_redirects=False,
            stream=True,
            headers={'User-Agent': _USER_AGENT},
        )
    except (requests.Timeout, requests.ConnectionError, requests.RequestException):
        return None

    if resp.status_code != 200:
        return None

    # Read up to 100KB of the response body
    body_chunks = []
    bytes_read = 0
    for chunk in resp.iter_content(chunk_size=8192):
        body_chunks.append(chunk)
        bytes_read += len(chunk)
        if bytes_read >= _MAX_BODY_BYTES:
            break
    html = b''.join(body_chunks)

    soup = BeautifulSoup(html, 'lxml')

    # Extract image URL: og:image first, fallback to twitter:image
    image_url = None
    og_img = soup.find('meta', property='og:image')
    if og_img and og_img.get('content'):
        image_url = og_img['content'].strip()
    else:
        tw_img = soup.find('meta', attrs={'name': 'twitter:image'})
        if tw_img and tw_img.get('content'):
            image_url = tw_img['content'].strip()

    if not image_url:
        return None

    # Resolve relative and protocol-relative URLs
    if image_url.startswith('//'):
        image_url = 'https:' + image_url
    elif not image_url.startswith('http'):
        image_url = urljoin(url, image_url)

    # Reject non-HTTPS image URLs
    if not image_url.startswith('https://'):
        return None

    # Extract title: og:title first, fallback to <title>
    title = None
    og_title = soup.find('meta', property='og:title')
    if og_title and og_title.get('content'):
        title = og_title['content'].strip()
    else:
        title_tag = soup.find('title')
        if title_tag and title_tag.string:
            title = title_tag.string.strip()

    if title and len(title) > _MAX_TITLE_LEN:
        title = title[:_MAX_TITLE_LEN]

    parsed = urlparse(url)
    return {
        'image_url': image_url,
        'title': title or '',
        'domain': parsed.hostname or '',
    }
