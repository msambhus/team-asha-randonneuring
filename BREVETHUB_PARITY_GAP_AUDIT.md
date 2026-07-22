# BrevetHub vs Team Asha — Feature Parity Gap Audit

Legend: **[MISSING]** = not built at all · **[SHALLOW]** = a token version, most depth absent · **[PRESENT]** = real parity.

BrevetHub today = a thin slice. Below is the concrete gap, per feature area. TA = Team Asha (repo root), BH = BrevetHub (`brevethub/`).

---

## 1. Live tracking & telemetry
BH live = guest browse of public rides + a single **anonymous** position trail on a token-free Leaflet map, fed only by a rider POSTing raw lat/lng. TA's engine is `services/live_telemetry.py` (~755 lines) + `routes/live.py` (1707) + `services/garmin_livetrack.py` + a poll cron.

- **Garmin LiveTrack connection + ingestion** — [MISSING]. No Garmin route, no share-page scraper, no `garmin_session_url/token` columns. (Your flag is exactly right.)
- **Background poller + 7-day retention purge** — [MISSING]. TA polls Garmin every ~3 min, downsamples, appends new fixes, purges old. BH has zero poller/purge — positions only ever arrive from direct API POSTs and are never cleaned up.
- **Telemetry fields on positions** — [SHALLOW]. `rp_live_position` stores only lat/lng/recorded_at. TA also stores accuracy, **speed, heart-rate, power, cadence, source**. BH can't show any of those.
- **Browser "beacon" phone-location streaming + sharing opt-in/consent** — [MISSING]. No beacon page, no sharing toggle, no consent gate.
- **Route/course polyline overlay on the map** — [MISSING]. BH draws only the trail (Leaflet/OSM, no Mapbox token, no RWGPS route line).
- **Multi-rider distinct dots (name, status color, pace color, staleness fade)** — [MISSING]. BH shows one anonymous blue trail; can't distinguish riders on a ride.
- **Plan-aware live telemetry** — [MISSING, entire engine]. None of: elapsed/moving/stopped time, distance done/remaining, route projection, on/off-route detection, plan delta / banked-time-vs-plan, banked-time-vs-cutoff (OTL margin), next control + ETA + required speed, time-left vs limit, grade/ascent split, headwind/crosswind done-vs-ahead, live elevation/wind/temp charts, avg/instant speed + HR/power/cadence readout.
- **Live plan selector (base/custom/own, IDOR-safe)** — [MISSING].
- **Auto-attach an inbound track to a calendar ride** — [MISSING] (BH requires a manually-created ride).
- **Mobile token-authed live/ride API** — [MISSING] (TA has a whole `/api/*` Bearer surface for its iOS app).
- PRESENT: owner-scoped self-posted position; guest public browse (BH uses an `is_public` flag vs TA's invite codes — different model).

---

## 2. Weather
BH weather = a **single start-point daily forecast badge** on calendar cards (temp range / wind / precip), warmed by `/cron/fetch-brevet-weather` into `rp_brevet_weather`. TA's `services/weather.py` (~1071 lines) is far deeper.

- **Along-route wind per segment/stop (headwind / tailwind / crosswind)** — [MISSING]. The signature TA feature. BH does one point only.
- **Wind-arrow SVG (direction in the rider's frame)** — [MISSING].
- **Per-stop forecast aligned to estimated arrival time** — [MISSING]. TA `fetch_stop_wind` samples the route every ~50km and forecasts each stop at the time you'll be there.
- **Weather map** (`/api/weather-map`, `weather.html`) — [MISSING].
- **Historical wind** (`get_historical_stop_wind`, Open-Meteo archive) — [MISSING].
- **Weather surfaced on ride-plan and analysis pages** — [MISSING] (TA shows it on `ride_plan_detail`, `strava_ride_analysis`, live).
- **Natural-language ride weather summary** (`generate_ride_summary`) — [MISSING].
- **Dedicated `/weather` page with speed/plan inputs** — [MISSING].
- PRESENT: keyless point forecast + cron cache (the one slice that shipped). ~1 of ~10 TA weather capabilities.

---

## 3. Ride analysis, AI coach & fitness
BH = per-activity analysis computed on demand and cached (`rp_ride_analysis`), reusing the extracted engine; fitness scoring module exists (`shared/fitness.py`).

- **AI ride coach** (`services/ride_coach.py` + `openai_coach.py`, gpt-4o) — [MISSING]. Deferred; needs `OPENAI_API_KEY` + de-branding the TA-branded prompt.
- **1-year Strava activity sync/storage** — [MISSING]. TA stores a year of `strava_activity` + streams; BH only fetches on-demand, stores nothing beyond a single analyzed activity.
- **Ride ↔ Strava activity auto-matching** (`strava_ride_match`) — [MISSING]. TA auto-links a brevet to its Strava activity by name/date; BH makes the rider pick.
- **Same-route history comparison** — [MISSING]. TA compares your ride to your past rides on the same route.
- **Cohort / multi-rider comparison** — [MISSING].
- **Brevet comparison page** (multi-ride chart/stats/distribution) — [MISSING].
- **Per-ride grading (A–F) surfaced** — [SHALLOW] (scoring logic present via fitness module; not surfaced like TA's ride pages).
- **Ride map + segment thumbnails depth** — [SHALLOW] vs TA.
- PRESENT: single-activity per-segment breakdown (cadence/NP/climb/gradient) on demand.

---

## 4. Ride planning
BH planning = one guest-facing pacing **table** (`/plan/<event_id>`) built on synthetic stops. The pacing *engine* is at parity (shared), but everything above the raw math is missing.

- **Base ride plans as real DB objects (ordered stops w/ location, type, elevation, notes)** — [MISSING]. TA has `ride_plan` + `ride_plan_stop` tables; BH's `rp_brevet_plan` stores only one rider's target speed + a JSON snapshot.
- **Plan catalog / index page** (`/ride-plans`) — [MISSING].
- **Plan detail richness** — [SHALLOW]. TA v2 = SVG elevation profile, per-segment difficulty coloring, pace strategies, heavy weather/wind. BH = flat 4-column table (distance / arrival / avg speed / time-bank). No elevation, weather, wind, difficulty.
- **RWGPS import → real controls + real elevation** — [MISSING]. TA `services/rwgps.py` parses actual route waypoints + per-segment elevation + gradient speed model. BH uses `_control_distances()` = **evenly-spaced every 100 km** and hardcodes **elevation = 0** for every stop (so difficulty is always flat, speed uniform).
- **Stop types / names / notes** — [MISSING]. BH stops are bare km markers (no start/control/rest typing, no location name, no notes).
- **Custom per-rider plans + base-plan inheritance/merge** — [MISSING]. TA `custom_ride_plan(_stop)` + `get_merged_plan_stops` (override/hide/inject stops). BH has no base-plan-a-rider-customizes concept.
- **Base plan editor + custom plan editor UIs** (add/hide/clone/delete stops, apply-pace-to-all, edit metadata) — [MISSING] (full REST suite in TA).
- **Plan comparison** (`/ride-plan/<slug>/compare`) — [MISSING].
- **Live plan tracking** (`/ride-plan/<slug>/live`, ETA vs controls) — [MISSING].
- **Plan sharing (public custom plans)** — [MISSING].
- **plan_match actually wired up** — [SHALLOW]. Module vendored into BH but **unused** by BH's planning flow (dead code there); TA uses it to resolve ride→plan by name pervasively.
- PRESENT: shared pacing engine (`recalculate_cumulative_values`, at parity — but fed impoverished inputs: no elevation, no rest durations, no per-segment pace); guest compute + a rider saving a scalar target.

## 5. Everything else — dashboard, chat/AI, stats, community, admin, auth

- **AI chat assistant** (7-intent classifier, `run_agent_loop`, 49-query allowlist, web search) + **conversation persistence** + **community-knowledge RAG** — [MISSING] (entire subsystem: `chat_service`, `chat_tools`, `conversation`/`chat_message`, `chat_widget`).
- **Admin console (~50 routes)** — [MISSING] entirely (ride CRUD, season admin, Strava admin, sync/finalize, plan generation, user management, etc.).
- **Coaching layer** — personality profiles, gear preferences, coaching guardrails, coach roster, per-ride AI readiness/advice — [MISSING].
- **Eddington number** (service, profile card, leaderboard column, admin recalc) — [MISSING].
- **Community surfaces** — rider directory (search), career leaderboard, per-season rosters, **public rider profiles** — [MISSING]. BH is self-profile-only; you can't view another rider.
- **Brevet / cohort comparison** (multi-ride chart/stats/distribution) — [MISSING].
- **SR & R-12** — [PRESENT for own profile]; [SHALLOW] no team-wide SR/R-12 standings, no per-award date detail (BH shows a count only).
- **Dashboard / landing richness** — [SHALLOW]. TA = animated hero, team count-up stats (riders/rides/kms/SRs), season storytelling, PBP callout. BH landing/dashboard are basic. Marketing pages (`/about`, `/resources`, `/privacy`) — [MISSING].
- **Ride sign-up lifecycle & result workflow** — [SHALLOW]. TA = full `RideStatus` (INTERESTED/MAYBE/GOING/WITHDRAW/FINISHED/DNF/DNS/OTL) + rider notes + per-ride edit. BH signup = a bare POST + a one-time profile form; no maybe/withdraw states, no DNF/OTL result tracking, no notes.
- **Auth breadth** — [MISSING]. TA = Google + **email OTP / passwordless / magic-link** + mobile token auth (`/apple`, `/demo`, `/google`) + **account deletion** (App-Store requirement). BH is Google-only, no ongoing profile editor/privacy toggles.
- **Notifications / transactional email** (`services/email_service.py`) — [MISSING].
- **RUSA results → ride reconciliation** (`/sync-rusa-results`, finalize past rides) — [SHALLOW]. BH refreshes calendar/history but doesn't reconcile official results back onto ride records.
- **FIT-file merge tool** (route + UI) — [MISSING] (BH vendors `shared/fit_merge.py` but exposes no page).
- **Badges/awards display, feedback capture (`/api/feedback`)** — [MISSING].
- **⚠️ SaaS inversion** — BH is multi-tenant (`rp_club`) but has **NO club-admin surface** (no club onboarding/edit, member management, or per-club admin role — clubs are seed-only, read-only). A capability BH *should* have that TA legitimately lacks.

---

## Scale & priorities

**Rough count: ~55–65 distinct gaps** — the majority [MISSING] entirely, the rest [SHALLOW]. Whole subsystems absent: AI chat + RAG, admin console (~50 routes), coaching layer, Eddington, community/directory/leaderboard, comparison, notifications, the live telemetry engine, Garmin, RWGPS-backed real plans, along-route weather.

Suggested build order (highest value / most-visible first):
1. **Live tracking depth** — Garmin LiveTrack ingestion + poller + telemetry fields + route overlay + plan-aware ETA/banked-time (your flagged gap; the biggest single engine, `live_telemetry.py`).
2. **RWGPS-backed real ride plans** — real controls + elevation + elevation profile + stop types (turns the pacing table into an actual plan). Needs RWGPS keys on Vercel.
3. **Along-route weather** — per-stop wind/forecast + wind arrows on plan/live pages. (Scope-B of what shipped.)
4. **Ride sign-up lifecycle** (interested/maybe/withdraw/DNF/OTL + notes) + **result reconciliation**.
5. **Community** — public rider profiles, directory, leaderboard, per-season rosters.
6. **AI ride coach** (needs OPENAI key + de-brand) and **AI chat assistant**.
7. **Eddington**, **brevet/cohort comparison**, **richer dashboard/landing + marketing pages**.
8. **Admin console** (club-scoped) + the **SaaS club-admin surface** + **notifications** + **email-OTP auth** + **account deletion**.

Most of these reuse a TA engine already (extract→shim→vendor), so each is a bounded mission — but there are *many*, and several need env keys (RWGPS, OpenAI, Mapbox).
