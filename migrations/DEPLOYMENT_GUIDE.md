# Migration 006 Deployment Guide

This guide provides step-by-step instructions for deploying the composite index migration to your production database.

## Pre-Deployment Checklist

- [ ] Review the migration SQL file: `migrations/006_add_composite_indexes.sql`
- [ ] Confirm you have database access (credentials, network connectivity)
- [ ] Backup your database (optional, but recommended for peace of mind)
- [ ] Choose your deployment method (see options below)
- [ ] Schedule deployment during low-traffic period (optional - migration is non-blocking)

## Deployment Methods

### Method 1: Supabase Dashboard SQL Editor (Easiest) ⭐

**Best for**: Quick deployment, no server access needed

**Time required**: 5 minutes

**Steps**:

1. **Open Supabase Dashboard**
   - Go to https://supabase.com/dashboard
   - Select your Team Asha project
   - Navigate to **SQL Editor** in the left sidebar

2. **Open the Migration File**
   - On your local machine, open: `migrations/006_add_composite_indexes.sql`
   - Or view it on GitHub after pushing

3. **Copy Index Statements**

   Copy each of these statements one at a time:

   ```sql
   -- Index 1: Season rides query optimization
   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ride_season_date
   ON ride(season_id, date DESC);
   ```

   ```sql
   -- Index 2: Club rides query optimization
   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ride_club_date
   ON ride(club_id, date DESC);
   ```

   ```sql
   -- Index 3: Upcoming events optimization
   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ride_date_status
   ON ride(date, event_status);
   ```

   ```sql
   -- Index 4: JOIN optimization
   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ride_season_id_id
   ON ride(season_id, id);
   ```

   ```sql
   -- Index 5: Rider stats optimization
   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rider_ride_rider_status
   ON rider_ride(rider_id, status);
   ```

   ```sql
   -- Index 6: Participation queries optimization
   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rider_ride_ride_status
   ON rider_ride(ride_id, status);
   ```

   ```sql
   -- Index 7: Signup queries optimization
   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rider_ride_ride_signup
   ON rider_ride(ride_id, signed_up_at)
   WHERE signed_up_at IS NOT NULL;
   ```

   ```sql
   -- Index 8: Batch queries optimization
   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rider_ride_rider_ride
   ON rider_ride(rider_id, ride_id);
   ```

4. **Execute Each Statement**
   - Paste one statement into the SQL Editor
   - Click **RUN** or press Ctrl+Enter
   - Wait for "Success" message
   - Repeat for all 8 indexes

5. **Update Table Statistics**

   After creating all indexes, run:

   ```sql
   VACUUM ANALYZE ride;
   VACUUM ANALYZE rider_ride;
   ```

6. **Verify Indexes Were Created**

   Run this verification query:

   ```sql
   SELECT tablename, indexname,
          pg_size_pretty(pg_relation_size(indexname::regclass)) as size
   FROM pg_indexes
   WHERE tablename IN ('ride', 'rider_ride')
     AND indexname LIKE 'idx_%'
   ORDER BY tablename, indexname;
   ```

   You should see 8 new indexes listed.

**Expected Output**:
```
tablename   | indexname                        | size
------------|----------------------------------|-------
ride        | idx_ride_club_date              | 16 kB
ride        | idx_ride_date_status            | 16 kB
ride        | idx_ride_season_date            | 16 kB
ride        | idx_ride_season_id_id           | 16 kB
rider_ride  | idx_rider_ride_ride_signup      | 8 kB
rider_ride  | idx_rider_ride_ride_status      | 16 kB
rider_ride  | idx_rider_ride_rider_ride       | 16 kB
rider_ride  | idx_rider_ride_rider_status     | 16 kB
```

---

### Method 2: Production Server via SSH

**Best for**: Automated deployment, server access available

**Time required**: 3 minutes

**Prerequisites**:
- SSH access to production server
- Python 3.6+ installed
- Project code deployed

**Steps**:

1. **SSH into Production Server**

   ```bash
   ssh user@your-production-server.com
   ```

2. **Navigate to Project Directory**

   ```bash
   cd /path/to/team-asha-randonneuring
   ```

3. **Ensure Latest Code**

   ```bash
   git pull origin main
   ```

4. **Verify Migration Files Exist**

   ```bash
   ls -la migrations/
   # Should see: 006_add_composite_indexes.sql
   #             apply_migration_standalone.py
   ```

5. **Check Database Connectivity**

   ```bash
   # Test database connection
   python3 -c "from db import get_db; print('✓ Database connected')"
   ```

   If this fails, make sure your `.env` file has the correct `DATABASE_URL`.

