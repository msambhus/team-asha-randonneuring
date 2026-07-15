"""shared/broker_state.py — HMAC state signing round-trips and every tamper /
staleness case returns None (never raises).

The broker's whole authenticity story rests on this module: a Team Asha state must
verify on BrevetHub with the shared secret and be rejected otherwise. Pure crypto,
no Flask app or DB needed.
"""
from shared.broker_state import sign_state, verify_state

_SECRET = 'a-shared-broker-secret-long-enough'
_NOW = 1_700_000_000


def _sign(**overrides):
    kwargs = dict(secret=_SECRET, origin='team-asha', ta_rider_id=42,
                  return_url='https://team-asha-randonneuring.vercel.app/strava/broker-return',
                  issued_at=_NOW, nonce='fixed-nonce')
    kwargs.update(overrides)
    return sign_state(**kwargs)


def test_round_trip_recovers_payload():
    state = _sign()
    payload = verify_state(state, secret=_SECRET, max_age=600, now=_NOW)
    assert payload is not None
    assert payload['origin'] == 'team-asha'
    assert payload['ta_rider_id'] == 42
    assert payload['return_url'].endswith('/strava/broker-return')
    assert payload['nonce'] == 'fixed-nonce'
    assert payload['iat'] == _NOW


def _flip_first(text):
    """Flip the FIRST base64 char — reliably changes the decoded bytes (a trailing
    char can encode only dropped padding bits, so flipping it may be a no-op)."""
    return ('A' if text[0] != 'A' else 'B') + text[1:]


def test_tampered_payload_rejected():
    state = _sign()
    payload_b64, sig = state.split('.')
    assert verify_state(f"{_flip_first(payload_b64)}.{sig}",
                        secret=_SECRET, max_age=600, now=_NOW) is None


def test_tampered_signature_rejected():
    state = _sign()
    payload_b64, sig = state.split('.')
    assert verify_state(f"{payload_b64}.{_flip_first(sig)}",
                        secret=_SECRET, max_age=600, now=_NOW) is None


def test_wrong_secret_rejected():
    state = _sign()
    assert verify_state(state, secret='different-secret', max_age=600, now=_NOW) is None


def test_expired_iat_rejected():
    state = _sign(issued_at=_NOW - 5000)
    assert verify_state(state, secret=_SECRET, max_age=600, now=_NOW) is None


def test_future_iat_rejected():
    state = _sign(issued_at=_NOW + 5000)
    assert verify_state(state, secret=_SECRET, max_age=600, now=_NOW) is None


def test_malformed_state_rejected():
    for bad in (None, '', 'no-dot', 'a.b.c', '.', 'x.', '.y', 12345):
        assert verify_state(bad, secret=_SECRET, max_age=600, now=_NOW) is None


def test_empty_secret_rejected():
    state = _sign()
    assert verify_state(state, secret='', max_age=600, now=_NOW) is None


def test_signatures_differ_by_secret():
    a = _sign()
    b = _sign(secret='another-secret-value-entirely')
    assert a.split('.')[1] != b.split('.')[1]
