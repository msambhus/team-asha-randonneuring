#!/usr/bin/env python3
"""Railway scheduler for Team Asha maintenance jobs.

This replaces the GitHub Actions cron runners.  Railway keeps this small
process alive and it calls the existing authenticated Vercel cron endpoints,
so the application logic remains in one place.  The RUSA importer is the one
exception: it runs locally because it needs direct database access.

Required environment:
  APP_BASE_URL   Team Asha deployment URL (defaults to production)
  CRON_SECRET    bearer secret accepted by the Vercel cron endpoints
  DATABASE_URL   required by the direct RUSA importer

The live-track poller remains in the same Railway service and is run by
``worker/service.py``; this module only owns the lower-frequency jobs.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request


BASE_URL = os.environ.get(
    "APP_BASE_URL", "https://team-asha-randonneuring.vercel.app"
).rstrip("/")
SECRET = os.environ.get("CRON_SECRET", "").strip()

# (name, minute-of-hour, endpoint).  ``period_hours`` is used for the jobs
# whose cadence is shorter than a day.
HTTP_JOBS = (
    ("fetch-route-weather", 15, "/api/cron/fetch-route-weather", 1),
    ("sync-strava", 0, "/api/cron/sync-strava", 6),
    ("finalize-rides", 0, "/api/cron/finalize-rides", 24),
    ("sync-rusa-results", 0, "/api/cron/sync-rusa-results", 24),
    ("warm-plan-elevation", 45, "/api/cron/warm-plan-elevation", 24),
)


def log(message: str) -> None:
    print(f"[railway-scheduler] {message}", flush=True)


def post(endpoint: str, body: dict | None = None, timeout: int = 300) -> tuple[int, str]:
    request = urllib.request.Request(
        BASE_URL + endpoint,
        method="POST",
        data=json.dumps(body or {}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {SECRET}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read().decode("utf-8", "replace")


def run_http_job(name: str, endpoint: str) -> None:
    try:
        rounds = 20 if name == "sync-strava" else 1
        for round_number in range(1, rounds + 1):
            status, body = post(endpoint, timeout=300 if name == "fetch-route-weather" else 90)
            log(f"{name}: HTTP {status} {body[:300]}")
            if status < 200 or status >= 300 or name != "sync-strava":
                break
            try:
                result = json.loads(body)
            except ValueError:
                break
            backfill = result.get("backfill") or {}
            if backfill.get("error") or backfill.get("status") == "All riders fully backfilled":
                break
            if round_number < rounds:
                # Avoid hammering Strava while still allowing a full backfill.
                import time
                time.sleep(2)
    except Exception as exc:  # keep the scheduler alive after a failed job
        log(f"{name}: failed: {exc}")


def run_rusa_import() -> None:
    if not os.environ.get("DATABASE_URL"):
        log("update-rusa-events: skipped (DATABASE_URL is not configured)")
        return
    try:
        completed = subprocess.run(
            [sys.executable, "scripts/update_rusa_events.py"],
            check=False,
            capture_output=True,
            text=True,
            timeout=900,
        )
        output = (completed.stdout + completed.stderr).strip().replace("\n", " ")
        log(f"update-rusa-events: exit={completed.returncode} {output[-500:]}")
    except Exception as exc:
        log(f"update-rusa-events: failed: {exc}")


def due(job_name: str, now: dt.datetime) -> bool:
    """Return whether a job is due in this UTC minute."""
    if job_name == "fetch-route-weather":
        return now.minute == 15
    if job_name == "sync-strava":
        return now.minute == 0 and now.hour % 6 == 0
    if job_name == "finalize-rides":
        return now.hour == 6 and now.minute == 0
    if job_name == "sync-rusa-results":
        return now.hour == 7 and now.minute == 0
    if job_name == "warm-plan-elevation":
        return now.hour == 12 and now.minute == 45
    if job_name == "update-rusa-events":
        return now.hour == 13 and now.minute == 0
    return False


def main() -> None:
    if not SECRET:
        log("WARNING: CRON_SECRET is not configured; HTTP jobs will fail")
    log(f"starting for {BASE_URL}; schedules are evaluated in UTC")
    last_minute = None
    running: set[str] = set()
    lock = threading.Lock()

    while True:
        now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
        if now != last_minute:
            last_minute = now
            jobs = [(name, endpoint) for name, _minute, endpoint, _period in HTTP_JOBS]
            jobs.append(("update-rusa-events", ""))
            for name, endpoint in jobs:
                if not due(name, now):
                    continue
                with lock:
                    if name in running:
                        continue
                    running.add(name)

                def execute(job_name=name, job_endpoint=endpoint):
                    try:
                        if job_name == "update-rusa-events":
                            run_rusa_import()
                        else:
                            run_http_job(job_name, job_endpoint)
                    finally:
                        with lock:
                            running.discard(job_name)

                threading.Thread(target=execute, name=name, daemon=True).start()

        import time
        time.sleep(10)


if __name__ == "__main__":
    main()
