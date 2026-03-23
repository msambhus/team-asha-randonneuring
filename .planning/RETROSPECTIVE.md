# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

## Milestone: v1.0 — Wind Forecast Integration

**Shipped:** 2026-03-23
**Phases:** 7 | **Plans:** 11 | **Sessions:** 7 (ralph iterations)

### What Was Built
- Wind math foundation (crosswind projection, classification, color/intensity/font styling)
- Stop-to-coordinate interpolation from RWGPS track points with miles-to-meters conversion
- Forecast wind columns in base and custom ride plans with color-coded headwind/tailwind/crosswind
- Heavy wind warning banner on upcoming brevets page (28-day lookahead)
- Historical wind persistence via Open-Meteo archive API with 5-day ERA5 fallback
- Actual Wind display in Strava analysis and clickable ride name links for recent seasons

### What Worked
- **Bottom-up layering**: Pure math → interpolation → service orchestration → UI. Each phase built cleanly on the previous.
- **TDD discipline**: Every plan started with failing tests, then made them pass. Zero regressions across 11 plans.
- **Inline styles decision early**: Recognizing Tailwind JIT can't purge dynamic classes saved rework in phases 3-7.
- **ralph.sh automation**: 7 iterations of plan-or-execute ran unattended, each completing one phase.
- **Graceful degradation pattern**: `{% if stop_wind %}` gating meant no phase broke pages without wind data.

### What Was Inefficient
- **ROADMAP.md checkboxes not updated**: Phases 2, 3, 7 show `[ ]` and "Planning" status despite being complete. STATE.md progress was correct but ROADMAP.md drifted.
- **No milestone audit**: Skipped `/gsd:audit-milestone` — would have caught the ROADMAP drift before archival.

### Patterns Established
- `fetch_stop_wind()` as the central wind orchestration function — all surfaces (base plan, custom plan, warnings) funnel through it
- `wind_cell_style()` returns inline style dicts consumed directly by Jinja2 templates — no CSS class generation
- `ride_wind_data` table with ON CONFLICT DO NOTHING for idempotent writes
- `(data, source)` tuple return pattern for functions that fetch from multiple backends
- stop_wind dict keyed by stop_name (not index) for Strava analysis where row counts may differ

### Key Lessons
1. **Cache key design matters early**: Using `wind:{plan_slug}:{YYYYMMDD}{HH}` avoided collision with existing `weather:` cache keys — worth planning cache key namespace up front.
2. **Archive API lag is real**: Open-Meteo ERA5 reanalysis has a 5-day lag. Building the fallback to forecast `past_days` into the initial design (Phase 6) avoided a production surprise.
3. **Local imports in route handlers affect test patching**: When functions import at the function level, tests must patch the source module (e.g., `models.get_ride_plan_stops`) not the route module.

### Cost Observations
- Model mix: 100% opus (via ralph.sh automation)
- Sessions: 7 ralph iterations, ~14 hours wall-clock
- Notable: Single-day milestone completion for a 7-phase, 11-plan, 31-requirement feature set

---

## Cross-Milestone Trends

### Process Evolution

| Milestone | Sessions | Phases | Key Change |
|-----------|----------|--------|------------|
| v1.0 | 7 | 7 | First milestone — established TDD + ralph.sh automation pattern |

### Cumulative Quality

| Milestone | Tests Added | Files Changed | Zero-Dep Additions |
|-----------|-------------|---------------|-------------------|
| v1.0 | ~1,749 lines | 12 | 3 (wind math, interpolation, model persistence) |

### Top Lessons (Verified Across Milestones)

1. Bottom-up layering (pure functions → service orchestration → UI) prevents integration surprises
2. TDD with failing tests first catches contract mismatches between phases before they compound
