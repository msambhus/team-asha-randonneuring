"""Garmin LiveTrack ingestion — pure re-export shim.

The single Garmin LiveTrack implementation now lives in
``shared/garmin_livetrack.py`` (club-agnostic, framework-agnostic, Flask-free),
so Team Asha and BrevetHub share ONE engine that cannot drift. This module
re-exports that engine's ENTIRE surface — every public name AND every
module-private (``_extract_point``, ``_extract_trackpoints_html``,
``_parse_timestamp``, ``_num``) and constant (``_SESSION_URL_RE`` /
``_SHARE_PAGE_URL`` / ``_BROWSER_UA`` / ``_BROWSER_HEADERS`` / ``_REQUEST_TIMEOUT``)
— each the *same object* as in ``shared.garmin_livetrack``. So every existing
``from services.garmin_livetrack import ...`` caller (routes/cron.py,
routes/live.py) and test (tests/test_live_tracking.py, tests/test_live_metrics.py,
which reach for the module-privates) keeps working unchanged, and a test that
patches ``services.garmin_livetrack.fetch_positions`` still intercepts the callers
that import it from here.

This shim holds NO logic of its own (it defines no function/class), so it can
never diverge from the canonical engine. ``tests/test_garmin_livetrack_shim.py``
enforces the same-object re-export of the full surface and that the shim stays
def-free.
"""
from shared.garmin_livetrack import (  # noqa: F401  — re-export, names used by callers
    _BROWSER_HEADERS,
    _BROWSER_UA,
    _REQUEST_TIMEOUT,
    _SESSION_URL_RE,
    _SHARE_PAGE_URL,
    _extract_point,
    _extract_trackpoints_html,
    _num,
    _parse_timestamp,
    fetch_positions,
    parse_session,
)
