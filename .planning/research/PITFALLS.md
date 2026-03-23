# Pitfalls Research

**Domain:** Wind forecast & historical wind integration for a cycling randonneuring app
**Researched:** 2026-03-23
**Confidence:** HIGH (critical pitfalls verified against Open-Meteo GitHub issues, official docs, and codebase inspection)

---

## Critical Pitfalls

### Pitfall 1: Archive API Data Lag — Requesting Today or Yesterday

**What goes wrong:**
The Open-Meteo archive API (`/v1/archive`) uses ERA5 reanalysis data that is updated daily with a **5-day delay**. Calling the archive with `end_date=today` or `end_date=yesterday` for a ride that finished two days ago returns an HTTP 400 or silently returns empty/partial data, breaking historical wind display for recently completed rides.

**Why it happens:**
Developers assume "archive" means all past data is immediately available. The archive endpoint and the forecast endpoint are separate systems. ERA5 data is published with a 5-day processing delay; ECMWF IFS assimilation is closer to 2 days. The project plans to show historical wind for completed 2026 rides in Strava analysis — many of these rides will be within the lag window.

**How to avoid:**
- Compute `latest_available_date = today - timedelta(days=5)` before calling the archive API.
- If `ride_date > latest_available_date`, fall back to the forecast API's `past_days` parameter (which returns archived forecast model runs, not ERA5 observations — acceptable accuracy for recent rides).
- Document in `services/weather.py` which source is being used (archive reanalysis vs. forecast-based) so the UI can show appropriate precision indicators.
- Store the `data_source` field in `ride_wind_data` table alongside the fetched wind values.

**Warning signs:**
- Empty wind columns for rides from the past 1-5 days despite the API call "succeeding."
- HTTP 400 errors with message "End date is too recent" or no data returned for recent dates.
- Test: call archive with `end_date = date.today() - timedelta(days=1)` and verify a non-empty response.

**Phase to address:** Historical wind fetch phase (archive API integration). Implement the fallback logic before writing any Strava analysis display code, not as a follow-up fix.

---

### Pitfall 2: Archive vs. Forecast API Data Disagreement

**What goes wrong:**
A ride completed 60 days ago returns different wind values depending on whether you query the archive API or the forecast API with `past_days=60`. The values can differ by 3-8 km/h in wind speed and 10-30 degrees in direction. If both paths are used (e.g., different code for "older rides" vs. "recent rides"), the Strava analysis UI shows inconsistent values for consecutive rides.

**Why it happens:**
The archive API uses ERA5 reanalysis (a smoothed, lower-resolution climate model). The forecast API's `past_days` returns historical **forecast model runs** concatenated together — not observations. These are fundamentally different datasets that happen to share an API shape. The Open-Meteo maintainer confirmed: "This data will always differ." (GitHub issue #1231)

**How to avoid:**
- Pick one source per ride age band and document it explicitly. Recommended: archive API for rides older than 7 days, forecast `past_days` for rides 1-6 days old.
- Add a `wind_data_source ENUM('archive', 'forecast_past_days')` column to `ride_wind_data` so differences are traceable.
- Do not mix sources in a single ride's wind display — fetch all stops from the same source for a given ride.

**Warning signs:**
- Wind values flip between page loads (cache miss hitting different source each time).
- Riders ask "why did my wind data change?" after a week — archive data becoming available and overwriting forecast-based data.

**Phase to address:** Historical wind persistence phase. Define the source-selection logic in the DB schema before any data is written.

---

### Pitfall 3: Meteorological "Wind From" Convention Inversion

**What goes wrong:**
`wind_direction_10m` from Open-Meteo is **meteorological convention**: the direction the wind is blowing **FROM**, not toward. A value of 270° means wind coming from the west (blowing eastward). If this is used directly as the travel direction in the cosine projection, the headwind/tailwind label is inverted — a pure tailwind becomes a pure headwind.

**Why it happens:**
Navigation bearings describe where you are going; meteorological wind direction describes where the wind originates. Developers copying bearing math from navigation sources apply it directly to wind direction without the 180° inversion. The existing `headwind_component()` in `services/weather.py` correctly applies `wind_travel_deg = (wind_from_deg + 180) % 360` — this inversion is already implemented and tested. The risk is in new code written for crosswind calculation that bypasses this function and uses `wind_direction_10m` directly.

