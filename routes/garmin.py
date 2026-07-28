"""Private Garmin Connect account connection routes."""
import json
from datetime import date, timedelta
from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, session, url_for)

import models
from auth import profile_required
from services.garmin_connect import GarminPerformanceClient
from services.garmin_tokens import GarminTokenCipher
from vendor.python_garminconnect import (
    Client,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

garmin_bp = Blueprint("garmin", __name__)


def _cipher():
    return GarminTokenCipher(
        current_app.config.get("GARMIN_TOKEN_ENCRYPTION_KEY"))


def _enrich_one_matched_activity(rider_id, performance):
    """Best-effort detail sync; summary/history sync must still succeed."""
    try:
        candidates = models.get_garmin_detail_sync_candidates(
            rider_id, limit=1)
        for activity_id in candidates:
            activity_summary = performance.activity(activity_id)
            split_payload = performance.activity_splits(activity_id)
            normalized = performance.normalize_activity(activity_summary)
            laps = performance.normalize_splits(split_payload)
            detail_ciphertext = _cipher().encrypt(json.dumps(
                {"activity": activity_summary, "splits": split_payload},
                separators=(",", ":"), default=str))
            models.upsert_garmin_activity_detail(
                rider_id, activity_id, normalized, laps, detail_ciphertext)
    except Exception:
        # A phased deploy or one unsupported activity must not undo the already
        # committed recovery snapshot and resumable history page.
        try:
            models.get_db().rollback()
        except Exception:
            pass
        current_app.logger.warning(
            "Garmin activity detail enrichment deferred for rider %s",
            rider_id, exc_info=True)


@garmin_bp.route("/connect", methods=["GET", "POST"])
@profile_required
def connect():
    rider_id = session["rider_id"]
    configured = bool(current_app.config.get("GARMIN_TOKEN_ENCRYPTION_KEY"))
    if configured and models.get_garmin_connection(rider_id):
        flash("Garmin Connect is already connected.", "info")
        return redirect(url_for("auth.my_profile"))
    if request.method == "GET":
        return render_template("garmin_connect.html", configured=configured)
    if not configured:
        flash("Garmin Connect is not configured on this server.", "error")
        return redirect(url_for("garmin.connect"))

    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""
    if not email or not password:
        flash("Enter your Garmin email and password.", "error")
        return render_template("garmin_connect.html", configured=True,
                               email=email)

    # Credentials remain request-local and are never passed to models/logging.
    auth_client = Client()
    try:
        mfa_status, _ = auth_client.login(
            email, password, return_on_mfa=True)
        if mfa_status:
            state_json = json.dumps(auth_client.export_mfa_state(),
                                    separators=(",", ":"))
            if len(state_json.encode("utf-8")) > 262_144:
                raise GarminConnectConnectionError(
                    "Garmin MFA challenge state is unexpectedly large")
            models.save_garmin_mfa_challenge(
                rider_id, _cipher().encrypt(state_json))
            flash("Enter the verification code Garmin sent you.", "info")
            return redirect(url_for("garmin.mfa"))

        performance = GarminPerformanceClient(auth_client)
        profile = performance.profile()
        encrypted = _cipher().encrypt(performance.dump_tokens())
        models.upsert_garmin_connection(
            rider_id, encrypted, profile.get("displayName"))
    except GarminConnectAuthenticationError:
        flash("Garmin rejected those credentials.", "error")
        return render_template("garmin_connect.html", configured=True,
                               email=email)
    except GarminConnectTooManyRequestsError:
        flash("Garmin is rate limiting sign-ins. Please try again later.",
              "warning")
        return redirect(url_for("garmin.connect"))
    except (GarminConnectConnectionError, ValueError):
        current_app.logger.warning(
            "Garmin connection failed for rider %s", rider_id)
        flash("Could not connect to Garmin right now. No credentials were stored.",
              "error")
        return redirect(url_for("garmin.connect"))

    flash("Garmin Connect linked securely.", "success")
    return redirect(url_for("auth.my_profile"))


@garmin_bp.route("/mfa", methods=["GET", "POST"])
@profile_required
def mfa():
    if not current_app.config.get("GARMIN_TOKEN_ENCRYPTION_KEY"):
        flash("Garmin Connect is not configured on this server.", "error")
        return redirect(url_for("garmin.connect"))
    if request.method == "GET":
        return render_template("garmin_mfa.html")

    code = "".join((request.form.get("code") or "").split())
    if not code or len(code) > 12:
        flash("Enter the verification code from Garmin.", "error")
        return render_template("garmin_mfa.html")

    rider_id = session["rider_id"]
    challenge = models.take_garmin_mfa_attempt(rider_id)
    if not challenge:
        models.delete_garmin_mfa_challenge(rider_id)
        flash("That Garmin verification challenge expired. Sign in again.",
              "warning")
        return redirect(url_for("garmin.connect"))

    try:
        state = json.loads(_cipher().decrypt(challenge["state_ciphertext"]))
        auth_client = Client()
        auth_client.import_mfa_state(state)
        auth_client.resume_login(None, code)
        performance = GarminPerformanceClient(auth_client)
        profile = performance.profile()
        encrypted = _cipher().encrypt(performance.dump_tokens())
        models.upsert_garmin_connection(
            rider_id, encrypted, profile.get("displayName"))
    except GarminConnectAuthenticationError:
        if challenge["attempts"] >= 5:
            models.delete_garmin_mfa_challenge(rider_id)
            flash("Too many incorrect codes. Sign in to Garmin again.", "error")
            return redirect(url_for("garmin.connect"))
        flash("Garmin rejected that code. Check it and try again.", "error")
        return render_template("garmin_mfa.html")
    except GarminConnectTooManyRequestsError:
        flash("Garmin is rate limiting verification. Try again later.",
              "warning")
        return render_template("garmin_mfa.html")
    except (GarminConnectConnectionError, ValueError, json.JSONDecodeError):
        current_app.logger.warning(
            "Garmin MFA completion failed for rider %s", rider_id)
        models.delete_garmin_mfa_challenge(rider_id)
        flash("Could not complete Garmin verification. Sign in again.", "error")
        return redirect(url_for("garmin.connect"))

    models.delete_garmin_mfa_challenge(rider_id)
    flash("Garmin Connect linked securely.", "success")
    return redirect(url_for("auth.my_profile"))


@garmin_bp.route("/sync", methods=["POST"])
@profile_required
def sync():
    rider_id = session["rider_id"]
    imported_count = 0
    connection = models.get_garmin_connection(rider_id, include_tokens=True)
    if not connection:
        flash("Connect Garmin before syncing performance data.", "warning")
        return redirect(url_for("garmin.connect"))

    try:
        performance = GarminPerformanceClient()
        performance.load_tokens(
            _cipher().decrypt(connection["token_ciphertext"]))
        snapshot = performance.performance_snapshot(date.today())
        raw_ciphertext = _cipher().encrypt(json.dumps(
            snapshot["raw"], separators=(",", ":"), default=str))
        refreshed_tokens = _cipher().encrypt(performance.dump_tokens())
        models.upsert_garmin_performance_snapshot(
            rider_id, snapshot, raw_ciphertext, refreshed_tokens)
        default_cutoff = date.today() - timedelta(days=365)
        history_complete = bool(connection.get("activity_history_complete"))
        if history_complete:
            # Completed riders still refresh the newest page for new rides.
            cutoff = default_cutoff
            start = 0
            max_pages = 1
        else:
            cutoff = connection.get("activity_sync_since") or default_cutoff
            start = int(connection.get("activity_sync_cursor") or 0)
            # One page per request means the encrypted rows and continuation
            # cursor commit together before another Garmin API call can fail.
            max_pages = 1
        batch = performance.activity_history_batch(
            cutoff, start=start, max_pages=max_pages)
        for raw_page in batch["pages"]:
            imported = []
            for raw_activity in raw_page:
                normalized = performance.normalize_activity(raw_activity)
                activity_ciphertext = _cipher().encrypt(json.dumps(
                    raw_activity, separators=(",", ":"), default=str))
                imported.append((normalized, activity_ciphertext))
            # Commit each page independently. If Garmin rate-limits a later
            # page, completed history remains safely stored and resumable.
            models.upsert_garmin_activities(rider_id, imported)
            imported_count += len(imported)
        models.update_garmin_activity_sync_state(
            rider_id,
            cursor=batch["next_start"],
            since=cutoff,
            complete=batch["complete"] or history_complete,
        )
        from services.activity_matching import refresh_activity_matches_safely
        refresh_activity_matches_safely(rider_id)
        # One matched recording per request keeps Garmin calls and Vercel
        # execution time bounded while repeated syncs make durable progress.
        _enrich_one_matched_activity(rider_id, performance)
    except GarminConnectAuthenticationError:
        models.mark_garmin_reauth_required(rider_id)
        flash("Garmin authorization expired. Disconnect and reconnect Garmin.",
              "warning")
        return redirect(url_for("auth.my_profile"))
    except GarminConnectTooManyRequestsError:
        if imported_count:
            from services.activity_matching import (
                refresh_activity_matches_safely)
            refresh_activity_matches_safely(rider_id)
            flash(
                f"Garmin imported {imported_count} rides before rate limiting. "
                "Sync again later to continue the one-year history.",
                "warning",
            )
            return redirect(url_for("auth.my_profile"))
        flash("Garmin is rate limiting sync. Try again later.", "warning")
        return redirect(url_for("auth.my_profile"))
    except (GarminConnectConnectionError, ValueError):
        current_app.logger.warning(
            "Garmin performance sync failed for rider %s", rider_id)
        flash("Could not sync Garmin performance data right now.", "error")
        return redirect(url_for("auth.my_profile"))

    if not batch["complete"] and not history_complete:
        flash(
            f"Garmin imported {imported_count} cycling activities. More "
            "one-year history remains; sync again to continue.",
            "success",
        )
    else:
        flash(
            f"Garmin performance data and {imported_count} cycling "
            "activities synced.",
            "success",
        )
    return redirect(url_for("auth.my_profile"))


@garmin_bp.route("/disconnect", methods=["POST"])
@profile_required
def disconnect():
    if request.form.get("confirm_delete") != "DELETE":
        flash("Confirm permanent deletion before disconnecting Garmin.",
              "warning")
        return redirect(url_for("auth.my_profile"))
    rider_id = session["rider_id"]
    try:
        models.delete_garmin_connection(rider_id)
    except Exception:
        current_app.logger.exception(
            "Garmin deletion failed for rider %s", rider_id)
        flash(
            "Garmin data could not be deleted right now. Nothing was "
            "partially removed; please try again.",
            "error",
        )
        return redirect(url_for("auth.my_profile"))
    flash(
        "Garmin disconnected. Tokens, recovery snapshots, and imported "
        "Garmin activities were permanently deleted.",
        "success",
    )
    return redirect(url_for("auth.my_profile"))


@garmin_bp.route("/ride-matches")
@profile_required
def ride_matches():
    """Private review surface for Garmin-to-brevet associations."""
    rider_id = session["rider_id"]
    # Matching is derived and idempotent. Refresh here so a rider does not
    # need another provider sync after a matching/schema deployment.
    from services.activity_matching import refresh_activity_matches_safely
    refresh_activity_matches_safely(rider_id)
    activities = models.get_garmin_brevet_match_review(rider_id)
    brevets = models.get_finished_brevets_for_matching(rider_id)
    summary = {
        "garmin": len(activities),
        "strava": sum(bool(row.get("strava_activity_id"))
                      for row in activities),
        "brevets": sum(bool(row.get("ride_id"))
                       and row.get("match_status") != "rejected"
                       for row in activities),
    }
    rider = models.get_rider_by_id(rider_id)
    return render_template(
        "garmin_ride_matches.html",
        activities=activities,
        brevets=brevets,
        summary=summary,
        current_rusa_id=(rider or {}).get("rusa_id"),
    )


@garmin_bp.route("/ride-matches/<int:garmin_activity_id>", methods=["POST"])
@profile_required
def update_ride_match(garmin_activity_id):
    """Create, correct, or reject a rider-owned Garmin brevet link."""
    rider_id = session["rider_id"]
    action = request.form.get("action")
    try:
        if action == "unlink":
            if not models.reject_garmin_brevet_match(
                    rider_id, garmin_activity_id):
                flash("That Garmin ride did not have an active brevet link.",
                      "info")
            else:
                flash("Garmin ride unlinked. Future syncs will preserve this.",
                      "success")
        elif action == "link":
            ride_id = int(request.form.get("ride_id") or 0)
            models.set_manual_garmin_brevet_match(
                rider_id, garmin_activity_id, ride_id)
            flash("Garmin ride linked to the selected finished brevet.",
                  "success")
        else:
            flash("Choose a valid matching action.", "warning")
    except (TypeError, ValueError):
        current_app.logger.warning(
            "Rejected invalid Garmin brevet match for rider %s", rider_id)
        flash("That Garmin ride or brevet is not available to your account.",
              "error")
    except Exception:
        current_app.logger.exception(
            "Garmin brevet match update failed for rider %s", rider_id)
        flash("Could not update that ride match right now.", "error")
    return redirect(url_for("garmin.ride_matches"))
