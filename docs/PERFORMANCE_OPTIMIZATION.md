# Performance Optimization Analysis

**Date**: 2026-02-26
**Analyzed by**: Claude Code Performance Audit

## Executive Summary

This document outlines critical performance optimizations for the Team Asha Randonneuring application, focusing on database indexing and query optimization. The optimizations will significantly improve page load times, especially for rider profile pages and season leaderboards.

## Issues Identified

### 1. N+1 Query Problems

#### 🔴 Critical: Rider Profile Page (`routes/riders.py:546-687`)

**Problem**: The rider profile page makes 3+ database queries per season in a loop:

```python
for s in seasons:  # If 3 seasons, this runs 3 times
    participation = get_rider_participation(rider['id'], s['id'])  # Query 1
    stats = get_rider_season_stats(rider['id'], s['id'])          # Query 2
    sr_n = detect_sr_for_rider_season(rider['id'], s['id'], ...)  # Query 3
```

**Impact**: For a rider with 3 seasons of history, this makes **9+ database queries** just to load the profile page.

**Solution**: Create a batch query function similar to `get_all_rider_season_stats` that fetches all seasons data in 1-2 queries.

#### 🟡 Medium: Custom Plan Loading

**Location**: `routes/riders.py:441`

```python
for event in rusa_events:
    if event.get('plan_slug'):
        custom_plan = get_custom_plan(rider_id, plan_id)  # N queries
```

**Impact**: If there are 10 events with plans, this makes 10 separate queries.

**Solution**: Already has `@cache.memoize` which helps, but could be batch-loaded.

### 2. Missing Composite Indexes

#### 🔴 Critical: `ride` table

Current indexes are single-column only. Common query patterns need composite indexes:

```sql
-- Missing indexes:
CREATE INDEX idx_ride_season_date ON ride(season_id, date DESC);
CREATE INDEX idx_ride_club_date ON ride(club_id, date DESC);
CREATE INDEX idx_ride_date_status ON ride(date, event_status);
```

**Queries affected**:
- `get_rides_for_season` - joins by season_id, then filters by date (lines 150-164)
- `get_upcoming_rides` - filters by club_id + date (lines 181-197)
- `get_all_upcoming_events` - filters by date + event_status (lines 565-582)

#### 🟡 Medium: `rider_ride` table

```sql
-- Missing indexes:
CREATE INDEX idx_rider_ride_rider_status ON rider_ride(rider_id, status);
CREATE INDEX idx_rider_ride_ride_status ON rider_ride(ride_id, status);
CREATE INDEX idx_rider_ride_ride_signup ON rider_ride(ride_id, signed_up_at) WHERE signed_up_at IS NOT NULL;
```

**Queries affected**:
- All stats queries that filter by rider_id + status='FINISHED'
- Signup count queries (lines 651-658, 661-678)
- Participation matrix queries (lines 225-244)

#### 🟢 Low: `strava_activity` table

**Already optimized!** Has composite index:
```sql
CREATE INDEX idx_strava_activity_rider_date ON strava_activity(rider_id, start_date DESC);
```

### 3. Expensive Queries Without Optimization

#### Query 1: Participation Matrix
**Location**: `models.py:225-244`

```python
def get_participation_matrix(season_id):
    rows = _execute("""
        SELECT rr.rider_id, rr.ride_id, rr.status, rr.finish_time, rr.signed_up_at
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE ri.season_id = %s
          AND (ri.event_status = 'COMPLETED' OR ri.date < CURRENT_DATE)
    """, (season_id,)).fetchall()
```

**Optimization**: Add composite index on `ride(season_id, id)` to speed up JOIN.

#### Query 2: SR Detection
**Location**: `models.py:334-369`

```python
def detect_sr_for_all_riders_in_season(season_id, date_filter=False):
    rows = _execute("""
        SELECT rr.rider_id, ri.distance_km FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE ri.season_id = %s AND rr.status = %s AND ri.date <= %s
    """, (season_id, RideStatus.FINISHED.value, today)).fetchall()
```

**Optimization**: Composite index on `ride(season_id, date)` will help significantly.

## Optimization Plan

### Phase 1: Add Missing Composite Indexes (Immediate Impact)

**Estimated Improvement**: 40-60% reduction in query time for season/rider pages

```sql
-- Ride table composite indexes
CREATE INDEX CONCURRENTLY idx_ride_season_date ON ride(season_id, date DESC);
CREATE INDEX CONCURRENTLY idx_ride_club_date ON ride(club_id, date DESC);
CREATE INDEX CONCURRENTLY idx_ride_date_status ON ride(date, event_status);
CREATE INDEX CONCURRENTLY idx_ride_season_id_id ON ride(season_id, id);

-- Rider_ride table composite indexes
CREATE INDEX CONCURRENTLY idx_rider_ride_rider_status ON rider_ride(rider_id, status);
CREATE INDEX CONCURRENTLY idx_rider_ride_ride_status ON rider_ride(ride_id, status);
CREATE INDEX CONCURRENTLY idx_rider_ride_ride_signup ON rider_ride(ride_id, signed_up_at)
    WHERE signed_up_at IS NOT NULL;
```

**Note**: `CONCURRENTLY` allows index creation without locking the table (safe for production).

### Phase 2: Optimize N+1 Queries in Rider Profile

**Estimated Improvement**: 3-5x faster rider profile page loads

Create batch query functions:

