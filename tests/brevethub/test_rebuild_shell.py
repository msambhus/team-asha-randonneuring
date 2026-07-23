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


def test_brevethub_reuses_team_asha_app_surface():
    from brevethub.app import app

    endpoints = set(app.view_functions)
    assert "admin.dashboard" in endpoints
    assert "auth.my_profile" in endpoints
    assert "live.live_hub" in endpoints
    assert "riders.rider_profile" in endpoints
    assert "strava.connect" in endpoints
    assert "tools.merge_fit_page" in endpoints
    assert "weather.weather_page" in endpoints

    assert "main.about" not in endpoints
    assert "brevethub_overrides.calendar_alias" in endpoints
    assert "brevethub_overrides.auto_generate_plans" in endpoints
    assert "brevethub_overrides.cron_auto_generate_plans" in endpoints


def test_home_template_is_brevethub_neutral():
    source = (BREVETHUB_DIR / "templates" / "index.html").read_text()

    assert "BrevetHub" in source
    assert "randonneuring" in source.lower()
    assert "team_photo" not in source
    assert "Team Asha" not in source
    assert "underprivileged" not in source
    assert "main.about" not in source


def test_base_menu_matches_requested_brevethub_shape():
    source = (BREVETHUB_DIR / "templates" / "base.html").read_text()

    assert "My Profile" in source
    assert "My Rider Page" in source
    assert "Live" in source
    assert "Logout" in source
    assert "Merge GPS Files" in source
    assert "main.about" not in source


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

    assert "--rusa-blue: #0088ce" in combined_brevethub_source
    assert "--rusa-red: #c01700" in combined_brevethub_source
    assert "--rusa-red-dark: #a01300" in combined_brevethub_source
    assert "var(--rusa-white)" in combined_brevethub_source

    replaced_team_asha_or_old_brevethub_colors = {
        "#002868",
        "#bf0a30",
        "#1a365d",
        "#234878",
        "#0f766e",
        "#0891b2",
        "#0e7490",
        "#fb923c",
        "#9a3412",
        "#c2410c",
        "#4338ca",
        "#6d28d9",
        "#7c3aed",
    }
    for color in replaced_team_asha_or_old_brevethub_colors:
        assert color not in combined_brevethub_source


def test_inherited_team_asha_templates_are_color_neutralized_for_brevethub():
    from brevethub.app import app

    source, _, _ = app.jinja_loader.get_source(app.jinja_env, "rider_profile.html")
    source = source.lower()

    assert "#0088ce" in source
    assert "#c01700" in source
    assert "#1a365d" not in source
    assert "#e53e3e" not in source
    assert "#7c3aed" not in source


def test_inherited_templates_are_product_neutralized():
    from brevethub.app import app

    response = app.test_client().get("/auth/login")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "BrevetHub" in body
    assert "Team Asha" not in body


def test_calendar_template_uses_dynamic_state_and_club_filters():
    source = (BREVETHUB_DIR / "templates" / "upcoming_brevets.html").read_text()

    assert 'id="state-filter"' in source
    assert 'id="club-filter"' in source
    assert 'id="distance-filter"' in source
    assert 'id="search-filter"' in source
    assert "populateFilters()" in source
    assert "Auto-generate plans from RWGPS" in source
    assert "Davis, San Francisco, Santa Cruz" not in source
    assert "Filter by Club" not in source
    assert "Team Asha" not in source


def test_brevethub_calendar_template_renders_with_team_asha_context():
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
        body = render_template(
            "upcoming_brevets.html",
            season={"name": "2026-2027"},
            season_label="2026-2027 Season",
            rusa_events=[event],
            future_rides=[],
            completed_events=[],
            is_current=True,
            region_colors={},
            distances=[200],
            current_rider_id=None,
            user_signups={},
            all_ride_plans=[],
            can_edit_rides=True,
            wind_warnings=[],
        )

    assert "Sample 200k" in body
    assert "state-filter" in body
    assert "club-filter" in body
    assert "San Francisco Randonneurs" in body
    assert "Auto-generate plans from RWGPS" in body
    assert "Team Asha" not in body
