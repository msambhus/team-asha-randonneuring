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
- **Database:** SQLite (read-only on Vercel)
- **Hosting:** Vercel (serverless via `@vercel/python`)
- **Templates:** Jinja2

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
data/team_asha.db    # SQLite database

api/index.py         # Vercel serverless entrypoint
vercel.json          # Vercel build & routing config
```

## Running Locally

```bash
pip install -r requirements.txt
flask run
```

The app will be available at `http://localhost:5000`.

## Deployment

The site auto-deploys to [Vercel](https://vercel.com/) on push to `main`. Vercel routes all requests through `api/index.py`, which wraps the Flask app as a serverless function.

### Environment Variables

Set these in the Vercel dashboard:

| Variable | Description |
|----------|-------------|
| `ADMIN_USERNAME` | Admin panel login username |
| `ADMIN_PASSWORD` | Admin panel login password |

## License

This is a private project for Team Asha Randonneuring.
