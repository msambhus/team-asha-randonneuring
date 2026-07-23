"""The expires_at epoch<->TIMESTAMPTZ conversion + refresh-decision contract.

BrevetHub's shared Strava code is epoch-native, but rp_strava_connection.expires_at
is TIMESTAMPTZ. The model layer bridges that: writes use to_timestamp(%s), reads
return an epoch. Two levels of coverage:

  (1) non-DB unit — the route's token-validity/refresh decision is a plain epoch
      compare against time.time(); it never compares a bare datetime.
  (2) DB-gated round-trip — proves to_timestamp(epoch) then .timestamp() is the
      identity within 1s. Skipped unless TEST_DATABASE_URL points at a real DB.
"""
import os
import time
from unittest.mock import patch

import pytest

from brevethub.routes.strava import _valid_access_token


def test_future_expiry_uses_stored_token(app):
    conn = {'access_token': 'A', 'refresh_token': 'R',
            'expires_at': time.time() + 3600, 'rider_id': 7}
    with app.app_context(), \
         patch('brevethub.routes.strava.refresh_access_token') as mock_refresh:
        token = _valid_access_token(7, conn)
    assert token == 'A'
    mock_refresh.assert_not_called()


def test_past_expiry_refreshes_and_persists_epoch(app):
    conn = {'access_token': 'old', 'refresh_token': 'R',
            'expires_at': time.time() - 10, 'rider_id': 7}
    new_tokens = {'access_token': 'new', 'refresh_token': 'R2', 'expires_at': 2100000000}
    with app.app_context(), \
         patch('brevethub.routes.strava.refresh_access_token',
               return_value=new_tokens) as mock_refresh, \
         patch('brevethub.models.update_strava_tokens') as mock_update:
        token = _valid_access_token(7, conn)
    assert token == 'new'
    mock_refresh.assert_called_once()
    mock_update.assert_called_once()
    # The refreshed epoch is persisted through the epoch-native model write path.
    assert mock_update.call_args.kwargs['expires_at'] == 2100000000


def test_none_expiry_triggers_refresh(app):
    """A NULL expires_at (never converted) must not crash the compare — it refreshes."""
    conn = {'access_token': 'old', 'refresh_token': 'R',
            'expires_at': None, 'rider_id': 7}
    with app.app_context(), \
         patch('brevethub.routes.strava.refresh_access_token',
               return_value={'access_token': 'new', 'refresh_token': 'R2',
                             'expires_at': 2100000000}), \
         patch('brevethub.models.update_strava_tokens'):
        token = _valid_access_token(7, conn)
    assert token == 'new'


_TEST_DB = os.environ.get('TEST_DATABASE_URL')


@pytest.mark.skipif(not _TEST_DB, reason="no TEST_DATABASE_URL for a live round-trip")
def test_epoch_timestamptz_roundtrip_is_identity():
    """to_timestamp(epoch) stored as TIMESTAMPTZ, read back via .timestamp(),
    returns the same epoch (within 1s) — the write/read conversions are inverse."""
    import psycopg2
    import psycopg2.extras

    epoch = int(time.time())
    conn = psycopg2.connect(_TEST_DB)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT to_timestamp(%s) AS ts", (epoch,))
            ts = cur.fetchone()['ts']
    finally:
        conn.close()
    assert abs(ts.timestamp() - epoch) < 1
