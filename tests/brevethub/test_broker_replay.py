"""The durable single-use claim that backs replay rejection.

`claim_broker_state` is the one primitive that makes a signed state single-use
across stateless serverless invocations: its `INSERT ... ON CONFLICT (nonce) DO
NOTHING RETURNING nonce` returns a row for a fresh nonce and nothing for a
duplicate. There is no live DB in this suite, so the SQL semantics are asserted
structurally and the return-value wiring is exercised through a mocked db layer —
a fresh nonce yields the row, a claimed nonce yields None.
"""
import inspect
from unittest.mock import patch

from brevethub import models


def test_claim_uses_on_conflict_do_nothing_returning():
    src = inspect.getsource(models.claim_broker_state)
    assert 'INSERT INTO rp_strava_broker_state' in src
    assert 'ON CONFLICT (nonce) DO NOTHING' in src
    assert 'RETURNING nonce' in src


def test_claim_returns_row_for_fresh_nonce():
    with patch('brevethub.models.db.execute', return_value={'nonce': 'n1'}) as mock_exec:
        result = models.claim_broker_state('n1', state_ttl_seconds=600)
    assert result == {'nonce': 'n1'}
    # The claim is a RETURNING insert (returning=True) so the row is surfaced.
    assert mock_exec.call_args.kwargs.get('returning') is True


def test_claim_returns_none_for_duplicate_nonce():
    """ON CONFLICT DO NOTHING → no row → None → the route hard-rejects as replay."""
    with patch('brevethub.models.db.execute', return_value=None):
        assert models.claim_broker_state('dup', state_ttl_seconds=600) is None


def test_consume_uses_unconsumed_guard_and_returning():
    src = inspect.getsource(models.consume_broker_state)
    assert 'UPDATE rp_strava_broker_state' in src
    assert 'consumed_at = NOW()' in src
    assert 'consumed_at IS NULL' in src   # only a claimed, not-yet-used nonce consumes
    assert 'RETURNING nonce' in src


def test_consume_returns_row_when_claimed_and_unused():
    with patch('brevethub.models.db.execute', return_value={'nonce': 'n1'}) as mock_exec:
        assert models.consume_broker_state('n1') == {'nonce': 'n1'}
    assert mock_exec.call_args.kwargs.get('returning') is True


def test_consume_returns_none_when_unclaimed_or_already_used():
    """No matching unconsumed row → None → the route hard-rejects (bypass/replay)."""
    with patch('brevethub.models.db.execute', return_value=None):
        assert models.consume_broker_state('never-claimed') is None


def test_create_broker_handoff_returns_opaque_code_and_stores_via_to_timestamp():
    with patch('brevethub.models.db.execute') as mock_exec:
        code = models.create_broker_handoff(
            ta_rider_id=42, strava_athlete_id=1, access_token='A',
            refresh_token='R', strava_token_expires_at=1999999999,
            scope='activity:read_all', handoff_ttl_seconds=300,
        )
    assert isinstance(code, str) and len(code) >= 20  # high-entropy one-time code
    sql, params = mock_exec.call_args.args[0], mock_exec.call_args.args[1]
    assert 'INSERT INTO rp_strava_broker_handoff' in sql
    assert 'to_timestamp(%s)' in sql              # Strava-token lifetime column
    assert 'make_interval(secs => %s)' in sql     # short handoff TTL
    # The generated code is the first bound param and the tokens are stored.
    assert params[0] == code
    assert 'A' in params and 'R' in params
