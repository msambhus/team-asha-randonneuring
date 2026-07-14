# BrevetHub

A **club-agnostic, fully de-branded** randonneuring web app that lives in this
monorepo alongside the Team Asha app but shares **no data** with it. BrevetHub
reads and writes **only** the `rp_*` tenant tables and imports club-agnostic
logic from the sibling [`shared/`](../shared) package. It never imports Team
Asha's `app`, `models`, `routes`, `db`, or `config`.

This is **Mission 1 — the foundation slice**.

## What's in this slice

- Standalone Flask app (`brevethub/app.py`, own `create_app()` factory).
- Neutral, minimal theme (`static/style.css`, plain CSS — no Tailwind build).
- Google OAuth sign-in (reuses Team Asha's **existing** Google OAuth client).
- Post-login signup: optional RUSA ID (shape-checked, not verified) + club picker.
- Club directory (`rp_club`) seeded from RUSA's official club list.
- Multi-tenant `rp_*` schema (migration `033`).
- Its own `vercel.json` for a standalone Vercel deployment.

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

No secrets are committed — `.env` is git-ignored and the README uses placeholders.

## Deploy (post-merge; out of this `pr-only` mission, documented for the owner)

> **Note on `shared/`:** this Mission-1 slice does **not** import `shared/` yet —
> it only needs the files under `brevethub/`. So the deploy has no dependency on
> the sibling package, and the entry point (`api/index.py`) is written to work
> whether or not the repo root is present in the deployment (see `test_deploy_entrypoint.py`).
> When a later mission actually imports `shared.*`, enable Vercel's **"Include
> files outside the Root Directory in the Build Step"** so the sibling package is
> bundled.

1. **Apply the migration** `migrations/033_brevethub_rp_tables.sql` to Supabase.
   It is additive/idempotent and touches no Team Asha table. *(Applied 2026-07-14.)*
2. **Create a new Vercel project** on this same repo (Vercel supports a second
   project in one repo). Set its **Root Directory** to `brevethub`. The entry
   point self-heals under both layouts, so the "include files outside root"
   toggle is optional for Mission 1 (turn it on once a mission imports `shared/`).
3. **Set env vars** on the new project (Production + Preview):
   - `DATABASE_URL` — same Supabase Postgres as Team Asha (BrevetHub only touches `rp_*`).
   - `BREVETHUB_SECRET_KEY` — a fresh 32+ char random value (its own, not Team Asha's).
   - `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` — reused from Team Asha's existing OAuth client.
4. **Register the Google redirect URIs** below on the existing OAuth client.

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

## Deferred to follow-on missions (out of scope for this slice)

- **Mission 2** — RUSA brevet-history scraping + per-rider stats UI
  (`shared.rusa` is ready; only the BrevetHub route/UI is deferred). Strava
  OAuth connect + per-rider Strava stats (`rp_strava_connection` created empty).
- **Mission 3** — public live-ride browse shell + `rp_live_position` ingestion
  (`rp_ride.is_public` flag created now). The `services/live_telemetry.py` →
  `shared/live_telemetry.py` extraction (including lifting `_compute_difficulty_score`
  into `shared/route_scoring.py`) is deferred to this mission — it transitively
  imports Flask via `services.rwgps`, so moving it now would break the `shared/`
  standalone contract.
