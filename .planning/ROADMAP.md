# Roadmap: Wind Forecast Integration

## Overview

Seven phases build from pure service-layer math up through full user-visible wind data. Phases 1-2 lay a verified computational foundation (no UI, no new API calls) before any user-facing work begins. Phases 3-5 deliver forecast wind across all three planning surfaces (base plan, warning banner, custom plan). Phases 6-7 close the loop with historical wind persistence and the Strava analysis display. Each phase delivers one coherent, testable capability before the next begins.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Wind Math Foundation** - Pure Python wind classification, color/intensity helpers, and named threshold constants in services/weather.py
- [ ] **Phase 2: Stop-to-Coordinate Interpolation** - RWGPS track point interpolation that resolves lat/lng for every ride plan stop
- [ ] **Phase 3: Forecast Wind in Base Ride Plan** - Color-coded wind columns visible in the base ride plan control sheet
- [ ] **Phase 4: Heavy Wind Warning Banner** - "Heavy Winds" warning banner on the upcoming brevets page for rides in the next 28 days
- [ ] **Phase 5: Forecast Wind in Custom Ride Plan** - Wind columns extended to custom ride plan views with correct merged stop resolution
- [ ] **Phase 6: Historical Wind — Archive API and DB Persistence** - Archive API fetch with 5-day fallback, ride_wind_data table, one-time persist per completed ride
- [ ] **Phase 7: Historical Wind Display and Ride Header Links** - Actual wind columns in Strava analysis and clickable 2025/2026 season ride headers

## Phase Details

