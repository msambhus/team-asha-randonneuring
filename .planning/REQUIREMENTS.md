# Requirements: Wind Forecast Integration

**Defined:** 2026-03-23
**Core Value:** Riders can see wind conditions — forecast and historical — integrated directly into ride plans for informed preparation decisions

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Wind Service

- [x] **WIND-01**: System calculates crosswind component using sine projection of wind angle relative to rider bearing
- [x] **WIND-02**: System classifies wind at each stop as headwind, tailwind, or crosswind based on 45-degree threshold (|headwind| > |crosswind| → head/tailwind; else → crosswind)
- [x] **WIND-03**: System returns wind color (green=#16A34A tailwind, red=#DC2626 headwind, blue=#2563EB crosswind) with intensity scaling based on wind speed
- [x] **WIND-04**: System returns font size scaling based on wind speed (0-5 km/h = 0.75rem, 5-15 = 0.875rem, 15+ = 1.0rem)
- [x] **WIND-05**: System interpolates lat/lng coordinates for each ride plan stop by matching cumulative distance against RWGPS track points (converting miles to meters at boundary)
- [x] **WIND-06**: System fetches wind forecast for interpolated stop coordinates via Open-Meteo batch API with 1-hour cache
- [x] **WIND-07**: System fetches historical wind data via Open-Meteo archive API with start_date/end_date parameters
- [x] **WIND-08**: System falls back to forecast API `past_days` parameter when archive API returns no data for rides within 5 days (ERA5 reanalysis lag)
- [x] **WIND-09**: System normalizes single-location (dict) and multi-location (list) archive API responses identically to existing forecast normalization
- [x] **WIND-10**: Wind thresholds defined as named constants in services/weather.py (HEAVY_WIND_MAX_KMH=30, HEAVY_WIND_AVG_HEADWIND_KMH=15)

### Wind Storage

- [x] **STOR-01**: System stores historical wind data in `ride_wind_data` table (ride_id, stop_order, stop_name, wind_speed_kmh, wind_direction_deg, headwind_kmh, crosswind_kmh, wind_type, temperature_c, conditions, data_source, fetched_at)
- [x] **STOR-02**: System checks `ride_wind_data` table before fetching from archive API; only fetches if no existing data for that ride
- [x] **STOR-03**: System stores `data_source` as 'archive' or 'forecast_past_days' to track provenance

### Base Plan Wind Display

- [x] **BPLN-01**: User sees wind column in base ride plan detail page showing wind speed at each stop
- [x] **BPLN-02**: Wind cells have green background for tailwind, red for headwind, blue for crosswind
- [x] **BPLN-03**: Wind cell background color opacity scales with wind speed (light 0-5, medium 5-15, strong 15+ km/h)
- [x] **BPLN-04**: Wind cell font size scales with wind speed (small for light, medium for moderate, large for strong)
- [x] **BPLN-05**: Wind column only renders when wind data is available (graceful degradation)
- [x] **BPLN-06**: Wind legend section explains green=tailwind, red=headwind, blue=crosswind color coding

### Custom Plan Wind Display

- [x] **CPLN-01**: User sees wind columns in custom ride plan view with same color coding as base plan
- [x] **CPLN-02**: Custom stop positions correctly interpolated on route (including rider-added stops and hidden stops)

### Upcoming Brevets Warnings

- [x] **WARN-01**: User sees "Heavy Winds" warning banner at top of upcoming brevets page when winds are significant
- [x] **WARN-02**: Wind warnings only calculated for rides in the next 28 days with linked ride plans
- [x] **WARN-03**: Warning triggers when max wind speed > 30 km/h OR average headwind > 15 km/h along route
- [x] **WARN-04**: Warning shows ride name, date, and wind description (e.g., "Strong headwinds expected — avg 18 km/h headwind, gusts to 35 km/h")

### Historical Wind / Strava Analysis

- [ ] **HIST-01**: System pulls actual wind data for completed 2026 rides that have linked ride plans with RWGPS routes
- [ ] **HIST-02**: User sees wind conditions in Strava analysis section with same column format as ride plans
- [ ] **HIST-03**: Historical wind uses same green/red/blue color coding with intensity and font scaling
- [ ] **HIST-04**: Historical wind columns labeled "Actual Wind" (not "Forecast")

### Ride Header Links

- [x] **LINK-01**: 2025/2026 season ride names in rider profile link to ride detail pages
- [x] **LINK-02**: Only rides with linked ride plans show as clickable links; others remain plain text

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Chat Integration

- **CHAT-01**: Chat agent answers "will it be windy Saturday?" by querying ride plan wind data
- **CHAT-02**: Chat agent includes wind context in coaching advice for upcoming rides

### Extended Historical

- **EXTH-01**: Wind data for pre-2025 rides (subject to Open-Meteo archive availability)
- **EXTH-02**: Wind trend analysis across rides (is headwind getting stronger over a season?)

## Out of Scope

| Feature | Reason |
|---------|--------|
| Real-time wind updates during rides | Requires push infrastructure + mobile-first redesign; riders can check weather apps mid-ride |
| Animated wind arrows on map | Requires mapping library (Leaflet/Mapbox) + JS build step; incompatible with Flask/Jinja2 stack |
| User-configurable wind thresholds | Adds settings UI complexity; 30/15 km/h thresholds match community norms (75% of cyclists avoid >30 km/h) |
| Precipitation/temperature columns in plan table | Existing weather button already covers this; adding would create visual noise |
| Wind for rides without RWGPS routes | No track points = no coordinate interpolation = no per-stop wind possible |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| WIND-01 | Phase 1 | Complete |
| WIND-02 | Phase 1 | Complete |
| WIND-03 | Phase 1 | Complete |
| WIND-04 | Phase 1 | Complete |
| WIND-05 | Phase 2 | Complete |
| WIND-06 | Phase 3 | Complete |
| WIND-07 | Phase 6 | Complete |
| WIND-08 | Phase 6 | Complete |
| WIND-09 | Phase 5 | Complete |
| WIND-10 | Phase 1 | Complete |
| STOR-01 | Phase 6 | Complete |
| STOR-02 | Phase 6 | Complete |
| STOR-03 | Phase 6 | Complete |
| BPLN-01 | Phase 3 | Complete |
| BPLN-02 | Phase 3 | Complete |
| BPLN-03 | Phase 3 | Complete |
| BPLN-04 | Phase 3 | Complete |
| BPLN-05 | Phase 3 | Complete |
| BPLN-06 | Phase 3 | Complete |
| CPLN-01 | Phase 5 | Complete |
| CPLN-02 | Phase 5 | Complete |
| WARN-01 | Phase 4 | Complete |
| WARN-02 | Phase 4 | Complete |
| WARN-03 | Phase 4 | Complete |
| WARN-04 | Phase 4 | Complete |
| HIST-01 | Phase 7 | Pending |
| HIST-02 | Phase 7 | Pending |
| HIST-03 | Phase 7 | Pending |
| HIST-04 | Phase 7 | Pending |
| LINK-01 | Phase 7 | Complete |
| LINK-02 | Phase 7 | Complete |

**Coverage:**
- v1 requirements: 31 total
- Mapped to phases: 31
- Unmapped: 0

---
*Requirements defined: 2026-03-23*
*Last updated: 2026-03-23 after roadmap creation*
