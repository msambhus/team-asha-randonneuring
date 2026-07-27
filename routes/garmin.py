"""Private Garmin Connect account connection routes."""
import json
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


@garmin_bp.route("/disconnect", methods=["POST"])
@profile_required
def disconnect():
    if not current_app.config.get("GARMIN_TOKEN_ENCRYPTION_KEY"):
        flash("Garmin Connect is not configured on this server.", "error")
        return redirect(url_for("auth.my_profile"))
    models.delete_garmin_connection(session["rider_id"])
    models.delete_garmin_mfa_challenge(session["rider_id"])
    flash("Garmin Connect disconnected. Stored tokens were deleted.", "success")
    return redirect(url_for("auth.my_profile"))
