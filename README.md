# Team Asha Randonneuring

A dynamic web app for **Team Asha Randonneuring** — a group of Bay Area cyclists who participate in ultra-distance randonneuring events while raising funds for the education of underprivileged children in India through [Team Asha](https://www.ashanet.org/).

## What This Site Does

- Tracks rider participation across multiple brevet seasons (2021-2022, 2022-2023, 2025-2026)
- Shows individual rider profiles with career stats, finish times, and Super Randonneur achievements
- Displays an upcoming brevets calendar sourced from [RUSA](https://rusa.org/) for San Francisco, Davis, and Santa Cruz regions
- Links to RideWithGPS routes with elevation and ft/mile data
- Documents team resources (gear, lights, nutrition, ride reports)
- Celebrates PBP (Paris-Brest-Paris) finishers

## Tech Stack

- **Backend:** Flask (Python)
- **Database:** PostgreSQL (Supabase)
- **Hosting:** Vercel (serverless via `@vercel/python`)
- **Templates:** Jinja2
- **CSS:** Tailwind CSS

## Project Structure

```
app.py               # Flask app factory
config.py            # Configuration
db.py                # Database connection helpers
models.py            # Data access functions
auth.py              # Admin authentication

routes/
  main.py            # Home, about, resources pages
  riders.py          # Season pages, rider profiles, upcoming brevets
  admin.py           # Admin panel (data management)
  signup.py          # Ride signup flow

templates/           # Jinja2 templates
static/              # CSS, images, rider photos

scripts/
  update_rusa_events.py    # Scrape RUSA/SFR/SCR brevet calendars into DB
  backfill_finish_times.py # Populate finish times from RUSA results
  migrate_to_supabase.py   # One-time SQLite to PostgreSQL migration

api/index.py         # Vercel serverless entrypoint
vercel.json          # Vercel build & routing config
```

## Running Locally

### 1. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 2. Install Node dependencies and build CSS
```bash
npm install
npm run build:css
```

### 3. Set up environment variables

Copy the example env file and fill in the database connection string:

```bash
cp .env.example .env
```

Edit `.env` and set `DATABASE_URL` to the Supabase PostgreSQL connection string. You can find this in the [Supabase dashboard](https://supabase.com/dashboard) under **Project Settings > Database > Connection string > URI** (use the **Transaction pooler** on port 6543).

If you don't have access to the Supabase project yet, ask the project owner to invite you (see [Giving Collaborators Production Access](#giving-collaborators-production-access) below).

### 4. Run Flask
```bash
flask run -p 5001
```

The app will be available at `http://localhost:5001`.

> **Note:** macOS Monterey and later use port 5000 for AirPlay Receiver, so we use port 5001 instead.

### Development Mode (Auto-rebuild CSS)

In a separate terminal:
```bash
npm run dev
```
This watches for changes to `static/input.css` and templates, automatically rebuilding Tailwind CSS.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | **Yes** | Supabase PostgreSQL connection string (Transaction pooler, port 6543) |
| `SECRET_KEY` | No | Flask session secret (defaults to `dev-key-change-in-prod`) |
| `ADMIN_PASSWORD` | No | Admin panel login password (defaults to `asha2026`) |

## Deployment

The site auto-deploys to [Vercel](https://vercel.com/) on push to `main`. Vercel routes all requests through `api/index.py`, which wraps the Flask app as a serverless function.

Tailwind CSS is automatically built during deployment via the `buildCommand` in `vercel.json`.

### Vercel Environment Variables

Set `DATABASE_URL` in the Vercel dashboard under **Project Settings > Environment Variables**. Apply it to all environments (Production, Preview, Development).

### Giving Collaborators Production Access

To give a new collaborator full access to develop, deploy, and debug:

1. **Vercel** — Go to your Vercel team settings and invite them by email. This gives them access to deployment logs, environment variables, and preview deployments.

2. **Supabase** — Go to your Supabase organization settings and invite them by email. This gives them access to the database, connection strings, and the SQL editor for debugging.

3. **GitHub** — Add them as a collaborator on the repo so they can push branches and create PRs. Merges to `main` trigger auto-deploy.

## Maintenance Scripts

These scripts connect directly to the Supabase database via `DATABASE_URL`.

```bash
# Update upcoming brevet calendar from RUSA, SFR, Davis, and Santa Cruz websites
DATABASE_URL='postgresql://...' python scripts/update_rusa_events.py

# Backfill finish times from RUSA race results
DATABASE_URL='postgresql://...' python scripts/backfill_finish_times.py
```

- **`update_rusa_events.py`** — Scrapes upcoming brevet events from SF Randonneurs (Google Sheet), Davis (Gold Country Randonneurs), and Santa Cruz Randonneurs. Upserts into the `upcoming_rusa_event` table.

- **`backfill_finish_times.py`** — Populates rider finish times from RUSA race result data. Matches by rider RUSA ID, date, and distance.

- **`migrate_to_supabase.py`** — One-time migration from the legacy SQLite database to Supabase PostgreSQL. Already run; kept for reference.

## License

This is a private project for Team Asha Randonneuring.