6. **Run the Migration**

   ```bash
   python3 migrations/apply_migration_standalone.py
   ```

7. **Verify Success**

   You should see output like:

   ```
   ======================================================================
   Migration 006: Add Composite Indexes for Performance
   ======================================================================

   Connecting to database... ✓ Connected

   Creating indexes...

     idx_ride_season_date... ✓ Created
     idx_ride_club_date... ✓ Created
     idx_ride_date_status... ✓ Created
     idx_ride_season_id_id... ✓ Created
     idx_rider_ride_rider_status... ✓ Created
     idx_rider_ride_ride_status... ✓ Created
     idx_rider_ride_rider_ride... ✓ Created
     idx_rider_ride_ride_signup... ✓ Created

   ======================================================================
   Migration Summary:
     Created:       8
     Skipped:       0 (already exist)
     Errors:        0
   ======================================================================

   Updating table statistics...
   ✓ Statistics updated

   ✓ Migration completed successfully!
   ```

8. **Restart Application** (if needed)

   ```bash
   # Restart your Flask app (method depends on your deployment)
   sudo systemctl restart team-asha-app
   # or
   supervisorctl restart team-asha
   # or
   kill -HUP $(cat /var/run/team-asha.pid)
   ```

---

### Method 3: Using psql Command Line

**Best for**: Direct database access, maximum control

**Time required**: 2 minutes

**Prerequisites**:
- `psql` installed on your machine
- Database credentials

**Steps**:

1. **Get Your Database URL**

   From your `.env` file or environment:
   ```bash
   export DATABASE_URL="postgresql://postgres.xxxxx:YOUR_PASSWORD@aws-0-us-west-2.pooler.supabase.com:6543/postgres"
   ```

   **Security Note**: Never commit the real password to git!

2. **Test Connection**

   ```bash
   psql "$DATABASE_URL" -c "SELECT current_database(), current_user;"
   ```

   Expected output:
   ```
    current_database | current_user
   ------------------+--------------
    postgres         | postgres
   ```

3. **Run Migration File**

   ```bash
   psql "$DATABASE_URL" -f migrations/006_add_composite_indexes.sql
   ```

4. **Verify Indexes**

   ```bash
   psql "$DATABASE_URL" -c "
   SELECT tablename, indexname,
          pg_size_pretty(pg_relation_size(indexname::regclass)) as size
   FROM pg_indexes
   WHERE tablename IN ('ride', 'rider_ride')
     AND indexname LIKE 'idx_%'
   ORDER BY tablename, indexname;
   "
   ```

---

### Method 4: GitHub Actions / CI/CD Pipeline

**Best for**: Automated deployments, team environments

**Time required**: 10 minutes setup, automatic thereafter

**Steps**:

1. **Create GitHub Actions Workflow**

   Create `.github/workflows/deploy-migration.yml`:

   ```yaml
   name: Deploy Migration 006

   on:
     workflow_dispatch:  # Manual trigger only
       inputs:
         confirm:
           description: 'Type "apply" to confirm migration'
           required: true

   jobs:
     deploy-migration:
       runs-on: ubuntu-latest
       if: github.event.inputs.confirm == 'apply'

       steps:
         - name: Checkout code
           uses: actions/checkout@v3

         - name: Setup Python
           uses: actions/setup-python@v4
           with:
             python-version: '3.9'

         - name: Install dependencies
           run: |
             pip install psycopg2-binary

         - name: Run migration
           env:
             DATABASE_URL: ${{ secrets.DATABASE_URL }}
           run: |
             python3 migrations/apply_migration_standalone.py

         - name: Verify migration
           env:
             DATABASE_URL: ${{ secrets.DATABASE_URL }}
           run: |
             python3 -c "
             import psycopg2
             import os
             conn = psycopg2.connect(os.environ['DATABASE_URL'])
             cur = conn.cursor()
             cur.execute('''
               SELECT COUNT(*) FROM pg_indexes
               WHERE tablename IN ('ride', 'rider_ride')
                 AND indexname LIKE 'idx_%'
             ''')
             count = cur.fetchone()[0]
             print(f'Found {count} indexes')
             assert count >= 8, 'Migration verification failed'
             print('✓ Migration verified')
             "
   ```

2. **Add Database Secret**

   - Go to GitHub repository → Settings → Secrets and variables → Actions
   - Add new secret: `DATABASE_URL`
   - Value: Your production database connection string

3. **Trigger the Workflow**

   - Go to Actions tab
   - Select "Deploy Migration 006"
   - Click "Run workflow"
   - Type "apply" in the confirmation field
   - Click "Run workflow"

4. **Monitor Execution**

   Watch the workflow logs to see the migration progress.

---

## Post-Deployment Verification

