# Roadmap: Team Asha Randonneuring

## Milestones

- ✅ **v1.0 Wind Forecast Integration** — Phases 1-7 (shipped 2026-03-23)

## Phases

<details>
<summary>✅ v1.0 Wind Forecast Integration (Phases 1-7) — SHIPPED 2026-03-23</summary>

- [x] Phase 1: Wind Math Foundation (1/1 plans) — completed 2026-03-23
- [x] Phase 2: Stop-to-Coordinate Interpolation (1/1 plans) — completed 2026-03-23
- [x] Phase 3: Forecast Wind in Base Ride Plan (2/2 plans) — completed 2026-03-23
- [x] Phase 4: Heavy Wind Warning Banner (2/2 plans) — completed 2026-03-23
- [x] Phase 5: Forecast Wind in Custom Ride Plan (1/1 plans) — completed 2026-03-23
- [x] Phase 6: Historical Wind — Archive API + DB (2/2 plans) — completed 2026-03-23
- [x] Phase 7: Historical Wind Display + Links (2/2 plans) — completed 2026-03-23

Full details: [v1.0-ROADMAP.md](milestones/v1.0-ROADMAP.md)

</details>

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Wind Math Foundation | v1.0 | 1/1 | Complete | 2026-03-23 |
| 2. Stop-to-Coordinate Interpolation | v1.0 | 1/1 | Complete | 2026-03-23 |
| 3. Forecast Wind in Base Ride Plan | v1.0 | 2/2 | Complete | 2026-03-23 |
| 4. Heavy Wind Warning Banner | v1.0 | 2/2 | Complete | 2026-03-23 |
| 5. Forecast Wind in Custom Ride Plan | v1.0 | 1/1 | Complete | 2026-03-23 |
| 6. Historical Wind — Archive API + DB | v1.0 | 2/2 | Complete | 2026-03-23 |
| 7. Historical Wind Display + Links | v1.0 | 2/2 | Complete | 2026-03-23 |
| 8. Weather/Wind Forecasting | — | 0/2 | Planned | — |
| 9. WhatsApp Knowledge Priority | — | 0/1 | Planned | — |
| 10. Multi-Rider Strava Analysis | — | 1/2 | In progress | — |

### Phase 7: RWGPS Route Intelligence
**Goal:** When the user asks about a route, the chatbot resolves the ride name to a RWGPS route ID, checks for a cached ride plan first, and if none exists, fetches live route data from the RWGPS API -- providing elevation profile, distance, control points, and key segments grounded in real route data, not generic advice
**Depends on:** Phase 6
**Requirements**: RWGPS-01, RWGPS-02, RWGPS-03, RWGPS-04, RWGPS-05, RWGPS-06, RWGPS-07
**Success Criteria** (what must be TRUE):
  1. Asking "Tell me about the Cascade 400" with no cached ride plan triggers a live RWGPS API fetch and returns elevation, distance, control stops, and key segment data
  2. Asking about a route that HAS a cached ride plan returns the cached data without calling the RWGPS API
  3. RWGPS API errors (404, 401, 429, timeout) produce user-friendly messages, not crashes
  4. RWGPS responses are cached in-memory for 5 minutes to avoid duplicate API calls within a chat session
  5. The intent classification prompt describes route_discussion as capable of live RWGPS data access
**Plans**: 2 plans

Plans:
- [ ] 07-01-PLAN.md — Route data functions (TDD): get_ride_rwgps_url SQL query, summarize_route_for_chat(), fetch_and_summarize_route() with caching and error handling
- [ ] 07-02-PLAN.md — Agent loop wiring: extend route_discussion branch with live RWGPS fallback, update intent classification prompt

### Phase 8: Weather and wind forecasting for routes — use RandoPlan-style data to answer about headwinds, tailwinds, temperature, and conditions along a route