```python
@cache.memoize(CACHE_TIMEOUT)
def get_rider_all_seasons_data(rider_id):
    """Fetch all season participation, stats, and SR data in optimized queries.

    Returns dict: {season_id: {participation: [...], stats: {...}, sr_count: int}}
    """
    # Single query for all participation across all seasons
    participation_rows = _execute("""
        SELECT s.id as season_id, s.name, s.start_date, s.end_date,
               rr.status, rr.finish_time, ri.name as ride_name, ri.date,
               ri.distance_km, ri.elevation_ft, ri.ft_per_mile, ri.rwgps_url,
               c.code as club_code
        FROM season s
        LEFT JOIN ride ri ON ri.season_id = s.id
        LEFT JOIN rider_ride rr ON rr.ride_id = ri.id AND rr.rider_id = %s
        LEFT JOIN club c ON ri.club_id = c.id
        WHERE rr.id IS NOT NULL
          AND (ri.event_status = 'COMPLETED' OR ri.date < CURRENT_DATE)
        ORDER BY s.start_date DESC, ri.date
    """, (rider_id,)).fetchall()

    # Group by season
    from collections import defaultdict
    season_data = defaultdict(lambda: {'participation': [], 'stats': {'rides': 0, 'kms': 0}})

    for row in participation_rows:
        sid = row['season_id']
        season_data[sid]['participation'].append(row)
        if row['status'] == RideStatus.FINISHED.value:
            season_data[sid]['stats']['rides'] += 1
            season_data[sid]['stats']['kms'] += row['distance_km'] or 0

    # Add SR counts (batch query)
    seasons = get_all_seasons()
    current = get_current_season()
    for s in seasons:
        sid = s['id']
        if sid not in season_data:
            season_data[sid] = {'participation': [], 'stats': {'rides': 0, 'kms': 0}}
        is_current = current and current['id'] == sid
        sr_count = detect_sr_for_rider_season(rider_id, sid, date_filter=is_current)
        season_data[sid]['sr_count'] = sr_count
        season_data[sid]['season'] = s

    return dict(season_data)
```

### Phase 3: Add Query Result Caching

**Estimated Improvement**: 90%+ reduction in database load for frequently accessed pages

The application already uses `@cache.memoize` extensively (great!), but some queries could benefit from longer cache times:

```python
# Extend cache timeout for rarely-changing data
@cache.memoize(timeout=3600)  # 1 hour instead of 5 minutes
def get_all_ride_plans():
    return _execute("SELECT * FROM ride_plan ORDER BY name").fetchall()

@cache.memoize(timeout=3600)
def get_clubs():
    return _execute("SELECT * FROM club ORDER BY name").fetchall()
```

## Performance Metrics

### Before Optimization (Estimated)

| Page | Query Count | Estimated Load Time |
|------|-------------|---------------------|
| Season Riders (34 riders) | 15-20 queries | 800-1200ms |
| Rider Profile (3 seasons) | 12-15 queries | 600-900ms |
| Upcoming Brevets (20 events) | 8-10 queries | 400-600ms |

### After Optimization (Projected)

| Page | Query Count | Estimated Load Time |
|------|-------------|---------------------|
| Season Riders (34 riders) | 8-10 queries | 200-400ms |
| Rider Profile (3 seasons) | 4-6 queries | 150-250ms |
| Upcoming Brevets (20 events) | 3-5 queries | 100-200ms |

**Overall improvement**: 50-70% reduction in query count, 60-80% reduction in load time

## Implementation Priority

1. **Phase 1** (Day 1): Add composite indexes - Zero code changes, immediate 40-60% improvement
2. **Phase 2** (Day 2-3): Optimize rider profile N+1 queries - Biggest user-facing impact
3. **Phase 3** (Day 4): Tune cache timeouts - Low effort, good ROI

## Testing Plan

1. **Index verification**: Use `EXPLAIN ANALYZE` to confirm indexes are being used
2. **Load testing**: Compare page load times before/after with realistic data volume
3. **Cache hit ratio**: Monitor cache effectiveness with `cache.get_stats()`

## Additional Recommendations

### 1. Add Database Query Logging

```python
# Add to models.py
import time

def _execute(sql, params=None):
    """Execute a query and return a RealDictCursor."""
    start = time.time()
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params or ())
    elapsed = (time.time() - start) * 1000
    if elapsed > 100:  # Log slow queries (>100ms)
        print(f"[SLOW QUERY] {elapsed:.0f}ms: {sql[:100]}...")
    return cur
```

### 2. Consider Materialized View for Season Stats

For frequently accessed aggregate data that changes infrequently:

```sql
CREATE MATERIALIZED VIEW mv_season_stats AS
SELECT
    ri.season_id,
    COUNT(DISTINCT rr.rider_id) as active_riders,
    COUNT(*) as total_rides,
    COALESCE(SUM(ri.distance_km), 0) as total_kms
FROM rider_ride rr
JOIN ride ri ON rr.ride_id = ri.id
WHERE rr.status = 'FINISHED'
GROUP BY ri.season_id;

-- Refresh periodically (e.g., after ride results are entered)
REFRESH MATERIALIZED VIEW mv_season_stats;
```

### 3. Pagination for Large Result Sets

If the number of riders/events continues to grow, consider pagination:

```python
@riders_bp.route('/riders/<season_name>')
def season_riders(season_name):
    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    # Add LIMIT/OFFSET to queries
    riders = get_riders_for_season_paginated(season['id'], limit=per_page, offset=offset)
```

## Conclusion

The proposed optimizations will significantly improve application performance with minimal code changes. The composite indexes in Phase 1 provide the biggest bang for the buck, while the N+1 query optimizations in Phase 2 will make the biggest difference in user experience.

**Estimated total improvement**: 60-80% reduction in database query time across the application.
