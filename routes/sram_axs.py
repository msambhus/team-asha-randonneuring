"""Private SRAM AXS connection and gearing sync routes."""
import json

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, session, url_for)

import models
from auth import profile_required
from services.sram_axs import (
    SramAxsAuthenticationError,
    SramAxsClient,
    SramAxsConnectionError,
    SramAxsRateLimitError,
    SramTokenCipher,
)
from services.sram_matching import build_sram_match


sram_axs_bp = Blueprint("sram_axs", __name__)


def _cipher():
    return SramTokenCipher(
        current_app.config.get("SRAM_AXS_TOKEN_ENCRYPTION_KEY"))


def _import_activity_detail(client, rider_id, activity_id, component_ids):
    """Fetch and persist one bounded, owner-scoped AXS component payload."""
    full_activity = client.activity(activity_id)
    summary_ids = component_ids or [
        row.get("id")
        for row in full_activity.get("componentsummary_set", [])
        if isinstance(row, dict) and row.get("id")
    ]
    component_rows = [
        client.component_summary(summary_id)
        for summary_id in summary_ids[:30]
    ]
    components = client.normalize_components(component_rows)
    gear_summary = client.gear_summary(components)
    models.upsert_sram_axs_activity_detail(
        rider_id, activity_id, gear_summary, components,
        _cipher().encrypt(json.dumps(
            {"activity": full_activity, "components": component_rows},
            separators=(",", ":"), default=str)))


@sram_axs_bp.route("/connect", methods=["GET", "POST"])
@profile_required
def connect():
    rider_id = session["rider_id"]
    configured = bool(
        current_app.config.get("SRAM_AXS_TOKEN_ENCRYPTION_KEY"))
    connection = (
        models.get_sram_axs_connection(rider_id) if configured else None)
    reconnecting = bool(
        connection
        and (
            connection.get("status") == "reauth_required"
            or request.args.get("reconnect") == "1"
        )
    )
    connect_url = url_for(
        "sram_axs.connect", reconnect=1) if connection else url_for(
            "sram_axs.connect")
    if configured and connection and not reconnecting:
        flash("SRAM AXS is already connected.", "info")
        return redirect(url_for("auth.my_profile"))
    if request.method == "GET":
        return render_template(
            "sram_axs_connect.html",
            configured=configured,
            reconnecting=reconnecting,
            email=(connection or {}).get("display_name"),
        )
    if not configured:
        flash("SRAM AXS is not configured on this server.", "error")
        return redirect(url_for("sram_axs.connect"))

    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""
    if not email or not password:
        flash("Enter your SRAM ID email and password.", "error")
        return render_template(
            "sram_axs_connect.html", configured=True, email=email,
            reconnecting=reconnecting)

    # The password remains request-local and never crosses into model/log calls.
    client = SramAxsClient()
    try:
        client.login(email, password)
        models.upsert_sram_axs_connection(
            rider_id, _cipher().encrypt(client.dump_tokens()),
            client.display_name() or email)
    except SramAxsAuthenticationError:
        flash(
            "SRAM rejected those credentials or requires a verification "
            "step AXS Web does not expose to connectors.", "error")
        return render_template(
            "sram_axs_connect.html", configured=True, email=email,
            reconnecting=reconnecting)
    except SramAxsRateLimitError:
        flash("SRAM is rate limiting sign-ins. Try again later.", "warning")
        return redirect(connect_url)
    except (SramAxsConnectionError, ValueError):
        current_app.logger.warning(
            "SRAM AXS connection failed for rider %s", rider_id)
        flash("Could not connect to SRAM AXS. No credentials were stored.",
              "error")
        return redirect(connect_url)

    if connection:
        flash(
            "SRAM AXS session renewed. Existing activities, matches, and "
            "Stats were preserved; your password was not stored.",
            "success",
        )
    else:
        flash(
            "SRAM AXS linked securely. Your password was not stored.",
            "success",
        )
    return redirect(url_for("auth.my_profile"))


