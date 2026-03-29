# Phase 6: Historical Wind — Archive API and DB Persistence - Research

**Researched:** 2026-03-23
**Domain:** Open-Meteo Archive API, PostgreSQL persistence, Flask/psycopg2 data layer
**Confidence:** HIGH

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| WIND-07 | System fetches historical wind data via Open-Meteo archive API with start_date/end_date parameters | Archive API endpoint `https://archive-api.open-meteo.com/v1/archive` verified; batch lat/lng pattern identical to existing forecast fetch |
| WIND-08 | System falls back to forecast API `past_days` parameter when archive API returns no data for rides within 5 days (ERA5 reanalysis lag) | ERA5 5-day lag confirmed by official docs; forecast `/v1/forecast?past_days=N` supports up to 92 past days at 10m wind variables |
| STOR-01 | System stores historical wind data in `ride_wind_data` table with all required columns including data_source | Table schema defined; migration pattern from existing `migrations/00N_*.sql` files; `IF NOT EXISTS` for idempotency |
| STOR-02 | System checks `ride_wind_data` table before fetching from archive API; only fetches if no existing data for that ride | DB check-before-fetch pattern: `SELECT COUNT(*) FROM ride_wind_data WHERE ride_id = %s LIMIT 1`; if non-zero, skip API call entirely |
| STOR-03 | System stores `data_source` as 'archive' or 'forecast_past_days' to track provenance | `data_source TEXT NOT NULL CHECK (data_source IN ('archive', 'forecast_past_days'))` column in table; set at write time based on which API path was used |
</phase_requirements>

---

## Summary

Phase 6 introduces the only new database infrastructure in the wind integration milestone: the `ride_wind_data` table that persists one row per stop per completed ride. All Open-Meteo API machinery (batch lat/lng arrays, response normalization, wind classification math) already exists in `services/weather.py`. This phase extends it with one new URL, one new function, one migration script, and two new model functions — nothing more.

The central challenge is the ERA5 reanalysis lag: the Open-Meteo archive API (`/v1/archive`) uses ERA5 data updated with a 5-day delay. Rides completed within the past 5 days will return no archive data. The fallback is the forecast API's `past_days` parameter (`/v1/forecast?past_days=N`), which returns concatenated historical forecast model runs — a different dataset with slightly different values, but sufficient accuracy for recent rides. The `data_source` column records which API was used so consumers can apply appropriate precision caveats.

The persistence design is intentionally minimal: one row per stop (not one JSON blob per ride) so individual stops can be queried without deserializing the whole ride. The DB check happens first on every request; the archive API is only called when the table has no rows for the given `ride_id`.

**Primary recommendation:** Implement `fetch_historical_wind()` in `services/weather.py`, add `get_ride_wind_data()` and `save_ride_wind_data()` to `models.py`, and create `migrations/011_add_ride_wind_data.sql` as an idempotent `CREATE TABLE IF NOT EXISTS` script. No new runtime dependencies.

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| requests | 2.31.0 | HTTP GET to Open-Meteo archive API | Already used in `fetch_route_weather()`; same call pattern — just change URL and add date params |
| psycopg2-binary | 2.9.9 | Write/read `ride_wind_data` rows | Already used throughout `models.py`; `_execute()` helper handles cursor lifecycle |
| psycopg2.extras.RealDictCursor | (bundled) | Return rows as dicts from `get_ride_wind_data()` | All existing model queries use this cursor factory via `_execute()` |

### No New Dependencies Required

This phase requires zero new packages. The full capability set is already present:

| Need | Covered By |
|------|-----------|
| Archive API HTTP call | `requests` 2.31.0 |
| Forecast API past_days fallback | `requests` 2.31.0 (same `fetch_route_weather()` endpoint, different params) |
| Row-per-stop DB write | `psycopg2-binary` + `_execute()` helper |
| Idempotent migration | Raw SQL file in `migrations/` — existing project convention |

---

## Architecture Patterns

### Recommended Project Structure

Files touched in Phase 6:

