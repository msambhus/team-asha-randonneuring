# Scheduled maintenance on Railway

The recurring Team Asha jobs no longer run on GitHub Actions.  GitHub workflow
files retain `workflow_dispatch` for an emergency/manual run, but have no
`schedule` trigger and therefore consume no scheduled-run minutes.

The existing Railway `poller` service now starts `worker/service.py`.  That
process runs the one-minute Garmin poller and a lightweight UTC scheduler for
the maintenance jobs:

| UTC schedule | Job |
| --- | --- |
| `:15` every hour | Route weather prefetch |
| `00:00, 06:00, 12:00, 18:00` | Strava sync/backfill |
| `06:00` daily | Finalize past rides |
| `07:00` daily | Sync RUSA finish times |
| `12:45` daily | Warm plan elevation |
| `13:00` daily | Update RUSA events (direct database importer) |

## Railway variables

Keep the existing live-poll variables and add:

| Variable | Value |
| --- | --- |
| `APP_BASE_URL` | `https://team-asha-randonneuring.vercel.app` |
| `CRON_SECRET` | The same bearer secret configured on the Team Asha Vercel project |
| `DATABASE_URL` | The Supabase pooler URL used by `scripts/update_rusa_events.py` |

The service already needs `POLL_URL`, `CRON_SECRET`, and optionally
`BREVETHUB_POLL_URL`/`BREVETHUB_CRON_SECRET` for live tracking; no poller
variables were removed.  Railway's Nixpacks build installs `requirements.txt`
so the direct RUSA importer has its existing dependencies.

## Deploying the change

From the linked Railway project:

```sh
railway up
```

After deployment, check the service logs for both lines:

```text
[poll-loop] starting — every 60s -> team-asha
[railway-scheduler] starting for https://team-asha-randonneuring.vercel.app; schedules are evaluated in UTC
```

The scheduler does not run jobs immediately on boot.  It waits for the next
scheduled UTC minute, prevents duplicate concurrent runs, and logs the HTTP
response or importer exit status.  A failed job is isolated and does not stop
live tracking or the other scheduled jobs.
