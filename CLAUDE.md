# Team Asha Randonneuring - Claude Code Guidelines

## Git Workflow Rules
- **NEVER push directly to main.** Always create a feature branch and open a PR.
- **Always merge from main** before creating a PR (`git merge main`).
- Use descriptive branch names: `fix/`, `feature/`, `chore/` prefixes.
- Commit messages should explain the "why", not just the "what".

## Tech Stack
- **Backend**: Flask (Python), Jinja2 templates, PostgreSQL via Supabase (psycopg2)
- **Frontend**: Tailwind CSS-based styling with CSS variables, inline styles
- **Deployment**: Vercel serverless
- **Integrations**: Strava OAuth2 + API v3, RUSA event scraping

## Key Concepts
- **RideStatus enum** (models.py): INTERESTED, MAYBE, GOING (renamed from SIGNED_UP), WITHDRAW, FINISHED, DNF, DNS, OTL
- **Seasons**: Named like "2024-2025", one is marked `is_current`
- **SR (Super Randonneur)**: Completing 200, 300, 400, 600 km brevets in one season
- **R-12 (Randonneur 12)**: At least one 200+km ride finished per month for 12 consecutive months
- **Fitness scoring** (services/fitness.py): 0-100 scale, 4 components — Frequency /25, Volume /35, Intensity /25, Recency /15
- **Per-ride grading** (services/fitness.py): A=70+, B=50+, C=30+, D=15+, F=0-14. Distance (60km=full), Elevation (1000m=full), Intensity (adaptive), Progressive Overload

## Database Notes
- Ride names may contain HTML entities from web scraping. Use `clean_name` template filter to sanitize.
- Strava activities stored in `strava_activity` table, synced for 1 year of data.
- All SQL queries using `rr.status = %s` must pass `RideStatus.FINISHED.value` as parameter.

## Template Filters
- `commafy` — Format numbers with commas (e.g., 1,234)
- `clean_name` — Decode HTML entities in ride names (html.unescape + nbsp cleanup)

## Units
- Career stats: **KMs** (not miles)
- Training rides (Strava): miles and feet (US convention for display)
- Brevet history: **km** for distance, feet for elevation