```
services/
└── weather.py           # Add: OPEN_METEO_ARCHIVE_URL constant + fetch_historical_wind()
models.py                # Add: get_ride_wind_data() + save_ride_wind_data()
migrations/
└── 011_add_ride_wind_data.sql   # New: idempotent CREATE TABLE IF NOT EXISTS
tests/
└── test_weather.py      # Extend: TestFetchHistoricalWind + TestFallbackToPastDays
tests/
└── test_models_wind.py  # New: TestGetRideWindData + TestSaveRideWindData
```

Nothing in `routes/` is touched in Phase 6. The route integration (loading wind data into Strava analysis pages) belongs to Phase 7.

### Pattern 1: Archive API Fetch with 5-Day Fallback

**What:** Before calling the archive API, compute whether the ride date is within the ERA5 lag window. If `ride_date > today - 5 days`, call the forecast API with `past_days` instead.

**When to use:** Every time historical wind is needed for a completed ride.

**Decision boundary:** `5 days` is the ERA5 lag as documented by Open-Meteo. Rides from 1-4 days ago will hit the forecast fallback; rides 5+ days ago use the archive. This boundary is a named constant.

```python
# Source: services/weather.py extension
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
ARCHIVE_LAG_DAYS = 5  # ERA5 reanalysis published with 5-day delay


def fetch_historical_wind(stop_coords, ride_date):
    """Fetch historical wind for stop coordinates on a specific ride date.

    stop_coords: list of {'lat': float, 'lng': float}
    ride_date: datetime.date object for the completed ride

    Returns (wind_data_list, data_source) where:
        wind_data_list: list of per-location hourly dicts (same shape as forecast)
        data_source: 'archive' or 'forecast_past_days'

    Raises requests.HTTPError on API failure.
    """
    from datetime import date, timedelta
    today = date.today()
    lag_cutoff = today - timedelta(days=ARCHIVE_LAG_DAYS)

    if ride_date <= lag_cutoff:
        return _fetch_archive_wind(stop_coords, ride_date), 'archive'
    else:
        days_ago = (today - ride_date).days
        return _fetch_forecast_past_days_wind(stop_coords, days_ago), 'forecast_past_days'


def _fetch_archive_wind(stop_coords, ride_date):
    """Call Open-Meteo archive API for a specific past date."""
    lats = ",".join(str(round(p['lat'], 4)) for p in stop_coords)
    lngs = ",".join(str(round(p['lng'], 4)) for p in stop_coords)
    date_str = ride_date.strftime('%Y-%m-%d')
    params = {
        'latitude': lats,
        'longitude': lngs,
        'start_date': date_str,
        'end_date': date_str,
        'hourly': 'wind_speed_10m,wind_direction_10m,wind_gusts_10m',
        'timezone': 'auto',
    }
    resp = requests.get(OPEN_METEO_ARCHIVE_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return [data] if isinstance(data, dict) else data


def _fetch_forecast_past_days_wind(stop_coords, days_ago):
    """Call Open-Meteo forecast API with past_days for very recent rides."""
    lats = ",".join(str(round(p['lat'], 4)) for p in stop_coords)
    lngs = ",".join(str(round(p['lng'], 4)) for p in stop_coords)
    params = {
        'latitude': lats,
        'longitude': lngs,
        'past_days': max(days_ago + 1, 1),  # include at least 1 day of past data
        'hourly': 'wind_speed_10m,wind_direction_10m,wind_gusts_10m',
        'timezone': 'auto',
    }
    resp = requests.get(OPEN_METEO_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return [data] if isinstance(data, dict) else data
```

### Pattern 2: DB Check-Before-Fetch (STOR-02)

**What:** Query `ride_wind_data` before calling the archive API. If rows exist for the `ride_id`, skip the API entirely and return stored data.

**When to use:** Every call to the function that fetches and persists historical wind.

**Example:**