**How to avoid:**
- The crosswind calculation MUST use the same `wind_from_deg + 180` inversion before the sine projection.
- Add an explicit comment in the crosswind function: `# wind_direction_10m is "from" direction — add 180 to get travel direction`.
- Unit-test: north wind (0°), rider heading east (90°) → crosswind should be approximately `wind_speed * 1.0` (pure crosswind), not zero.

**Warning signs:**
- Strong headwinds labeled as tailwinds (and vice versa).
- "Strong tailwind" warnings on days riders report fighting the wind.
- A north wind (0°) on a northbound rider should produce a pure headwind — verify this in tests.

**Phase to address:** Wind classification and crosswind calculation phase. Add the crosswind unit test before wiring any UI.

---

### Pitfall 4: Stop Distance Interpolation — Off-By-One in Distance Units

**What goes wrong:**
RWGPS track points use `d` in **meters** (`distance_m`). Ride plan stops store `distance_miles`. When interpolating a stop's coordinates from track points using cumulative distance, mixing these units produces stop coordinates that are off by a factor of 1609 — placing the "40-mile stop" at a point 40 meters into the route.

**Why it happens:**
The existing codebase (`services/rwgps.py`) converts meters to miles for display in `build_ride_plan()`, but the raw track point distances remain in meters. When new interpolation code walks the track point array searching for the nearest `d` value to a stop's distance, it must convert the stop's `distance_miles` back to meters first — or compare in native meters throughout.

**How to avoid:**
- Implement interpolation entirely in **meters**. Store stop distances in meters internally during interpolation; convert to miles only for display.
- Create a single helper: `def find_track_point_for_stop(track_points, stop_distance_miles)` that converts to meters at entry and never re-converts internally.
- Assert in the function: `if stop_distance_m > max(tp['d'] for tp in track_points): raise ValueError(...)` — catches cases where stop distance exceeds track length.

**Warning signs:**
- Wind data for a 200-mile control looks like it's for the start of the route.
- API calls with coordinates near 0.0, 0.0 (null island) — symptom of unit confusion producing near-zero lat/lng deltas.
- Interpolated coordinates cluster near the start point for all stops.

**Phase to address:** Stop-to-coordinate interpolation phase. Write the unit test with a known track and known stop distances (in both units) before implementation.

---

### Pitfall 5: Single-Response Assumption for Multi-Location Open-Meteo Batch Requests

**What goes wrong:**
When requesting weather for 1 location, Open-Meteo returns a JSON **dict**. When requesting 2+ locations with comma-separated lat/lng, it returns a JSON **list**. Code that always does `data['hourly']` crashes with `TypeError: list indices must be integers` when multiple stops are fetched in one call.

**Why it happens:**
The API's inconsistent return type is a well-known gotcha (confirmed in GitHub discussion #696 and already handled in the existing `fetch_route_weather()` which wraps single-dict responses in a list). The archive API has the same behavior. New code that calls the archive endpoint and processes the response must apply the same normalization.

**How to avoid:**
- Extract the normalization logic into a shared helper: `def _normalize_open_meteo_response(data) -> list`.
- Call it in both the forecast fetch and the new archive fetch function.
- Test both code paths: one location and multiple locations in the same test suite.

**Warning signs:**
- "Works for single-stop routes, crashes for multi-stop routes."
- `TypeError: list object is not subscriptable` in production logs after deploying archive fetch.

**Phase to address:** Archive API fetch implementation. Apply the normalization before writing any processing logic downstream.

---

### Pitfall 6: Wind Classification Threshold Mismatch Between Display and Warning Logic

**What goes wrong:**
The wind warning banner uses `>30 km/h max or >15 km/h avg headwind` thresholds. The wind cell color classification uses different intensity buckets. If these are defined separately as magic numbers in the route handler and the template, they drift over time — the banner triggers at 30 km/h but the cell color doesn't turn "dark red" until 35 km/h, confusing riders who see a warning but no strong-color cells.

**Why it happens:**
Thresholds get defined inline in two places during development: once in the Python route logic that decides whether to show the banner, and once in the Jinja template that computes `background-color` intensity. No single source of truth is established.

**How to avoid:**
- Define all wind thresholds in `services/weather.py` as named constants:
  ```python
  HEAVY_WIND_MAX_KMH = 30
  HEAVY_HEADWIND_AVG_KMH = 15
  STRONG_HEADWIND_KMH = 15  # matches existing wind_label()
  ```
- Import these constants in both the route handler and pass them to the Jinja context rather than embedding numbers in the template.

**Warning signs:**
- Banner and cell coloring tell different stories for the same ride.
- A PR changes a threshold in one place but not the other.

