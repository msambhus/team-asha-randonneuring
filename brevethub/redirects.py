"""Redirect safety — only ever bounce to a relative path on this host.

Both the OAuth callback and the signup flow consume a caller-supplied `next`
URL stashed in the session at login. An attacker who plants an absolute
(`https://evil`) or scheme-relative (`//evil`) value there could send a
freshly-signed-in user off-site. Every such redirect goes through the single
guard here so the check can never drift between call sites.
"""
from urllib.parse import urlparse

from flask import redirect, url_for


def is_safe_relative_url(url):
    """True only for a same-host relative path (no scheme, no network location).

    Backslashes are normalized to forward slashes first: some browsers treat
    ``/\\evil.com`` as the protocol-relative ``//evil.com``, so the guard must
    see it that way too.
    """
    if not url:
        return False
    normalized = url.replace('\\', '/')
    parsed = urlparse(normalized)
    return not parsed.scheme and not parsed.netloc


def safe_redirect(url, fallback_endpoint):
    """Redirect to `url` if it is a safe relative path, else to `fallback_endpoint`."""
    if is_safe_relative_url(url):
        return redirect(url)
    return redirect(url_for(fallback_endpoint))


def is_allowed_broker_return_url(url, allowed_origins):
    """True only for an absolute URL whose ``scheme://host[:port]`` origin exactly
    matches one in ``allowed_origins``.

    This guards the shared Strava broker's cross-app bounce: BrevetHub must only
    ever 302 a Team-Asha flow back to a Team-Asha (or its own) origin, never to an
    attacker-chosen host. Backslashes are normalized first (some browsers read
    ``https:/\\evil.com`` as a host), and a relative or scheme-less URL has no
    origin so it can never match an allowlisted absolute origin.
    """
    if not url or not allowed_origins:
        return False
    parsed = urlparse(url.replace('\\', '/'))
    if not parsed.scheme or not parsed.netloc:
        return False
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return origin in allowed_origins