```python
# Source: models.py additions
def get_ride_wind_data(ride_id):
    """Return all stored wind rows for a ride, or empty list if none.

    Returns list of dicts with stop_order, stop_name, wind_speed_kmh,
    wind_direction_deg, headwind_kmh, crosswind_kmh, wind_type,
    temperature_c, conditions, data_source, fetched_at.
    """
    rows = _execute(
        "SELECT * FROM ride_wind_data WHERE ride_id = %s ORDER BY stop_order",
        (ride_id,)
    ).fetchall()
    return list(rows)


def save_ride_wind_data(ride_id, wind_rows):
    """Persist per-stop wind data for a completed ride.

    wind_rows: list of dicts with keys:
        stop_order, stop_name, wind_speed_kmh, wind_direction_deg,
        headwind_kmh, crosswind_kmh, wind_type, temperature_c,
        conditions, data_source

    Uses INSERT ... ON CONFLICT DO NOTHING for idempotency.
    Commits immediately (not within a transaction that auto-rolls back).
    """
    conn = get_db()
    cur = conn.cursor()
    for row in wind_rows:
        cur.execute("""
            INSERT INTO ride_wind_data
                (ride_id, stop_order, stop_name, wind_speed_kmh,
                 wind_direction_deg, headwind_kmh, crosswind_kmh,
                 wind_type, temperature_c, conditions, data_source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ride_id, stop_order) DO NOTHING
        """, (
            ride_id,
            row['stop_order'],
            row.get('stop_name'),
            row.get('wind_speed_kmh'),
            row.get('wind_direction_deg'),
            row.get('headwind_kmh'),
            row.get('crosswind_kmh'),
            row.get('wind_type'),
            row.get('temperature_c'),
            row.get('conditions'),
            row['data_source'],
        ))
    conn.commit()
```

### Pattern 3: Migration Script Convention

**What:** The project uses numbered SQL files in `migrations/` applied by hand or via `scripts/migrate_to_supabase.py`. The pattern uses `IF NOT EXISTS` for idempotency.

**Convention from existing migrations:**
- Filename: `migrations/011_add_ride_wind_data.sql`
- Use `CREATE TABLE IF NOT EXISTS` — never bare `CREATE TABLE`
- Use `CREATE INDEX IF NOT EXISTS` for all indexes
- One logical change per file; brief comment header

```sql
-- Migration 011: Add ride_wind_data table for historical wind persistence
-- Stores one row per stop per completed ride; prevents repeat archive API calls.

CREATE TABLE IF NOT EXISTS ride_wind_data (
    id SERIAL PRIMARY KEY,
    ride_id INTEGER NOT NULL REFERENCES ride(id) ON DELETE CASCADE,
    stop_order INTEGER NOT NULL,
    stop_name TEXT,
    wind_speed_kmh NUMERIC,
    wind_direction_deg INTEGER,
    headwind_kmh NUMERIC,
    crosswind_kmh NUMERIC,
    wind_type TEXT CHECK (wind_type IN ('headwind', 'tailwind', 'crosswind')),
    temperature_c NUMERIC,
    conditions TEXT,
    data_source TEXT NOT NULL CHECK (data_source IN ('archive', 'forecast_past_days')),
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (ride_id, stop_order)
);

CREATE INDEX IF NOT EXISTS idx_ride_wind_data_ride_id ON ride_wind_data(ride_id);
```

### Anti-Patterns to Avoid

