# BrevetHub

A **club-agnostic, fully de-branded** randonneuring web app that lives in this
monorepo alongside the Team Asha app but shares **no data** with it. BrevetHub
reads and writes **only** the `rp_*` tenant tables and imports club-agnostic
logic from the sibling [`shared/`](../shared) package. It never imports Team
Asha's `app`, `models`, `routes`, `db`, or `config`.

This is **Mission 1 — the foundation slice**, extended by **Mission 2** (per-rider
RUSA stats + Strava connect — see below).

## What's in this slice

- Standalone Flask app (`brevethub/app.py`, own `create_app()` factory).
- Neutral, minimal theme (`static/style.css`, plain CSS — no Tailwind build).
- Google OAuth sign-in (reuses Team Asha's **existing** Google OAuth client).
- Post-login signup: optional RUSA ID (shape-checked, not verified) + club picker.
- Club directory (`rp_club`) seeded from RUSA's official club list.
- Multi-tenant `rp_*` schema (migration `033`).
- Its own `vercel.json` for a standalone Vercel deployment.

## Mission 2 — RUSA stats + Strava connect

The dashboard now renders the first two deferred features (the third, public
live-ride tracking, stays "Coming soon"):

- **RUSA brevet history + stats.** If the rider has a RUSA ID, their history is
  scraped via `shared.rusa` (the same logic Team Asha uses — no duplication),
  cached on `rp_rider` (`rusa_cache` JSONB + `rusa_fetched_at`, 7-day TTL), and
  shown as a history table plus summary stats (total km, per-band counts
  200/300/400/600/1000, SR status, longest, current-season totals). A
  `POST /rusa/refresh` forces a re-scrape. No RUSA ID shows an add-ID prompt;
  scrape failures degrade to cached/empty state with a message — never a 500.
- **Strava connect + per-rider fitness.** A "Connect Strava" OAuth2 flow stores
  tokens in `rp_strava_connection` and shows a 28-day activity summary (rides,
  distance, elevation, moving time) plus a fitness score (`shared.fitness`),
  cached 6 hours. "Disconnect Strava" revokes the token and deletes the row. The
  club-agnostic Strava HTTP layer lives in `shared/strava.py`; Team Asha's
  `services/strava.py` re-exports it through a shim (its epoch `INTEGER` path
  untouched). Migration `034` adds the cache columns (`rp_*` only).

**Security — Strava OAuth CSRF `state`.** `/strava/connect` mints a per-flow
`secrets.token_urlsafe(32)` state, stores it in the session, and echoes it to
Strava; `/strava/callback` validates it with a constant-time compare **before**
any code exchange or DB write, and clears the flow's session keys on every
terminal path. (Team Asha's own flow is state-less; BrevetHub deliberately
hardens past it.)

**`expires_at` conversion.** `shared/strava.py` is epoch-native (Unix integers,
as Strava returns them), but `rp_strava_connection.expires_at` is `TIMESTAMPTZ`.
`brevethub/models.py` owns the bridge: writes use `to_timestamp(%s)`, the getter
returns the value back as an epoch float — so route/service code never compares a
bare `datetime` to `time.time()`.

## Architecture

| Concern | BrevetHub | Isolation guarantee |
|---|---|---|
| Data | `rp_*` tables only | `tests/brevethub/test_rp_only.py` scans `models.py` |
| Code | imports `brevethub.*` + `shared.*` only | `tests/brevethub/test_brevethub_isolation.py` |
| Shared lib | `shared/` is standalone (no Flask, no `services.*`) | `tests/brevethub/test_shared_isolation.py` |

## Local development

```bash
# From the repo root, with the app's dependencies available:
cp brevethub/.env.example brevethub/.env   # then fill in values
python3 -m brevethub.app                    # serves on http://localhost:5001
```

Environment variables (BrevetHub's own namespace):

| Var | Purpose |
|---|---|
| `DATABASE_URL` | Same Supabase Postgres as Team Asha (BrevetHub only touches `rp_*`) |
| `BREVETHUB_SECRET_KEY` | Flask session secret (own key; falls back to `SECRET_KEY` in dev) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | **Reused** from Team Asha's existing OAuth client |
| `STRAVA_CLIENT_ID` | Strava app client id (**default `113090`** — Team Asha's app; override only for a separate app) |
| `STRAVA_CLIENT_SECRET` | Strava app client secret — **required for Strava connect**; unset = the button flashes a config message, no crash |
| `CRON_SECRET` | Bearer secret for the scheduled calendar refresh (`/cron/refresh-calendar`). Unset = the cron endpoint 500s (never scrapes unauthenticated). Its own value, separate from Team Asha's. |

No secrets are committed — `.env` is git-ignored and the README uses placeholders.

The daily Vercel cron (`brevethub/vercel.json` → `/cron/refresh-calendar`, `0 8 * * *`)
scrapes the RUSA national calendar off the request path and upserts `rp_brevet_event`,
so `/calendar` only reads the warm cache (it never blocks on the heavy scrape). The
cache is seeded on the first `/calendar` load when still empty; the maintainer can also
warm it immediately with `curl -H "Authorization: Bearer $CRON_SECRET" .../cron/refresh-calendar`.

## Deploy (post-merge; out of this `pr-only` mission, documented for the owner)

> **Note on `shared/` (changed in Mission 2):** BrevetHub now imports `shared.*`
> (`shared/rusa.py`, `shared/strava.py`, `shared/fitness.py`). Enable Vercel's
> **"Include files outside the Root Directory in the Build Step"** on the
> BrevetHub project so the sibling package is bundled. The entry point
> (`api/index.py`) already self-heals under both layouts (see
> `test_deploy_entrypoint.py`); the toggle is what makes `shared/` present.

1. **Apply the migrations** `migrations/033_brevethub_rp_tables.sql` *(applied
   2026-07-14)* **and** `migrations/034_brevethub_rusa_strava_cache.sql` to
   Supabase. Both are additive/idempotent and touch no Team Asha table.
2. **Create a new Vercel project** on this same repo (Vercel supports a second
   project in one repo). Set its **Root Directory** to `brevethub`. The entry
   point self-heals under both layouts, so the "include files outside root"
   toggle is optional for Mission 1 (turn it on once a mission imports `shared/`).
3. **Set env vars** on the new project (Production + Preview):
   - `DATABASE_URL` — same Supabase Postgres as Team Asha (BrevetHub only touches `rp_*`).
   - `BREVETHUB_SECRET_KEY` — a fresh 32+ char random value (its own, not Team Asha's).
   - `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` — reused from Team Asha's existing OAuth client.
   - `STRAVA_CLIENT_ID` / `STRAVA_CLIENT_SECRET` — **recommended: reuse Team Asha's
     Strava app (`113090`).** Strava allows multiple redirect URIs per app, so one
     app serves both; set the *same* client id + secret Team Asha uses. (Only create
     a separate Strava app if you want the two apps' rate limits isolated — then set
     a different `STRAVA_CLIENT_ID`.)
4. **Register the Google redirect URIs** and the **Strava callback URL** below.
5. **Enable "Include files outside the Root Directory in the Build Step"** on the
   BrevetHub Vercel project (Mission 2 imports `shared/`).

### Google redirect URIs to register (on the existing OAuth client)

Add these Authorized redirect URIs in Google Cloud Console → the existing
OAuth 2.0 Client:

```
https://<brevethub-domain>.vercel.app/auth/google/callback
http://localhost:5001/auth/google/callback
```

`<brevethub-domain>` is the Vercel project's production domain — it depends on
the project name chosen at deploy time (**open question**; `brevethub` is used as
a placeholder here). Update the production URI once the domain is assigned.

### Strava callback URL to register (on the Strava app)

In the Strava app settings (API → Authorization Callback Domain / redirect URIs),
register the exact BrevetHub callback path:

```
https://brevethub.vercel.app/strava/callback
http://localhost:5001/strava/callback
```

Strava's "Authorization Callback Domain" field takes just the host
(`brevethub.vercel.app`); the full path above is what the app redirects to. If the
production domain differs from `brevethub.vercel.app`, register that host instead.

## Deferred to follow-on missions (out of scope for this slice)

- **Mission 3** — public live-ride browse shell + `rp_live_position` ingestion
  (`rp_ride.is_public` flag created now). The `services/live_telemetry.py` →
  `shared/live_telemetry.py` extraction (including lifting `_compute_difficulty_score`
  into `shared/route_scoring.py`) is deferred to this mission — it transitively
  imports Flask via `services.rwgps`, so moving it now would break the `shared/`
  standalone contract.