**Phase to address:** Wind warning banner phase AND wind column display phase. Establish constants in the first phase so the second phase imports them.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Cache wind data in Flask-Caching only (no DB) for forecast | Simpler, no migration | Forecast data lost on Vercel cold starts; every new session re-fetches | Acceptable for forecast (ephemeral by nature) |
| Cache historical wind in Flask-Caching instead of `ride_wind_data` table | No DB schema needed | Every dyno restart re-fetches archive API; archive fetch is slow (~2s) | Never — DB persistence is explicitly required |
| Store only headwind component, not raw wind speed + direction | Simpler schema | Can't recalculate crosswind component from stored data if classification logic changes | Only if DB columns are constrained; add raw fields |
| Compute bearing between stop N and stop N+1 only | Easy to implement | Last stop has no "next" stop; bearing for final segment is undefined | Acceptable if final stop uses previous segment's bearing (already handled in `format_weather_response`) |
| Fetch all stops in separate API calls instead of batch | Easier response handling | Uses N requests instead of 1; risks hitting 10,000/day free tier limit on heavy traffic | Never for production — batch is essential |

---

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Open-Meteo Archive | Using `end_date = date.today()` — returns 400 or empty for recent dates due to 5-day ERA5 lag | Compute `end_date = min(ride_date, date.today() - timedelta(days=5))` |
| Open-Meteo batch request | Sending separate requests per stop for a 10-stop brevet | Single request with comma-separated lat/lng; normalize list-vs-dict response |
| Open-Meteo `timezone` parameter | Omitting it causes UTC-based hourly indices to misalign with local morning start times | Pass `timezone=auto` so hourly array indices match local time; verify hour 6 AM is index 6, not index 14 |
| RWGPS track points | Accessing `lat`/`lng` fields — these don't exist | RWGPS uses `y` (lat) and `x` (lng) and `d` (distance in meters) |
| RWGPS track points | Assuming all points have valid coordinates | Filter out `None` values in `y`/`x` before interpolation (already done in `sample_track_points` — must replicate in new interpolation function) |
| Archive API + Strava match | Fetching wind for ride's GPS start time in UTC, not local time | Convert `activity.start_date` (UTC from Strava) to local time before computing hourly index |

---

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Fetching RWGPS track points on every ride plan page load | Slow page load (300-500ms per ride plan); 429 errors from RWGPS API on heavy traffic | Cache track points by `rwgps_route_id` with long TTL (24h) — they don't change | ~20 concurrent page loads |
| Re-fetching archive wind data already in `ride_wind_data` table | Duplicate API calls; slow repeated queries | Check DB for existing `ride_id + stop_id` before calling archive | Every page load for a completed ride |
| Computing bearings inside the Jinja template loop | Template CPU time spikes; Vercel serverless timeout on 30-stop brevets | Compute all bearings in Python, pass pre-computed list to template context | 15+ stops per ride plan |
| Generating inline `style` strings with full HSL color computation per cell in Jinja | Works fine; not a perf issue at 10 stops | Pre-compute color values in Python and pass as string to template | Not a real break point — this is fine at expected scale |

---

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Passing raw `ride_id` or `stop_id` from URL query params directly into archive fetch without authorization check | Any user can fetch wind data for any ride, including private riders' data | Verify the ride belongs to the current user session before fetching/displaying historical wind |
| Caching wind data by `ride_id` only (not `user_id`) in Flask-Caching | Wind data for a private rider leaks to another user who happens to share the same cache key | Cache key must include session/user context for any user-specific data, or limit wind to public ride plan data only |

---

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Showing wind columns for a ride plan with no start date/time | Wind at "hour 0" is meaningless without knowing when the rider will be at each stop | Show a placeholder ("Set start time to see wind") rather than incorrect wind data based on current time |
| Using pure red/green for headwind/tailwind cells | Red-green colorblindness affects ~8% of male riders; cells are unreadable | Use blue for tailwind, orange-red for headwind (as specified in PROJECT.md) rather than green/red; add wind speed text as fallback signal |
| Showing headwind component (can be negative) as the wind speed number | "−12 km/h" confuses riders expecting a speed | Display raw `wind_speed_kmh` as the number in the cell; use the headwind component only to determine color direction |
| Wind warning banner appearing for a ride 4 weeks away when forecast data doesn't exist | Banner fires with stale cache or placeholder data | Only show the banner when the ride is within 7-day forecast range and actual wind data was fetched |
| Font size scaling for wind speed competing with the stop table layout | Cells at different heights break the table grid; looks broken on mobile | Use font-weight variation instead of font-size, or constrain font-size range to ±2px from base |

