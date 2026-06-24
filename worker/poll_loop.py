#!/usr/bin/env python3
"""Always-on worker that polls Garmin LiveTrack on a tight interval.

GitHub Actions' scheduled cron is throttled to ~45 minutes in practice, which is
far too slow for "live" tracking. This worker instead loops on its own clock and
triggers the existing, already-tested ingest endpoint every
POLL_INTERVAL_SECONDS, so positions land within ~a minute. Designed to run as a
tiny always-on Railway service.

It only needs to reach the public endpoint — no database access, stdlib only.

Environment:
  POLL_URL               full URL of the poll endpoint, e.g.
                         https://team-asha-randonneuring.vercel.app/api/cron/poll-garmin-livetrack
  CRON_SECRET            bearer secret the endpoint checks (same value as Vercel)
  POLL_INTERVAL_SECONDS  seconds between polls (default 60, floor 15)

The loop never dies on a transient error — it logs and keeps going.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_INTERVAL = 60
MIN_INTERVAL = 15
REQUEST_TIMEOUT = 90


def _log(msg):
    # Unbuffered, single-line logs so Railway streams them live.
    print(f"[poll-loop] {msg}", flush=True)


def parse_interval(raw, default=DEFAULT_INTERVAL, floor=MIN_INTERVAL):
    """Parse POLL_INTERVAL_SECONDS, clamped to a sane floor. Tolerates junk."""
    try:
        return max(floor, int(raw))
    except (TypeError, ValueError):
        return default


def poll_once(url, secret, timeout=REQUEST_TIMEOUT):
    """POST the poll endpoint once. Returns (status_code, body_text)."""
    req = urllib.request.Request(
        url, method="POST", data=b"{}",
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def _summarize(status, body):
    """Turn the endpoint's JSON into a compact log line."""
    try:
        data = json.loads(body)
    except ValueError:
        return f"HTTP {status} {body[:200]}"
    return (f"HTTP {status} polled={data.get('polled')} "
            f"inserted={data.get('inserted')} "
            f"errors={len(data.get('errors') or [])}")


def main():
    url = os.environ.get("POLL_URL", "").strip()
    secret = os.environ.get("CRON_SECRET", "").strip()
    interval = parse_interval(os.environ.get("POLL_INTERVAL_SECONDS"))

    if not url or not secret:
        _log("FATAL: POLL_URL and CRON_SECRET must both be set")
        sys.exit(1)

    _log(f"starting — every {interval}s -> {url}")
    while True:
        start = time.monotonic()
        try:
            status, body = poll_once(url, secret)
            _log(_summarize(status, body))
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:200] if hasattr(exc, "read") else ""
            _log(f"HTTP error {exc.code}: {detail!r}")
        except Exception as exc:  # noqa: BLE001 — keep the loop alive no matter what
            _log(f"poll failed: {exc}")
        # Sleep the remainder of the interval (subtract time the poll took).
        time.sleep(max(1, interval - (time.monotonic() - start)))


if __name__ == "__main__":
    main()
