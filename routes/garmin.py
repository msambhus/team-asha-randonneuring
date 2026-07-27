"""Private Garmin Connect account connection routes."""
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
            # The upstream MFA state contains a live requests.Session and cannot
            # safely be serialized into a Vercel cookie/database row.
            flash("Garmin requires MFA for this account. Resumable MFA support "
                  "is not enabled yet; no credentials or tokens were stored.",
                  "warning")
            return redirect(url_for("garmin.connect"))

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


@garmin_bp.route("/disconnect", methods=["POST"])
@profile_required
def disconnect():
    if not current_app.config.get("GARMIN_TOKEN_ENCRYPTION_KEY"):
        flash("Garmin Connect is not configured on this server.", "error")
        return redirect(url_for("auth.my_profile"))
    models.delete_garmin_connection(session["rider_id"])
    flash("Garmin Connect disconnected. Stored tokens were deleted.", "success")
    return redirect(url_for("auth.my_profile"))