**Goal:** When a user asks about weather conditions for a specific route, the chatbot fetches route geometry from RWGPS, samples coordinates along the route, makes a single batched Open-Meteo API call for hourly forecasts, computes headwind/tailwind components from bearing math, and presents a structured segment-by-segment weather summary with arrival-time-adjusted forecasts
**Depends on:** Phase 7
**Requirements**: WTHR-01, WTHR-02, WTHR-03, WTHR-04, WTHR-05, WTHR-06, WTHR-07, WTHR-08, WTHR-09, WTHR-10
**Success Criteria** (what must be TRUE):
  1. Asking "What's the weather for the Cascade 400?" triggers a `weather_query` intent and returns a segment-by-segment forecast with temperature, wind, and precipitation for each section of the route
  2. Wind analysis includes headwind/tailwind assessment per segment using bearing math and meteorological wind direction convention
  3. Forecasts are time-adjusted: the weather at km 300 uses the estimated arrival time (T+24h for a 400km ride), not current-hour weather
  4. Open-Meteo is called with a single batched multi-coordinate request (not one call per point)
  5. Weather results are cached for 1 hour using Flask-Caching SimpleCache
  6. If Open-Meteo is unavailable or the route has no RWGPS track data, the chatbot responds with a clear explanation instead of crashing
**Plans**: 2 plans

Plans:
- [ ] 08-01-PLAN.md — Weather service module (TDD): route sampling, bearing math, headwind computation, Open-Meteo batch fetch, caching, response formatting
- [ ] 08-02-PLAN.md — Intent classification + agent loop integration: weather_query intent, execute_route_weather tool, RWGPS wiring

### Phase 9: Prioritize WhatsApp community knowledge in chatbot responses — attribute insights to the group, then compare and contrast with web search results

**Goal:** When both community knowledge (RAG) and web search results are available, the chatbot always presents community knowledge FIRST with explicit attribution ("Team member Venki mentioned..."), then compares/contrasts with web sources -- with clear source separation, contradiction framing, and named attribution throughout
**Requirements**: WA-PRI-01, WA-PRI-02, WA-PRI-03, WA-PRI-04, WA-PRI-05, WA-PRI-06, WA-PRI-07, WA-PRI-08
**Depends on:** Phase 8
**Plans:** 1 plan

Plans:
- [ ] 09-01-PLAN.md — Strengthen RAG injection instruction, add web-with-community instruction variant, update CHAT_SYSTEM_PROMPT with community-first priority, bump max_tokens for web_search

### Phase 10: Multi-rider Strava ride analysis — show all riders per ride, move plan toggle to admin

**Goal:** The Strava ride analysis page expands from single-rider to multi-rider -- a new page at `/ride/<ride_id>/all-strava` shows every FINISHED rider's cached analysis for a ride event with summary table and per-rider accordion, honoring privacy flags and using only cached data (no live Strava API calls). The base/custom plan toggle in ride_plan_detail.html becomes admin-only.
**Requirements**: MULTI-01, MULTI-02, MULTI-03, MULTI-04
**Depends on:** Phase 9
**Success Criteria** (what must be TRUE):
  1. GET `/ride/<ride_id>/all-strava` returns a page showing all FINISHED riders for that ride with their Strava analysis summaries
  2. Riders with `strava_data_private = True` are shown as "Analysis Private" -- no Strava data exposed
  3. Riders without cached `strava_ride_analysis` are shown as "Not Yet Analyzed" with a link to their individual page -- no live Strava API calls triggered
  4. Riders with cached analysis display comparison data (plan vs actual stops, summary metrics)
  5. The "Base Plan" toggle in `ride_plan_detail.html` is only visible to admin users; "View My Custom Plan" remains visible to all users with a custom plan
  6. The ride detail page links to the multi-rider analysis view
**Plans:** 2 plans

Plans:
- [x] 10-01-PLAN.md — Backend model function (get_finished_riders_for_ride), route handler (/ride/<ride_id>/all-strava), and tests
- [ ] 10-02-PLAN.md — Multi-rider template (summary table + per-rider accordion), admin-gate plan toggle, navigation links, human-verify checkpoint