### 1. Check Index Existence

Run this query in any SQL client:

```sql
SELECT
    schemaname,
    tablename,
    indexname,
    pg_size_pretty(pg_relation_size(indexname::regclass)) as size,
    idx_scan as times_used,
    idx_tup_read as rows_read
FROM pg_indexes
JOIN pg_stat_user_indexes USING (schemaname, tablename, indexname)
WHERE tablename IN ('ride', 'rider_ride')
  AND indexname LIKE 'idx_%'
ORDER BY tablename, indexname;
```

### 2. Test Query Performance

Before (without indexes):
```sql
EXPLAIN ANALYZE
SELECT * FROM ride
WHERE season_id = 3
ORDER BY date DESC;
```

After (with indexes):
- Should show "Index Scan using idx_ride_season_date"
- Execution time should be 50-70% faster

### 3. Monitor Application Performance

- Check page load times in browser dev tools
- Season leaderboard should load in ~300ms (down from ~800ms)
- Rider profiles should load in ~250ms (down from ~600ms)
- Upcoming events should load in ~150ms (down from ~400ms)

### 4. Check Index Usage Over Time

After a few days, check which indexes are being used:

```sql
SELECT
    indexname,
    idx_scan as scans,
    idx_tup_read as tuples_read,
    pg_size_pretty(pg_relation_size(indexname::regclass)) as size
FROM pg_stat_user_indexes
WHERE schemaname = 'public'
  AND indexname LIKE 'idx_ride%' OR indexname LIKE 'idx_rider_ride%'
ORDER BY idx_scan DESC;
```

Indexes with high `idx_scan` values are being used frequently (good!).

---

## Troubleshooting

### Error: "permission denied to create index"

**Solution**: You need database owner or superuser privileges.

```sql
-- Grant privileges (run as superuser)
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO your_user;
```

### Error: "index already exists"

**Solution**: This is fine! It means the index was already created. Skip to next index.

### Error: "relation does not exist"

**Solution**: Table name might be different. Check your schema:

```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name LIKE '%ride%';
```

### Indexes not being used

**Possible causes**:
1. Table too small (PostgreSQL might prefer sequential scan)
2. Statistics outdated (run `VACUUM ANALYZE`)
3. Wrong query pattern

**Check with**:
```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM ride WHERE season_id = 3 ORDER BY date DESC;
```

### Migration takes longer than expected

**Normal for**:
- Large tables (>10,000 rows): May take 1-2 minutes per index
- High server load: Will complete when resources available

**Using CONCURRENTLY**: The migration won't lock your tables, so the app keeps working during creation.

---

## Rollback Instructions

If you need to remove the indexes (not recommended unless causing issues):

```sql
-- Remove all indexes created by this migration
DROP INDEX CONCURRENTLY IF EXISTS idx_ride_season_date;
DROP INDEX CONCURRENTLY IF EXISTS idx_ride_club_date;
DROP INDEX CONCURRENTLY IF EXISTS idx_ride_date_status;
DROP INDEX CONCURRENTLY IF EXISTS idx_ride_season_id_id;
DROP INDEX CONCURRENTLY IF EXISTS idx_rider_ride_rider_status;
DROP INDEX CONCURRENTLY IF EXISTS idx_rider_ride_ride_status;
DROP INDEX CONCURRENTLY IF EXISTS idx_rider_ride_ride_signup;
DROP INDEX CONCURRENTLY IF EXISTS idx_rider_ride_rider_ride;

-- Update statistics
VACUUM ANALYZE ride;
VACUUM ANALYZE rider_ride;
```

---

## Support

If you encounter issues:

1. Check the troubleshooting section above
2. Review the detailed analysis: `docs/PERFORMANCE_OPTIMIZATION.md`
3. Check Supabase logs for database errors
4. Verify database connectivity and credentials

---

## Success Criteria

✅ All 8 indexes created successfully
✅ No errors in migration output
✅ Application still works correctly
✅ Page load times improved by 50-70%
✅ No increase in error rates

---

## Timeline

- **Preparation**: 5 minutes (review this guide)
- **Execution**: 2-5 minutes (depending on method)
- **Verification**: 2 minutes
- **Total**: ~10-15 minutes

---

## Next Steps After Deployment

1. **Monitor performance** for 24-48 hours
2. **Check index usage** with the queries above
3. **Consider Phase 2 optimizations** (see `docs/PERFORMANCE_OPTIMIZATION.md`)
4. **Update team** on performance improvements
5. **Document** any issues or observations

---

**Questions?** Check the main `PERFORMANCE_SUMMARY.md` or detailed `docs/PERFORMANCE_OPTIMIZATION.md`.