- **Storing wind as a JSONB blob:** The `STOR-01` requirement names individual columns (`stop_order`, `stop_name`, `wind_speed_kmh`, etc.). Use per-row columns, not a JSONB array. Per-row storage also allows Phase 7 to query individual stop wind data without JSON unpacking.
- **Writing forecast wind to the DB:** Forecast wind is ephemeral. Only historical wind (from archive or `past_days`) is persisted. `ride_wind_data` is never written for upcoming/current rides.
- **Using `date.today()` as `end_date` in archive API call:** The archive API will return partial or empty data for dates within the ERA5 lag window. Always check against `today - timedelta(days=ARCHIVE_LAG_DAYS)` before routing to the archive endpoint.
- **Calling archive API on every page load:** The DB check in `get_ride_wind_data()` must happen before any API call. If rows exist, return immediately without calling Open-Meteo.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Per-stop lat/lng coordinates | Custom distance interpolation | Existing `get_stop_coordinates()` in `services/weather.py` | Already implemented, unit-tested in Phase 2 |
| Wind classification per stop | New classification logic | Existing `headwind_component()`, `crosswind_component()`, `classify_wind()`, `wind_cell_style()` in `services/weather.py` | Fully tested across Phases 1-5; identical logic needed here |
| API response normalization (dict vs. list) | Custom normalization | `[data] if isinstance(data, dict) else data` pattern already established | Archive API has same single/multi location response shape as forecast API (Pitfall 5 in PITFALLS.md) |
| DB connection management | New connection pool | `_execute()` in `models.py` + `get_db()` in `db.py` | All existing queries use this pattern; `get_db()` tied to Flask app context lifecycle |

---

## Common Pitfalls

### Pitfall 1: Archive API 5-Day ERA5 Lag (WIND-08)

**What goes wrong:** Calling `archive-api.open-meteo.com/v1/archive` with `end_date` within the past 5 days returns no data or an HTTP 400. A ride completed 3 days ago will return empty wind arrays.

**Why it happens:** ERA5 reanalysis data is published with approximately a 5-day processing delay. The archive endpoint only serves data that has completed reanalysis.

**How to avoid:** Implement the `ARCHIVE_LAG_DAYS = 5` constant and compare `ride_date <= today - timedelta(days=5)` before deciding which API to call. If within the lag window, call `/v1/forecast` with `past_days=N` instead.

**Warning signs:** Empty wind columns for rides from the past 1-4 days; wind data appears only for rides older than ~1 week.

### Pitfall 2: `data_source` Not Set at Write Time (STOR-03)

**What goes wrong:** Wind rows are stored without `data_source`, making it impossible to distinguish archive-sourced data from forecast-based fallback data. Phase 7 cannot surface appropriate precision caveats.

**How to avoid:** `fetch_historical_wind()` must return both the data AND the source string as a tuple `(wind_data, data_source)`. The source string is passed through to `save_ride_wind_data()` and stored on every row.

### Pitfall 3: Re-fetching on Every Request (STOR-02)

**What goes wrong:** `get_ride_wind_data(ride_id)` is called but its result is not checked before calling the archive API. Every Strava analysis page load triggers an archive API call (2-3 second latency, counted against the 10K/day free tier limit).

**How to avoid:** The orchestration function must check `get_ride_wind_data(ride_id)` first. Only call the archive API if the result list is empty. This is the entire point of the DB persistence layer.

```python
# Correct orchestration in route handler (Phase 7 will call this):
stored = get_ride_wind_data(ride_id)
if stored:
    return stored  # served from DB, no API call
wind_data, data_source = fetch_historical_wind(stop_coords, ride_date)
# ... compute per-stop rows ...
save_ride_wind_data(ride_id, wind_rows)
return wind_rows
```

### Pitfall 4: Transaction Rollback Swallowing Writes in Tests

**What goes wrong:** The `db_conn` fixture in `conftest.py` rolls back after each test. If `save_ride_wind_data()` is called via `get_db()` (the Flask app-context connection), the test's direct `db_conn` psycopg2 connection and the app-context connection are different connections — the test cannot see the inserted rows via `db_conn`.

**How to avoid:** Test DB persistence via the Flask test client (integration test against the route), or pass a `conn` explicitly to `save_ride_wind_data()` in tests. The pure unit tests for `fetch_historical_wind()` mock `requests.get` entirely and do not touch the DB.

---

## Code Examples

### Fetch Historical Wind (full orchestration, Phase 6 service layer)

