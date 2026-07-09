"""services/email_service.py — transactional email via Resend.

Currently used only for login OTP emails (services/otp_service.py). Reads
``RESEND_API_KEY`` and ``OTP_FROM_EMAIL`` from the environment. If the API key is
unset the send is a logged no-op that returns False (so local dev / tests without
mail config degrade gracefully, mirroring the BRAINTRUST_API_KEY pattern) — the
caller decides how to surface a delivery failure and must never 500 because of it.

Sender defaults to a Team Asha address on the already-verified ``thrrive.fit``
domain; override with ``OTP_FROM_EMAIL`` once a dedicated domain is verified.
"""
import os

import requests
from flask import current_app

RESEND_ENDPOINT = 'https://api.resend.com/emails'
# thrrive.fit is verified in the Resend account; any local-part is allowed on a
# verified domain (no mailbox needed). Swap via OTP_FROM_EMAIL when a Team Asha
# domain is verified.
DEFAULT_FROM = 'Team Asha Randonneuring <teamasha@thrrive.fit>'
_TIMEOUT = 10


def send_email(to, subject, html, text=None):
    """Send one transactional email. Returns True on success, False otherwise.

    Never raises: a mail failure must not break the request that triggered it.
    Returns False (a logged no-op) when RESEND_API_KEY is unset.
    """
    api_key = os.environ.get('RESEND_API_KEY')
    if not api_key:
        current_app.logger.warning('email_service: RESEND_API_KEY unset; skipping email to %s', to)
        return False

    payload = {
        'from': os.environ.get('OTP_FROM_EMAIL', DEFAULT_FROM),
        'to': [to],
        'subject': subject,
        'html': html,
    }
    if text:
        payload['text'] = text

    try:
        resp = requests.post(
            RESEND_ENDPOINT,
            json=payload,
            headers={'Authorization': f'Bearer {api_key}'},
            timeout=_TIMEOUT,
        )
    except requests.RequestException:
        current_app.logger.exception('email_service: request to Resend failed')
        return False

    if resp.status_code >= 400:
        # Resend puts the reason in the body; truncate so a huge body can't spam logs.
        current_app.logger.error('email_service: Resend returned %s: %s', resp.status_code, resp.text[:500])
        return False
    return True