---

## "Looks Done But Isn't" Checklist

- [ ] **Stop coordinate interpolation:** Often missing — edge case where stop distance exactly matches a track point `d` value vs. falls between two points. Verify both cases return correct coordinates.
- [ ] **Archive API integration:** Often missing — error handling for the 5-day lag. Verify fallback to forecast-based data triggers correctly for rides from yesterday and 3 days ago.
- [ ] **Crosswind calculation:** Often missing — the `sin` projection for the perpendicular component, and the 45-degree threshold logic for classifying as "crosswind" vs. "headwind/tailwind". Verify a pure 90-degree crosswind returns a crosswind classification, not a weak headwind.
- [ ] **Wind columns in custom plan view:** Often missing — the custom plan merges base stops with overrides. Verify wind data is fetched for the merged stop list, not just the base stops (hidden stops must be excluded; added stops must be included).
- [ ] **DB persistence for historical wind:** Often missing — the "store once, read many" pattern. Verify that loading the Strava analysis page twice does NOT make two archive API calls.
- [ ] **Wind warning banner date window:** Often missing — the 3-4 week window check. Verify a brevet 5 weeks away does NOT show the banner even if it has a route.
- [ ] **Timezone alignment:** Often missing — wind at a mountain pass at 2 PM local should use hour index 14, not 14+UTC_offset. Verify with a non-UTC route location.

---

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Archive data lag causes missing wind for recent rides | LOW | Add fallback to `past_days` in forecast API; re-run backfill after archive data becomes available |
| Unit mismatch in stop interpolation produces wrong coordinates | MEDIUM | Add unit assertion at function entry; wipe any incorrectly cached track point coordinates; re-fetch |
| Wind thresholds inconsistent between banner and cells | LOW | Centralize constants in `services/weather.py`; grep for hardcoded 30/15 values and replace |
| Historical wind stored without `data_source` column | MEDIUM | DB migration to add column; backfill with 'unknown'; add source tracking going forward |
| `ride_wind_data` cache never invalidated when route changes | MEDIUM | Add `rwgps_route_id` + `fetched_at` to the table; invalidate rows older than 7 days for forecast-sourced entries |

---

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Archive API 5-day lag | Historical archive fetch implementation | Test: fetch wind for `date.today() - 2 days`; verify fallback triggers |
| Archive vs. forecast data source mismatch | DB schema for `ride_wind_data` | Schema includes `data_source` column before first data is written |
| "Wind from" meteorological inversion | Crosswind calculation implementation | Unit test: north wind (0°) + eastbound rider = near-zero headwind + max crosswind |
| Stop distance unit mismatch (miles vs. meters) | Stop interpolation implementation | Unit test: known track + stop at 40 miles → verify returned lat/lng matches expected location |
| Single-response assumption in multi-location batch | Archive API fetch | Test both `n=1` and `n=3` stop requests; assert list response in both cases |
| Wind threshold constants defined in two places | Wind warning banner phase | Single import of constants from `services/weather.py`; grep confirms no magic numbers in templates |
| Missing timezone alignment | Any Open-Meteo fetch | Test: early-morning ride at known location; verify hour index = local start hour |

---

## Sources

- [Archive Data Different From Forecast Data — Open-Meteo GitHub issue #1231](https://github.com/open-meteo/open-meteo/issues/1231)
- [Clarification on past_days Data and Accessing Recent Historical Weather — Open-Meteo GitHub issue #1480](https://github.com/open-meteo/open-meteo/issues/1480)
- [Correct way to get wind data for multiple locations — Open-Meteo GitHub discussion #696](https://github.com/open-meteo/open-meteo/discussions/696)
- [Timezone parameter not working as expected — Open-Meteo GitHub issue #850](https://github.com/open-meteo/open-meteo/issues/850)
- [Historical Weather API documentation — Open-Meteo](https://open-meteo.com/en/docs/historical-weather-api)
- [Wind direction conventions — meteorological vs. mathematical](https://meteorologytraining.tpub.com/14269/css/14269_55.htm)
- [ERA5 wind component calculation — ECMWF Confluence](https://confluence.ecmwf.int/pages/viewpage.action?pageId=133262398)
- Codebase inspection: `services/weather.py`, `services/rwgps.py`, `tests/test_weather.py`, `.planning/PROJECT.md`

---
*Pitfalls research for: wind forecast + historical wind integration (cycling randonneuring app)*
*Researched: 2026-03-23*