```python
# Source: services/weather.py — new function
def get_historical_stop_wind(stops, track_points, ride_date):
    """Fetch historical wind for each stop on a completed ride's date.

    stops: list of dicts with 'distance_miles', 'stop_order', 'location' keys
    track_points: RWGPS track dicts (y=lat, x=lng, d=distance_m)
    ride_date: datetime.date for the completed ride

    Returns (wind_rows, data_source) or (None, None) on error.
    wind_rows: list of per-stop dicts ready for save_ride_wind_data()
    """
    if not track_points:
        return None, None

    coords = get_stop_coordinates(stops, track_points)
    valid_coords = [c for c in coords if c is not None]
    if not valid_coords:
        return None, None

    try:
        weather_data, data_source = fetch_historical_wind(valid_coords, ride_date)
    except Exception:
        logger.exception("get_historical_stop_wind: archive API error for %s", ride_date)
        return None, None

    if not weather_data:
        return None, None

    # Map valid coord indices back to original stop indices
    valid_map = {}
    valid_idx = 0
    for orig_idx, c in enumerate(coords):
        if c is not None:
            valid_map[orig_idx] = valid_idx
            valid_idx += 1

    # Estimate start as midnight + 7h (default start if not known)
    start_dt = datetime.combine(ride_date, datetime.min.time()).replace(hour=7)

    wind_rows = []
    for i, coord in enumerate(coords):
        if coord is None:
            continue
        v_idx = valid_map.get(i)
        if v_idx is None or v_idx >= len(weather_data):
            continue

        forecast = weather_data[v_idx]
        hourly = forecast.get('hourly', {})

        arrival_time_min = stops[i].get('arrival_time_min')
        if arrival_time_min is not None:
            arrival_dt = start_dt + timedelta(minutes=arrival_time_min)
        else:
            dist_km = stops[i].get('distance_miles', 0) * 1.60934
            arrival_dt = start_dt + timedelta(hours=dist_km / _AVG_SPEED_KMH)

        hour_index = get_hour_index(hourly.get('time', []), arrival_dt)
        wind_speed = _safe_get(hourly, 'wind_speed_10m', hour_index, 0.0)
        wind_dir = _safe_get(hourly, 'wind_direction_10m', hour_index, 0)
        temp = _safe_get(hourly, 'temperature_2m', hour_index, None)

        bearing = 0.0
        if i + 1 < len(coords) and coords[i + 1] is not None:
            bearing = calculate_bearing(
                coord['lat'], coord['lng'],
                coords[i + 1]['lat'], coords[i + 1]['lng'],
            )
        elif i > 0 and coords[i - 1] is not None:
            bearing = calculate_bearing(
                coords[i - 1]['lat'], coords[i - 1]['lng'],
                coord['lat'], coord['lng'],
            )

        hw = headwind_component(wind_speed, wind_dir, bearing)
        cw = crosswind_component(wind_speed, wind_dir, bearing)
        wind_type = classify_wind(hw, cw)

        wind_rows.append({
            'stop_order': stops[i].get('stop_order', i),
            'stop_name': stops[i].get('location', ''),
            'wind_speed_kmh': round(float(wind_speed), 1),
            'wind_direction_deg': int(wind_dir),
            'headwind_kmh': round(float(hw), 1),
            'crosswind_kmh': round(float(cw), 1),
            'wind_type': wind_type,
            'temperature_c': round(float(temp), 1) if temp is not None else None,
            'conditions': None,  # archive API does not return weather_code by default
            'data_source': data_source,
        })

    return wind_rows, data_source
```

### DB Migration (idempotent)

```sql
-- migrations/011_add_ride_wind_data.sql
-- Source: migrations/009_add_strava_ride_analysis.sql pattern

CREATE TABLE IF NOT EXISTS ride_wind_data (
    id SERIAL PRIMARY KEY,
    ride_id INTEGER NOT NULL REFERENCES ride(id) ON DELETE CASCADE,
    stop_order INTEGER NOT NULL,
    stop_name TEXT,
    wind_speed_kmh NUMERIC,
    wind_direction_deg INTEGER,
    headwind_kmh NUMERIC,
    crosswind_kmh NUMERIC,
    wind_type TEXT CHECK (wind_type IN ('headwind', 'tailwind', 'crosswind')),
    temperature_c NUMERIC,
    conditions TEXT,
    data_source TEXT NOT NULL CHECK (data_source IN ('archive', 'forecast_past_days')),
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (ride_id, stop_order)
);

CREATE INDEX IF NOT EXISTS idx_ride_wind_data_ride_id ON ride_wind_data(ride_id);
```

