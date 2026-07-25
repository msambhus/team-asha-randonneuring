import json
from pathlib import Path

from flask import render_template


REPO_ROOT = Path(__file__).resolve().parents[2]
BREVETHUB_DIR = REPO_ROOT / "brevethub"
ARCHIVE_DIR = REPO_ROOT / "archive" / "brevethub_legacy_20260723"


def test_legacy_brevethub_is_archived():
    assert (ARCHIVE_DIR / "app.py").exists()
    assert (ARCHIVE_DIR / "routes").is_dir()
    assert (ARCHIVE_DIR / "templates").is_dir()
    assert (ARCHIVE_DIR / "tests" / "brevethub").is_dir()


def test_brevethub_is_standalone_and_keeps_private_strava_surface():
    from brevethub.app import app

    endpoints = set(app.view_functions)
    assert "admin.plan_console" in endpoints
    assert "auth.my_profile" in endpoints
    assert "live.live_list" in endpoints
    assert "riders.rider_profile" in endpoints
    assert "strava.connect" in endpoints
    assert "tools.merge_fit_page" in endpoints
    assert "riders.leaderboard" not in endpoints


def test_vercel_cron_paths_are_registered_by_brevethub_app():
    from brevethub.app import app

    config = json.loads(
        (BREVETHUB_DIR / "vercel.json").read_text(encoding="utf-8"))
    configured = {item["path"] for item in config.get("crons", [])}
    registered = {rule.rule for rule in app.url_map.iter_rules()}

    assert configured
    assert configured <= registered


def test_home_template_is_brevethub_neutral():
    source = (BREVETHUB_DIR / "templates" / "landing.html").read_text()

    assert "randonneuring" in source.lower()
    assert "randonneuring" in source.lower()
    assert "team_photo" not in source
    assert "Team Asha" not in source
    assert "underprivileged" not in source
    assert "about" not in source.lower()


def test_base_menu_matches_requested_brevethub_shape():
    source = (BREVETHUB_DIR / "templates" / "base.html").read_text()

    assert "My Profile" in source
    assert "My Rider Page" in source
    assert "Live" in source
    assert "Logout" in source
    assert "Merge GPS Files" in source
    assert "career leaderboard" not in source.lower()


def test_career_leaderboard_is_removed_from_both_products():
    from brevethub.app import app

    assert not (REPO_ROOT / "templates" / "career_leaderboard.html").exists()
    assert not (
        BREVETHUB_DIR / "templates" / "career_leaderboard.html"
    ).exists()
    assert "riders.leaderboard" not in app.view_functions
    assert not any(
        rule.rule == "/riders/leaderboard"
        for rule in app.url_map.iter_rules()
    )


def test_brevethub_shell_uses_rusa_blue_white_red_palette():
    combined_source = "\n".join(
        (BREVETHUB_DIR / "templates" / name).read_text().lower()
        for name in ("base.html", "index.html", "upcoming_brevets.html")
    )
    combined_static_source = "\n".join(
        (BREVETHUB_DIR / "static" / name).read_text().lower()
        for name in ("favicon.svg", "style.css", "ride-plan-v2.css")
    )
    combined_brevethub_source = f"{combined_source}\n{combined_static_source}"

    assert "--primary: #1f4f85" in combined_brevethub_source
    assert "--accent: #b64040" in combined_brevethub_source

    assert "#1a365d" not in combined_brevethub_source


def test_inherited_team_asha_templates_are_color_neutralized_for_brevethub():
    from brevethub.app import app

    source, _, _ = app.jinja_loader.get_source(app.jinja_env, "rider_profile.html")
    source = source.lower()

    assert "Team Asha" not in source


def test_inherited_templates_are_product_neutralized():
    from brevethub.app import app

    response = app.test_client().get("/auth/login")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "BrevetHub" in body
    assert "Team Asha" not in body


def test_calendar_template_uses_dynamic_state_and_club_filters():
    source = (BREVETHUB_DIR / "templates" / "calendar.html").read_text()

    assert 'id="region-state"' in source
    assert 'id="region-area"' in source
    assert "regions_by_state" in source
    assert "Team Asha" not in source


def test_brevethub_calendar_template_renders_with_rusa_context():
    from brevethub.app import app

    event = {
        "id": 101,
        "date": "2026-08-15",
        "date_str": "2026-08-15",
        "region": "CA: San Francisco",
        "club_code": "SFR",
        "club_name": "San Francisco Randonneurs",
        "route_name": "Sample 200k",
        "name": "Sample 200k",
        "distance_km": 200,
        "distance_miles": 124.3,
        "elevation_ft": 6000,
        "signup_count": 3,
        "rwgps_url": "https://ridewithgps.com/routes/123",
        "rwgps_url_team": None,
        "plan_slug": "sample-200k",
        "has_custom_plan": False,
        "plan_avg_speed": 12,
        "start_time": "07:00",
        "start_location": "Sample Start",
        "time_limit_hours": 13.5,
        "ride_type": "BRM",
    }

    with app.test_request_context("/riders/2026-2027/upcoming-brevets"):
        body = render_template("calendar.html", events=[event],
                               months=[("August 2026", [event])],
                               rider=None, club=None, states=['CA'],
                               regions_by_state={'CA': ['San Francisco']},
                               my_status={}, my_results=[], degraded=None,
                               weather={})

    assert "Sample 200k" in body
    assert "region-state" in body
    assert "region-area" in body
    assert "San Francisco" in body
    assert "Team Asha" not in body
