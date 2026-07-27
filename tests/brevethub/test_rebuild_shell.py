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
    assert "admin.dashboard" in endpoints
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


def test_home_page_has_no_aggregate_statistics_or_alternate_template():
    source = (BREVETHUB_DIR / "templates" / "landing.html").read_text().lower()

    assert not (BREVETHUB_DIR / "templates" / "index.html").exists()
    assert "brevethub statistics" not in source
    assert "all-time brevet kms" not in source
    assert "active riders" not in source
    assert "super randonneurs" not in source
    assert "season_summaries" not in source


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
        for name in ("base.html", "landing.html", "upcoming_brevets.html")
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


def test_public_rider_profile_is_rusa_only_and_owner_profile_keeps_strava():
    public_source = (
        BREVETHUB_DIR / "templates" / "rider_profile.html"
    ).read_text().lower()
    owner_source = (
        BREVETHUB_DIR / "templates" / "profile.html"
    ).read_text().lower()
    model_source = (BREVETHUB_DIR / "models.py").read_text().lower()

    assert "eddington" not in public_source
    assert "strava" not in public_source
    assert "activity" not in public_source
    assert "eddington_miles=target" not in (
        BREVETHUB_DIR / "routes" / "riders.py"
    ).read_text()

    club_rider_query = model_source.split(
        "def get_club_rider(", 1
    )[1].split("def get_public_rider(", 1)[0]
    assert "eddington" not in club_rider_query

    assert "eddington" in owner_source
    assert "riders.my_strava_analysis" in owner_source


def test_club_directory_gets_connection_gated_eddington_without_public_leak():
    model_source = (BREVETHUB_DIR / "models.py").read_text().lower()
    directory_query = model_source.split(
        "def get_club_riders_with_rusa(", 1
    )[1].split("def get_club_rider(", 1)[0]
    public_query = model_source.split(
        "def get_public_rider(", 1
    )[1].split("# --------------------------------------------------------------------------- #", 1)[0]

    assert "left join rp_strava_connection" in directory_query
    assert "case when sc.id is not null then r.eddington_miles" in directory_query
    assert "eddington" not in public_query


def test_private_strava_index_visually_separates_brevets_and_regular_rides():
    source = (
        BREVETHUB_DIR / "templates" / "my_strava_analysis.html"
    ).read_text()

    assert "My Strava Rides" in source
    assert "Regular ride" in source
    assert "card.is_brevet" in source
    assert ">Stats<" not in source  # Jinja/HTML whitespace is intentional.
    assert "            Stats" in source


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
    assert "Find official registration via RUSA" in source
    assert "riders.upcoming_brevets" not in source


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
        "interested_count": 1,
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
                               rider={"id": 1}, club=None, states=['CA'],
                               regions_by_state={'CA': ['San Francisco']},
                               my_status={}, my_results=[], degraded=None,
                               weather={},
                               rusa_event_search_url=(
                                   "https://rusa.org/cgi-bin/"
                                   "eventsearch_PF.pl?sortby=date"))

    assert "Sample 200k" in body
    assert "region-state" in body
    assert "region-area" in body
    assert "San Francisco" in body
    assert "3 going" in body
    assert "1 interested" in body
    assert "Find official registration via RUSA" in body
    assert "Team Asha" not in body


def test_brevethub_calendar_requests_all_national_sanctioned_events():
    source = (
        BREVETHUB_DIR / "routes" / "calendar.py"
    ).read_text()

    assert "include_all_sanctioned=True" in source


def test_brevethub_signup_does_not_offer_maybe_as_a_new_intent():
    from brevethub.routes.calendar import _SIGNUP_STATUSES

    assert _SIGNUP_STATUSES == {"going", "interested", "withdraw"}


def test_brevethub_templates_use_the_active_calendar_endpoint():
    for name in ("live_hub.html", "riders.html"):
        source = (BREVETHUB_DIR / "templates" / name).read_text()
        assert "riders.upcoming_brevets" not in source
        assert "calendar.calendar" in source