### Test Mock Pattern (matching project conventions)

```python
# Source: tests/test_weather.py extension pattern
from unittest.mock import patch, MagicMock
from datetime import date, timedelta

class TestFetchHistoricalWind:
    def _make_mock_response(self, wind_speed=15.0, wind_dir=270):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'hourly': {
                'time': [f"2026-01-10T{h:02d}:00" for h in range(24)],
                'wind_speed_10m': [wind_speed] * 24,
                'wind_direction_10m': [wind_dir] * 24,
                'wind_gusts_10m': [wind_speed * 1.3] * 24,
            }
        }
        return mock_resp

    def test_old_ride_uses_archive(self, **kwargs):
        from services.weather import fetch_historical_wind
        old_date = date.today() - timedelta(days=10)
        with patch('services.weather.requests.get') as mock_get:
            mock_get.return_value = self._make_mock_response()
            _, source = fetch_historical_wind([{'lat': 37.77, 'lng': -122.41}], old_date)
        assert source == 'archive'
        call_url = mock_get.call_args[0][0]
        assert 'archive-api.open-meteo.com' in call_url

    def test_recent_ride_uses_past_days(self, **kwargs):
        from services.weather import fetch_historical_wind
        recent_date = date.today() - timedelta(days=3)
        with patch('services.weather.requests.get') as mock_get:
            mock_get.return_value = self._make_mock_response()
            _, source = fetch_historical_wind([{'lat': 37.77, 'lng': -122.41}], recent_date)
        assert source == 'forecast_past_days'
        call_url = mock_get.call_args[0][0]
        assert 'api.open-meteo.com/v1/forecast' in call_url
        assert 'past_days' in mock_get.call_args[1].get('params', {})
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| JSONB blob for all stops | Per-row normalized columns | Prior research decision (STACK.md) | Allows individual stop queries; matches `STOR-01` requirement explicitly listing column names |
| Separate calls per stop | Batch comma-separated lat/lng | Existing pattern from Phase 3 | Single API call for all stops; well within 10K/day limit |

**Deprecated/outdated (from prior research STACK.md):**
- Storing forecast wind in DB: Never correct; forecast data is ephemeral, belongs in Flask-Caching only
- `openmeteo-requests` SDK: Rejected; zero benefit over existing `requests` pattern

---

## Open Questions

1. **`temperature_c` and `conditions` from archive API**
   - What we know: The archive API supports `temperature_2m` and `weather_code` as optional hourly variables. The `conditions` column is TEXT (nullable) in the schema.
   - What's unclear: Phase 7 requirements (HIST-01 to HIST-04) only mention wind columns, not temperature or condition display. Phase 6's STOR-01 schema includes these columns but the success criteria don't require them to be populated.
   - Recommendation: Add `temperature_2m` to the archive API request params for future use. Store NULL for `conditions` initially (weather_code is not required by Phase 7). This avoids a schema migration later if Phase 7 wants temperature.

2. **Ride start time for historical hour selection**
   - What we know: Archive API returns hourly data for the entire `ride_date`. `get_hour_index()` selects the correct hour based on estimated arrival time. For historical rides, we may not know the exact start time.
   - What's unclear: Where does the route handler get `start_time_str` for a completed ride? The `ride` table has a `start_time TEXT` column and `ride_plan` has `start_time TEXT DEFAULT '07:00'`.
   - Recommendation: Use `ride.start_time` if present; fall back to ride_plan's `start_time`; fall back to `'07:00'` as default. Document the fallback chain in the function docstring.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (from requirements-dev.txt) |
| Config file | none — pytest discovers tests/ automatically |
| Quick run command | `python3 -m pytest tests/test_weather.py -x -q` |
| Full suite command | `python3 -m pytest tests/ -x -q` |

### Phase Requirements to Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| WIND-07 | Archive API called with correct URL, start_date, end_date params | unit | `python3 -m pytest tests/test_weather.py -k "archive" -x` | Wave 0 |
| WIND-07 | Batch lat/lng sent for multiple stops | unit | `python3 -m pytest tests/test_weather.py -k "archive_batch" -x` | Wave 0 |
| WIND-07 | Single-dict response normalized to list | unit | `python3 -m pytest tests/test_weather.py -k "archive_single" -x` | Wave 0 |
| WIND-08 | Ride 3 days ago routes to forecast API past_days | unit | `python3 -m pytest tests/test_weather.py -k "past_days" -x` | Wave 0 |
| WIND-08 | Ride 10 days ago routes to archive API | unit | `python3 -m pytest tests/test_weather.py -k "old_ride_archive" -x` | Wave 0 |
| WIND-08 | Boundary: ride exactly 5 days ago routes to archive | unit | `python3 -m pytest tests/test_weather.py -k "lag_boundary" -x` | Wave 0 |
| STOR-01 | `ride_wind_data` table has all required columns + data_source | manual/migration | run `migrations/011_add_ride_wind_data.sql` against DB | Wave 0 |
| STOR-02 | Second call for same ride_id returns DB rows, API not called | unit | `python3 -m pytest tests/test_models_wind.py -k "no_refetch" -x` | Wave 0 |
| STOR-03 | data_source='archive' stored for old rides | unit | `python3 -m pytest tests/test_models_wind.py -k "data_source_archive" -x` | Wave 0 |
| STOR-03 | data_source='forecast_past_days' stored for recent rides | unit | `python3 -m pytest tests/test_models_wind.py -k "data_source_forecast" -x` | Wave 0 |

### Sampling Rate

- **Per task commit:** `python3 -m pytest tests/test_weather.py -x -q`
- **Per wave merge:** `python3 -m pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_weather.py` — extend with `TestFetchHistoricalWind`, `TestFetchArchiveWind`, `TestFetchForecastPastDays`, `TestGetHistoricalStopWind` classes
- [ ] `tests/test_models_wind.py` — new file covering `get_ride_wind_data()` and `save_ride_wind_data()` (mock `_execute()` / `get_db()`)
- [ ] `migrations/011_add_ride_wind_data.sql` — migration file (applied manually before integration tests)

---

## Sources

### Primary (HIGH confidence)
- [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api) — archive endpoint URL, ERA5 5-day lag, `start_date`/`end_date` params, wind variables
- [Open-Meteo Forecast API](https://open-meteo.com/en/docs) — `past_days` parameter, max 92 days, same `/v1/forecast` endpoint
- Codebase — `services/weather.py` (existing fetch pattern), `models.py` (_execute helper, RealDictCursor), `db.py` (get_db), `migrations/009_add_strava_ride_analysis.sql` (IF NOT EXISTS migration convention), `tests/conftest.py` (db_conn fixture rollback behavior)
- `.planning/research/STACK.md` — prior research confirming archive API shape, no new dependencies, JSONB vs. row-per-stop decision
- `.planning/research/PITFALLS.md` — ERA5 lag confirmed, data_source tracking rationale, test patterns for DB persistence

### Secondary (MEDIUM confidence)
- [Open-Meteo GitHub issue #1231](https://github.com/open-meteo/open-meteo/issues/1231) — archive vs. forecast data disagreement confirmed by maintainer
- [Open-Meteo GitHub issue #1480](https://github.com/open-meteo/open-meteo/issues/1480) — past_days for recent historical weather

### Tertiary (LOW confidence)
- None

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependencies; all patterns verified from existing codebase
- Architecture: HIGH — direct extension of established weather.py pattern; migration convention verified from existing files
- Pitfalls: HIGH — ERA5 lag and DB persistence patterns verified from official docs and prior research

**Research date:** 2026-03-23
**Valid until:** 2026-06-23 (Open-Meteo API is stable; ERA5 lag is a fixed upstream characteristic)