@sram_axs_bp.route("/sync", methods=["POST"])
@profile_required
def sync():
    rider_id = session["rider_id"]
    connection = models.get_sram_axs_connection(
        rider_id, include_tokens=True)
    if not connection:
        flash("Connect SRAM AXS before syncing.", "warning")
        return redirect(url_for("sram_axs.connect"))
    try:
        token_json = _cipher().decrypt(connection["token_ciphertext"])
        client = SramAxsClient.from_token_json(token_json)
        rows = client.activities(page=1, page_size=50)
        imported = 0
        matched = 0
        matched_details_imported = 0
        for index, source in enumerate(rows):
            normalized = client.normalize_activity(source)
            if not normalized["sram_activity_id"]:
                continue
            raw_ciphertext = _cipher().encrypt(json.dumps(
                source, separators=(",", ":"), default=str))
            models.upsert_sram_axs_activity(
                rider_id, normalized, raw_ciphertext)

            candidates = models.get_sram_axs_match_candidates(
                rider_id, normalized)
            match = build_sram_match(normalized, candidates)
            if match:
                models.upsert_sram_axs_match(
                    rider_id, normalized["sram_activity_id"], match)
                matched += 1

            # Component telemetry is large and request-heavy. Backfill up to
            # five matched rides that lack detail per sync, while also
            # refreshing the newest three activities. Repeated syncs therefore
            # make bounded forward progress without timing out the request.
            needs_matched_detail = (
                bool(match)
                and matched_details_imported < 5
                and not models.has_sram_axs_activity_detail(
                    rider_id, normalized["sram_activity_id"])
            )
            if needs_matched_detail or index < 3:
                try:
                    _import_activity_detail(
                        client, rider_id, normalized["sram_activity_id"],
                        normalized.get("component_ids"))
                    if needs_matched_detail:
                        matched_details_imported += 1
                except (SramAxsConnectionError, SramAxsRateLimitError,
                        ValueError, json.JSONDecodeError):
                    current_app.logger.warning(
                        "SRAM AXS detail enrichment failed for rider %s "
                        "activity %s; summary sync continues",
                        rider_id, normalized["sram_activity_id"],
                        exc_info=True)

            imported += 1
        # The provider list is page-bounded, but older matched brevets still
        # need component detail. Advance a small backlog on every sync.
        remaining = max(0, 5 - matched_details_imported)
        if remaining:
            for pending in models.get_sram_axs_detail_backfill_candidates(
                    rider_id, limit=remaining):
                try:
                    _import_activity_detail(
                        client, rider_id, pending["sram_activity_id"],
                        pending.get("component_ids"))
                    matched_details_imported += 1
                except (SramAxsConnectionError, SramAxsRateLimitError,
                        ValueError, json.JSONDecodeError):
                    current_app.logger.warning(
                        "SRAM AXS backlog detail enrichment failed for rider "
                        "%s activity %s", rider_id,
                        pending["sram_activity_id"], exc_info=True)
        models.mark_sram_axs_sync(rider_id)
    except SramAxsAuthenticationError:
        models.mark_sram_axs_sync(
            rider_id, "SRAM session expired; reconnect SRAM AXS")
        flash("Your SRAM session expired. Reconnect SRAM AXS.", "warning")
        return redirect(url_for("sram_axs.connect"))
    except SramAxsRateLimitError:
        flash("SRAM AXS is rate limiting sync. Try again later.", "warning")
        return redirect(url_for("auth.my_profile"))
    except (SramAxsConnectionError, ValueError, json.JSONDecodeError):
        current_app.logger.warning(
            "SRAM AXS sync failed for rider %s", rider_id, exc_info=True)
        flash("SRAM AXS sync failed without changing your connection.",
              "error")
        return redirect(url_for("auth.my_profile"))

    flash(
        f"Synced {imported} SRAM AXS activities; "
        f"matched {matched} with existing rides and enriched "
        f"{matched_details_imported} matched rides.", "success")
    return redirect(url_for("auth.my_profile"))


@sram_axs_bp.route("/activities")
@profile_required
def activities():
    rows = models.get_sram_axs_activities(session["rider_id"], limit=100)
    return render_template("sram_axs_activities.html", activities=rows)


@sram_axs_bp.route("/disconnect", methods=["POST"])
@profile_required
def disconnect():
    if request.form.get("confirm_delete") != "DELETE":
        flash("Confirm permanent SRAM AXS data deletion.", "warning")
        return redirect(url_for("auth.my_profile"))
    models.delete_sram_axs_connection(session["rider_id"])
    flash("SRAM AXS disconnected and all imported SRAM data deleted.",
          "success")
    return redirect(url_for("auth.my_profile"))
