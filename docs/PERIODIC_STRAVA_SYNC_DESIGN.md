# Periodic Strava Sync Design

## Problem Statement

Currently, Strava activities are only synced when:
1. A rider manually clicks "Sync Strava Activities" on their profile
2. A rider first connects their Strava account

This has two issues:
- **User friction**: Riders must remember to sync manually after rides
- **Rate limiting risk**: If all riders sync at once, could hit Strava's rate limits (200 req/15min, 2000 req/day)

## Objective

Design a **periodic background sync** that automatically updates Strava activities for all connected riders, respecting rate limits and working within Vercel's serverless architecture.

## Strava Rate Limits

Source: [Strava API Rate Limits](https://developers.strava.com/docs/rate-limits/)

**Default limits:**
- **200 requests per 15 minutes** (resets at :00, :15, :30, :45)
- **2,000 requests per day** (resets at midnight UTC)
- Exceeding limits returns `429 Too Many Requests`

**With rate limit increase** (requires application):
- 600 requests per 15 minutes
- 6,000 requests per day

**Our usage per rider sync:**
- Token refresh: 1 request
- Fetch activities: 1-N requests (1 per 100 activities, pagination)
- Average: ~2-3 requests per rider for recent syncs

## Infrastructure Constraints

**Current stack:**
- **Hosting**: Vercel (serverless Python via `@vercel/python`)
- **Database**: Supabase PostgreSQL
- **Deployment**: Auto-deploy on push to `main`

**Serverless limitations:**
- No persistent worker processes
- Functions are stateless and cold-start
- Cannot use traditional job queues (Celery, RQ, etc.)

## Design Options

### Option 1: Vercel Cron Jobs ⚠️

**How it works:**
- Add `crons` config to `vercel.json`
- Create serverless function endpoint (e.g., `/api/cron/sync_strava`)
- Vercel triggers on schedule

**Pros:**
- Native integration with Vercel
- Simple configuration
- Reliable execution

**Cons:**
- **Hobby plan limitation**: Max 2 cron jobs, each once per day only
- **Paid plan required** for more frequent schedules
- Less control over execution timing

**Pricing:**
- Free on Hobby (limited to 2 jobs/day)
- Pro plan: $20/month for flexible scheduling

### Option 2: GitHub Actions ✅ RECOMMENDED

**How it works:**
- Create `.github/workflows/strava-sync.yml`
- Schedule using cron expression
- Action hits authenticated endpoint on production site
- Endpoint syncs all riders with rate limiting

**Pros:**
- **Free** (2,000 minutes/month on free tier)
- **Flexible scheduling** (any cron expression)
- **Built-in monitoring** (workflow logs)
- **No additional services** (already using GitHub)
- **Easy to debug** (clear logs, manual trigger available)
- Works with any hosting platform

**Cons:**
- Requires creating endpoint + authentication
- Minimum interval is 5 minutes (GitHub Actions limitation)

**Cost:** Free for public repos, generous limits for private repos

### Option 3: Upstash QStash

**How it works:**
- Use Upstash's HTTP-based scheduler
- Configure to hit endpoint periodically
- Similar to GitHub Actions approach

**Pros:**
- Free tier: 500 requests/day
- Simple setup
- Good for serverless

**Cons:**
- Additional external dependency
- Free tier may be insufficient for growth
- Requires account management

### Option 4: Supabase pg_cron

**How it works:**
- Use PostgreSQL's `pg_cron` extension
- Schedule function to call HTTP endpoint via `pg_net`

**Pros:**
- Database-native solution
- No additional services

**Cons:**
- Requires Supabase extension (may not be available)
- More complex debugging
- Less common approach

## Recommended Solution: GitHub Actions

**Rationale:**
1. **No cost** - Free for public repos
2. **Flexible scheduling** - Can run every hour, twice daily, etc.
3. **Already integrated** - Using GitHub for code management
4. **Simple monitoring** - Workflow logs in GitHub UI
5. **Manual trigger** - Can re-run failed syncs easily
6. **No vendor lock-in** - Works regardless of hosting platform

## Implementation Design

### Architecture

```
┌─────────────────┐      Scheduled Trigger       ┌──────────────────┐
│                 │◄──────(e.g., every 6 hours)───│                  │
│ GitHub Actions  │                               │  GitHub Servers  │
│   Workflow      │                               │                  │
└────────┬────────┘                               └──────────────────┘
         │
         │ HTTP POST with auth token
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Vercel Production Site (team-asha-randonneuring)               │
│                                                                  │
│  POST /api/cron/sync-strava                                     │
│  ├─ Verify CRON_SECRET token                                    │
│  ├─ Get all riders with active Strava connection                │
│  ├─ Sync each rider with rate limiting:                         │
│  │  ├─ Max 50 riders per run (100-150 requests)                 │
│  │  ├─ Add delay between syncs (prevent burst)                  │
│  │  └─ Track success/failure per rider                          │
│  └─ Return summary: {synced: 12, failed: 0, skipped: 3}         │
└─────────────────────────────────────────────────────────────────┘
         │
         │ Database updates
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Supabase PostgreSQL                                            │
│  ├─ strava_activity (new activities inserted)                   │
│  ├─ strava_connection (tokens refreshed, last_sync updated)     │
│  └─ eddington_number recalculated                               │
└─────────────────────────────────────────────────────────────────┘
```

### Components

#### 1. Cron Endpoint (`routes/cron.py`)

New Flask blueprint with protected endpoint:

```python
from flask import Blueprint, request, jsonify
import os

cron_bp = Blueprint('cron', __name__)

@cron_bp.route('/sync-strava', methods=['POST'])
def sync_strava():
    """Periodic Strava sync endpoint (called by GitHub Actions)."""

    # 1. Verify authentication
    auth_header = request.headers.get('Authorization')
    expected_token = f"Bearer {os.environ.get('CRON_SECRET')}"

    if not auth_header or auth_header != expected_token:
        return jsonify({'error': 'Unauthorized'}), 401

    # 2. Get all riders with active Strava connection
    from models import get_all_active_strava_connections
    connections = get_all_active_strava_connections()

    # 3. Sync with rate limiting
    from services.strava import sync_rider_activities
    import time

    results = {'synced': 0, 'failed': 0, 'skipped': 0}
    max_riders = 50  # Limit per run to avoid rate limits

    for i, conn in enumerate(connections[:max_riders]):
        try:
            count = sync_rider_activities(
                rider_id=conn['rider_id'],
                days=7  # Only sync last 7 days (reduce API calls)
            )
            results['synced'] += 1

            # Add small delay to avoid bursts (1 sec between riders)
            if i < len(connections) - 1:
                time.sleep(1)

        except Exception as e:
            results['failed'] += 1
            # Log error but continue with other riders
            print(f"Failed to sync rider {conn['rider_id']}: {e}")

    results['skipped'] = len(connections) - max_riders if len(connections) > max_riders else 0

    return jsonify(results), 200
```

**Key decisions:**
- **Authentication**: Require `CRON_SECRET` environment variable
- **Rate limiting**: Max 50 riders per run, 1 second delay between riders
- **Error handling**: Continue syncing other riders if one fails
- **Sync window**: Only last 7 days (not full year) to reduce API calls

#### 2. Database Function (`models.py`)

```python
def get_all_active_strava_connections():
    """Get all riders with active Strava connections, ordered by last sync."""
    return _execute("""
        SELECT rider_id, access_token, refresh_token, expires_at, last_sync
        FROM strava_connection
        WHERE access_token IS NOT NULL
        ORDER BY last_sync ASC NULLS FIRST
        LIMIT 100
    """).fetchall()
```

**Ordering**: Prioritize riders who haven't synced in longest time.

#### 3. GitHub Actions Workflow (`.github/workflows/strava-sync.yml`)

```yaml
name: Periodic Strava Sync

on:
  schedule:
    # Run every 6 hours: 00:00, 06:00, 12:00, 18:00 UTC
    - cron: '0 */6 * * *'

  workflow_dispatch:  # Allow manual trigger

jobs:
  sync-strava:
    runs-on: ubuntu-latest

    steps:
      - name: Trigger Strava Sync
        run: |
          curl -X POST https://team-asha-randonneuring.vercel.app/api/cron/sync-strava \
            -H "Authorization: Bearer ${{ secrets.CRON_SECRET }}" \
            -H "Content-Type: application/json" \
            -v

      - name: Check response
        if: failure()
        run: echo "Strava sync failed - check endpoint logs"
```

**Schedule options:**
- `0 */6 * * *` - Every 6 hours (4x per day)
- `0 */12 * * *` - Every 12 hours (2x per day)
- `0 0 * * *` - Daily at midnight UTC
- `0 2,14 * * *` - Twice daily (2 AM, 2 PM UTC)

#### 4. Environment Variable

Add to Vercel environment variables:
- `CRON_SECRET`: Random secure token (e.g., `openssl rand -hex 32`)

Add to GitHub repository secrets:
- `CRON_SECRET`: Same value as Vercel

### Sync Strategy

**Per-rider sync:**
- Fetch activities from last 7 days only (not 365 days)
- Reduces API calls from ~5-10 to ~1-2 per rider
- Eddington number recalculates using all historical data (already in DB)

**Rate limit math:**
- 50 riders × 2 requests = 100 requests per run
- 4 runs per day = 400 requests/day
- Well within 2,000/day limit ✅

**Growth headroom:**
- 100 riders × 2 requests × 4 runs = 800/day
- 200 riders × 2 requests × 4 runs = 1,600/day
- Can increase to 500 riders before hitting 2,000/day limit

### Error Handling

1. **Authentication failure**: Returns 401, GitHub Actions marks as failed
2. **Individual rider sync failure**: Log error, continue with others
3. **Rate limit hit**: Catch 429, stop batch, return partial success
4. **Network timeout**: Retry with exponential backoff

### Monitoring

**GitHub Actions dashboard:**
- View workflow run history
- See success/failure status
- Check curl response logs

**Application logs:**
- Log each sync result to stdout
- Vercel function logs show detailed errors

**Database queries:**
```sql
-- Check last sync times
SELECT r.name, sc.last_sync, sc.eddington_number_miles
FROM riders r
JOIN strava_connection sc ON r.id = sc.rider_id
ORDER BY sc.last_sync DESC;

-- Check for stale connections (not synced in 7+ days)
SELECT r.name, sc.last_sync
FROM riders r
JOIN strava_connection sc ON r.id = sc.rider_id
WHERE sc.last_sync < NOW() - INTERVAL '7 days'
  OR sc.last_sync IS NULL;
```

## Migration Path

### Phase 1: Create Endpoint (No Auto-Sync)

1. Add `routes/cron.py` with protected endpoint
2. Add `get_all_active_strava_connections()` to models
3. Add `CRON_SECRET` to Vercel environment
4. Test manually with curl
5. Deploy to production

**Testing:**
```bash
curl -X POST https://team-asha-randonneuring.vercel.app/api/cron/sync-strava \
  -H "Authorization: Bearer YOUR_CRON_SECRET" \
  -H "Content-Type: application/json"
```

### Phase 2: GitHub Actions (Auto-Sync)

1. Create `.github/workflows/strava-sync.yml`
2. Add `CRON_SECRET` to GitHub repository secrets
3. Test with manual workflow dispatch
4. Enable scheduled runs
5. Monitor for 1 week

### Phase 3: Optimization (Optional)

- Adjust sync frequency based on usage patterns
- Add Slack/email notifications for failures
- Implement smart sync (only recent riders, skip inactive)
- Add database table for sync history/audit log

## Alternative Approaches (Future Consideration)

### Webhook-Based Sync

Strava supports webhooks for real-time activity updates:
- Rider authorizes with webhook subscription scope
- Strava calls our endpoint when new activity uploaded
- Near-instant sync, minimal API usage

**Challenges:**
- Requires webhook endpoint setup
- Need to handle webhook verification
- More complex authentication flow
- Webhook subscriptions require approval from Strava

**When to consider**: If team grows to 500+ riders or needs real-time updates.

## Security Considerations

1. **Endpoint authentication**: Use strong random token (32+ hex chars)
2. **Environment separation**: Different `CRON_SECRET` for preview vs production
3. **Rate limiting**: Protect endpoint from abuse (max 10 calls/hour)
4. **Logging**: Don't log access tokens or secrets
5. **Error messages**: Don't leak sensitive info in responses

## Testing Plan

### Unit Tests

Test individual components:
```python
# Test endpoint authentication
def test_sync_endpoint_requires_auth():
    response = client.post('/api/cron/sync-strava')
    assert response.status_code == 401

# Test sync with no riders
def test_sync_with_no_riders():
    response = client.post('/api/cron/sync-strava',
                          headers={'Authorization': f'Bearer {CRON_SECRET}'})
    assert response.json['synced'] == 0
```

### Integration Tests

1. **Manual trigger**: Use workflow_dispatch to trigger manually
2. **Check logs**: Verify riders synced successfully
3. **Database verification**: Confirm activities updated
4. **Eddington recalc**: Verify numbers recalculated
5. **Error scenarios**: Test with expired token, rate limit

### Load Testing

Simulate 50, 100, 200 riders to verify:
- Execution time stays under Vercel's function timeout (10s hobby, 60s pro)
- Rate limits respected
- No database connection pool exhaustion

## Rollout Plan

### Week 1: Development & Testing
- Implement endpoint and GitHub Actions
- Test in preview deployments
- Verify with 2-3 test riders

### Week 2: Limited Production
- Deploy to production
- Enable workflow for manual trigger only
- Monitor for any issues

### Week 3: Auto-Sync Enabled
- Enable scheduled runs (every 12 hours)
- Monitor sync success rate
- Gather feedback from riders

### Week 4: Optimization
- Adjust frequency based on usage
- Fine-tune rate limiting
- Add monitoring/alerts

## Success Metrics

- **Sync success rate**: >95% of riders sync successfully
- **Rate limit errors**: <1% of sync attempts
- **Execution time**: <30 seconds per batch
- **User satisfaction**: Riders see activities auto-update within 6 hours

## Rollback Plan

If issues arise:
1. **Disable workflow**: Comment out schedule in YAML
2. **Remove endpoint**: Comment out blueprint registration
3. **Revert to manual**: Riders use manual sync button
4. **Debug offline**: Fix issues in development
5. **Re-enable gradually**: Start with manual triggers, then scheduled

## Cost Analysis

**GitHub Actions:**
- Free tier: 2,000 minutes/month
- Usage per run: ~1 minute
- Runs per month: 120 (every 6 hours = 4/day × 30 days)
- Total: 120 minutes/month
- **Cost: $0** ✅

**Vercel Function:**
- Hobby plan: 100 GB-hours free
- Function execution: ~5-10 seconds per run
- Runs per month: 120
- Total: ~0.17 GB-hours/month
- **Cost: $0** ✅

**Strava API:**
- Free tier: 2,000 requests/day
- Our usage: ~400 requests/day
- **Cost: $0** ✅

**Total monthly cost: $0**

## Future Enhancements

1. **Smart scheduling**: Sync more frequently during peak riding season
2. **Rider preferences**: Allow riders to opt in/out of auto-sync
3. **Sync history**: Track sync attempts, success/failure in database
4. **Notifications**: Email riders about sync failures or milestones
5. **Dashboard**: Admin view of sync status across all riders
6. **Exponential backoff**: Retry failed syncs with increasing delays
7. **Webhook integration**: Real-time sync using Strava webhooks

## References

- [Strava API Rate Limits](https://developers.strava.com/docs/rate-limits/)
- [Vercel Cron Jobs Documentation](https://vercel.com/docs/cron-jobs)
- [GitHub Actions Scheduled Events](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#schedule)
- [Upstash QStash](https://upstash.com/docs/qstash/overall/getstarted)

---

**Document Version**: 1.0
**Created**: 2026-02-28
**Author**: Claude Code
**Status**: Design Complete - Ready for Implementation
