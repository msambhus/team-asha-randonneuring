# Live tracking poller on Railway

GitHub Actions' scheduled cron is throttled to ~45 minutes, which is too slow to
feel "live." Instead, run `worker/poll_loop.py` as a small always-on **Railway**
service. It loops every ~60 s and triggers the existing, tested ingest endpoint —
so Garmin/beacon positions land within about a minute.

It only calls the public endpoint (`POST /api/cron/poll-garmin-livetrack`); it
never touches the database, and it's stdlib-only (no extra dependencies).

## One-time setup

1. **New Railway service from this repo**
   - Railway dashboard → your project → **New → GitHub Repo** → pick this repo.
   - (Railway reads `railway.json`, so the start command — `python worker/poll_loop.py` —
     is already configured. If you'd rather set it by hand: service **Settings →
     Deploy → Start Command** = `python worker/poll_loop.py`.)

2. **Set the service variables** (service → **Variables**):

   | Variable | Value |
   |---|---|
   | `POLL_URL` | `https://team-asha-randonneuring.vercel.app/api/cron/poll-garmin-livetrack` |
   | `CRON_SECRET` | **the same secret** the Vercel app uses (copy from Vercel → project → Settings → Environment Variables) |
   | `POLL_INTERVAL_SECONDS` | `60` (optional; floor is 15) |

3. **Deploy.** Watch the service **Logs** — you should see lines like:
   ```
   [poll-loop] starting — every 60s -> https://.../poll-garmin-livetrack
   [poll-loop] HTTP 200 polled=1 inserted=3 errors=0
   ```
   `inserted` > 0 whenever a tracked rider has new points; `polled=0` just means
   nobody currently has an active Garmin ride linked.

## After it's running

- **Turn off the GitHub Actions schedule** so the two don't both poll. This PR
  already comments out the `schedule:` trigger in
  `.github/workflows/poll-livetrack.yml` (the manual **Run workflow** button still
  works as a fallback). Re-enable it if you ever retire the Railway worker.
  (Double-polling is harmless — inserts are idempotent — but it's wasted work.)

- **Cost:** this is a minimal always-on container doing one tiny HTTP request a
  minute; it sits near Railway's smallest usage tier.

## How it fits together

```
Railway worker (always-on)              Vercel (Flask)            Supabase
  every ~60s:                           POST /api/cron/
    POST $POLL_URL          ───────▶    poll-garmin-livetrack ──▶ rider_live_position
    Authorization: Bearer $CRON_SECRET    (fetch Garmin, tag        (ride_id-tagged)
                                           points to active ride)
```

The worker is deliberately dumb — all the real logic (auth, Garmin scrape,
per-ride tagging, dedup, purge) stays in the one tested endpoint.
