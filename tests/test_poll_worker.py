"""Tests for the Railway poll worker (worker/poll_loop.py).

The worker only triggers the existing ingest endpoint, so we just verify it
builds the right authenticated POST, summarizes the JSON response, and parses
the interval safely. No network — urlopen is mocked.
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'worker'))
import poll_loop  # noqa: E402


def test_parse_interval_defaults_and_floor():
    assert poll_loop.parse_interval(None) == 60          # unset → default
    assert poll_loop.parse_interval('junk') == 60        # non-numeric → default
    assert poll_loop.parse_interval('5') == 15           # below floor → clamped
    assert poll_loop.parse_interval('120') == 120        # honored


def test_poll_once_sends_authenticated_post():
    captured = {}

    class _Resp:
        status = 200
        def read(self): return b'{"polled":1,"inserted":2,"errors":[]}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake_urlopen(req, timeout=None):
        captured['method'] = req.get_method()
        captured['url'] = req.full_url
        captured['auth'] = req.headers.get('Authorization')
        captured['data'] = req.data
        return _Resp()

    with patch('urllib.request.urlopen', side_effect=_fake_urlopen):
        status, body = poll_loop.poll_once('https://x/api/cron/poll', 'sekret')

    assert status == 200
    assert captured['method'] == 'POST'
    assert captured['url'] == 'https://x/api/cron/poll'
    assert captured['auth'] == 'Bearer sekret'   # secret sent as bearer
    assert captured['data'] == b'{}'
    assert json.loads(body)['inserted'] == 2


def test_summarize_compacts_json_and_tolerates_nonjson():
    line = poll_loop._summarize(200, '{"polled":2,"inserted":5,"errors":[{"x":1}]}')
    assert 'polled=2' in line and 'inserted=5' in line and 'errors=1' in line
    # Non-JSON body must not raise.
    assert 'HTTP 500' in poll_loop._summarize(500, '<html>oops</html>')


def test_main_exits_without_required_env():
    with patch.dict(os.environ, {'POLL_URL': '', 'CRON_SECRET': ''}, clear=False):
        with pytest.raises(SystemExit):
            poll_loop.main()


def test_loop_survives_poll_exception():
    """The core contract: a failing poll is logged and the loop keeps going."""
    class _Stop(Exception):
        pass

    sleeps = {'n': 0}

    def _sleep(_seconds):
        sleeps['n'] += 1
        if sleeps['n'] >= 2:        # let the loop run twice, then break out
            raise _Stop()

    with patch.dict(os.environ, {'POLL_URL': 'https://x/p', 'CRON_SECRET': 's'}, clear=False), \
         patch('poll_loop.poll_once', side_effect=RuntimeError('boom')) as mock_poll, \
         patch('poll_loop.time.sleep', side_effect=_sleep):
        with pytest.raises(_Stop):
            poll_loop.main()

    # poll_once raised every time, yet the loop iterated again instead of dying.
    assert mock_poll.call_count >= 2