### Phase 1: Wind Math Foundation
**Goal**: Correct wind classification, color intensity, and shared threshold constants exist as unit-tested service functions before any user-facing work begins
**Depends on**: Nothing (first phase)
**Requirements**: WIND-01, WIND-02, WIND-03, WIND-04, WIND-10
**Success Criteria** (what must be TRUE):
  1. Given any wind speed and rider bearing, the system returns the correct wind type (headwind / tailwind / crosswind) using the 45-degree threshold rule
  2. Given a wind speed, the system returns the correct hex color (#16A34A / #DC2626 / #2563EB) with correctly computed rgba opacity
  3. Given a wind speed, the system returns the correct font size (0.75rem / 0.875rem / 1.0rem) matching the three speed bands
  4. HEAVY_WIND_MAX_KMH and HEAVY_WIND_AVG_HEADWIND_KMH constants are defined once in services/weather.py and imported everywhere they are used
  5. Crosswind sine projection correctly inverts the meteorological "wind from" direction by 180 degrees before computing the projection
**Plans**: TBD

### Phase 2: Stop-to-Coordinate Interpolation
**Goal**: Every ride plan stop can be resolved to a lat/lng coordinate via RWGPS track point interpolation, with correct unit handling
**Depends on**: Phase 1
**Requirements**: WIND-05
**Success Criteria** (what must be TRUE):
  1. Given a ride plan with stops at known mile markers, get_stop_coordinates() returns a lat/lng for each stop that matches the RWGPS track at that distance
  2. A stop at 40.0 miles is placed within 0.5 km of the correct track position (not 40 meters — the miles-to-meters unit conversion is correct)
  3. Stops beyond the end of the track (rounding) are clamped to the final track point rather than returning an error
**Plans**: TBD

### Phase 3: Forecast Wind in Base Ride Plan
**Goal**: Riders viewing a base ride plan control sheet see a color-coded wind column at every stop, fetched from Open-Meteo via a single batched API call
**Depends on**: Phase 2
**Requirements**: WIND-06, BPLN-01, BPLN-02, BPLN-03, BPLN-04, BPLN-05, BPLN-06
**Success Criteria** (what must be TRUE):
  1. The base ride plan page shows a Wind column alongside existing stop columns, populated with wind speed text at each stop
  2. Each wind cell has a colored background (green / red / blue) whose opacity visibly varies between light, medium, and strong wind speeds
  3. Wind cell text is visibly smaller for light winds and larger for strong winds (three-step font scale)
  4. The wind column is absent (no empty column, no error) when wind data is unavailable for a route
  5. A wind legend below the table explains the green / red / blue color coding
  6. Viewing the same ride plan twice does not trigger a second Open-Meteo API call (1-hour cache active)
**Plans**: TBD

### Phase 4: Heavy Wind Warning Banner
**Goal**: Riders scanning the upcoming brevets page see a prominent warning when any brevet in the next 28 days has forecast heavy winds, so they can prepare before committing to a start
**Depends on**: Phase 3
**Requirements**: WARN-01, WARN-02, WARN-03, WARN-04
**Success Criteria** (what must be TRUE):
  1. A "Heavy Winds" banner appears at the top of the upcoming brevets page when at least one brevet within 28 days has max wind > 30 km/h or average headwind > 15 km/h along its route
  2. The banner names the affected brevet, its date, and includes a plain-language description (e.g., "Strong headwinds expected — avg 18 km/h headwind, gusts to 35 km/h")
  3. No banner appears for brevets more than 28 days away or for brevets without a linked ride plan
  4. The page renders without error when no upcoming brevets have heavy winds
**Plans**: TBD

### Phase 5: Forecast Wind in Custom Ride Plan
**Goal**: Riders viewing a custom ride plan see the same wind columns as the base plan, with wind correctly resolved for the merged stop list (base stops plus rider overrides)
**Depends on**: Phase 3
**Requirements**: WIND-09, CPLN-01, CPLN-02
**Success Criteria** (what must be TRUE):
  1. The custom ride plan view shows a wind column with the same green / red / blue color coding as the base plan
  2. Wind data is present for rider-added stops (not just base stops)
  3. Hidden stops do not produce a wind cell in the custom plan table
  4. Archive API responses with a single location (dict) and multiple locations (list) both render correctly without TypeError
**Plans**: TBD

### Phase 6: Historical Wind — Archive API and DB Persistence
**Goal**: Historical wind for completed rides is fetched once from the Open-Meteo archive API, persisted to the ride_wind_data table, and never re-fetched on subsequent page loads
**Depends on**: Phase 2
**Requirements**: WIND-07, WIND-08, STOR-01, STOR-02, STOR-03
**Success Criteria** (what must be TRUE):
  1. The ride_wind_data table exists with all required columns including data_source; a migration script creates it idempotently
  2. Fetching historical wind for a completed ride stores one row per stop with the correct wind values and data_source ('archive' or 'forecast_past_days')
  3. A second request for the same ride's wind reads from the database — the archive API is not called again
  4. For rides completed within the past 5 days, the system automatically falls back to the forecast API past_days parameter and stores data_source as 'forecast_past_days'
  5. A ride completed 10 days ago returns archive data; a ride completed 3 days ago returns forecast_past_days data
**Plans**: TBD

### Phase 7: Historical Wind Display and Ride Header Links
**Goal**: Riders viewing their Strava analysis see "Actual Wind" columns for completed 2026 rides, and 2025/2026 season ride names link directly to ride detail pages
**Depends on**: Phase 6
**Requirements**: HIST-01, HIST-02, HIST-03, HIST-04, LINK-01, LINK-02
**Success Criteria** (what must be TRUE):
  1. The Strava analysis section for a completed 2026 ride shows an "Actual Wind" column labeled as such (not "Forecast") with the same green / red / blue intensity format as ride plans
  2. Rides without a linked ride plan or RWGPS route show no wind column and no error
  3. 2025 and 2026 season ride names that have a linked ride plan appear as clickable links to the ride detail page
  4. Ride names without a linked ride plan remain plain text (no broken links)
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Wind Math Foundation | 0/TBD | Not started | - |
| 2. Stop-to-Coordinate Interpolation | 0/TBD | Not started | - |
| 3. Forecast Wind in Base Ride Plan | 0/TBD | Not started | - |
| 4. Heavy Wind Warning Banner | 0/TBD | Not started | - |
| 5. Forecast Wind in Custom Ride Plan | 0/TBD | Not started | - |
| 6. Historical Wind — Archive API and DB Persistence | 0/TBD | Not started | - |
| 7. Historical Wind Display and Ride Header Links | 0/TBD | Not started | - |
