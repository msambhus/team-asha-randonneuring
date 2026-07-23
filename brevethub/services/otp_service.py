"""services/otp_service.py — passwordless email OTP for the mobile app.

Pure helpers for the login OTP flow (crypto + email composition); persistence
lives in models.py and the HTTP surface in routes/api_auth.py. Phase 1 is email
only; phase 2 will reuse the same auth_otp table + verify logic for SMS.

Security model:
  * The 6-digit code is LOW entropy, so it gets a salted, slow werkzeug hash, a
    short TTL, and a per-code attempts cap (enforced in the route).
  * The magic-link token is HIGH entropy (256 bits), so it gets a fast,
    deterministic sha256 hash — deterministic so the link can be looked up by
    hash, safe because guessing it is infeasible.
  * We never persist either plaintext, so a DB leak can't be replayed.
"""
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

from werkzeug.security import check_password_hash, generate_password_hash

# Tunables. Kept conservative: a code is usable for 15 min, at most 5 wrong tries,
# and a member can request at most 5 codes/hour per email with a 30s cooldown
# between them. IP_MAX_PER_HOUR caps requests from one source across ALL emails,
# so the per-email limit can't be sidestepped by email-bombing many addresses.
CODE_TTL_MINUTES = 15
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 30
MAX_PER_HOUR = 5
IP_MAX_PER_HOUR = 30
MAX_EMAIL_LEN = 255      # app_user.email / auth_otp.identifier are VARCHAR(255)
MAX_PHONE_LEN = 32       # app_user.phone is VARCHAR(32)


def valid_phone(phone):
    """Loose sanity check on an optional phone: digits/space/()+-. only, length
    capped so an overlong/garbage value can't reach the DB and 500 the request.
    Empty/None is treated as 'not provided' by the caller, so this only runs on
    a non-empty value."""
    import re
    return bool(phone) and len(phone) <= MAX_PHONE_LEN and bool(re.match(r'^[0-9()+\-.\s]{7,}$', phone))

_DEFAULT_BASE_URL = 'https://team-asha-randonneuring.vercel.app'
# Custom scheme the Expo app registers (mobile/app.json "scheme": "teamasha").
_APP_DEEP_LINK = 'teamasha://auth/otp'


def generate_code():
    """A cryptographically-random, zero-padded 6-digit string ('000000'-'999999')."""
    return f'{secrets.randbelow(1_000_000):06d}'


def new_link_token():
    """A high-entropy, URL-safe magic-link token."""
    return secrets.token_urlsafe(32)


def hash_code(code):
    """Salted, slow hash of the 6-digit code (verify with :func:`verify_code`)."""
    return generate_password_hash(code)


def verify_code(code, code_hash):
    """Constant-time check of a submitted code against its stored hash."""
    if not code or not code_hash:
        return False
    return check_password_hash(code_hash, code)


def hash_link_token(token):
    """Deterministic sha256 hex of a magic-link token, for direct lookup by hash."""
    return hashlib.sha256((token or '').encode('utf-8')).hexdigest()


def expiry_from_now():
    """Timezone-aware expiry timestamp CODE_TTL_MINUTES in the future."""
    return datetime.now(timezone.utc) + timedelta(minutes=CODE_TTL_MINUTES)


def base_url():
    """Public base URL for magic links (env APP_BASE_URL, else the prod default)."""
    return (os.environ.get('APP_BASE_URL') or _DEFAULT_BASE_URL).rstrip('/')


def magic_url(link_token):
    """The https magic link that opens the app (via routes/api_auth.otp_magic)."""
    return f'{base_url()}/api/auth/otp/magic?token={link_token}'


def app_deep_link(link_token):
    """The teamasha:// deep link the magic-link interstitial redirects into."""
    return f'{_APP_DEEP_LINK}?token={link_token}'


def _email_html(code, link):
    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:420px;margin:0 auto;color:#111827">
  <h2 style="color:#1a2a4f">Team Asha Randonneuring</h2>
  <p>Your login code is:</p>
  <p style="font-size:32px;font-weight:700;letter-spacing:6px;margin:12px 0">{code}</p>
  <p>Enter it in the app, or <a href="{link}" style="color:#1a2a4f">tap here to sign in</a>.</p>
  <p style="color:#6b7280;font-size:13px">This code expires in {CODE_TTL_MINUTES} minutes. If you didn't request it, you can ignore this email.</p>
</div>"""


def _email_text(code, link):
    return (
        f'Your Team Asha Randonneuring login code is: {code}\n\n'
        f'Enter it in the app, or sign in here: {link}\n\n'
        f'This code expires in {CODE_TTL_MINUTES} minutes. '
        f"If you didn't request it, you can ignore this email."
    )


def send_otp_email(email, code, link_token):
    """Compose and send the login OTP email. Returns True on success.

    Imported lazily so a mail-layer import can't drag into unrelated code paths.
    """
    from services.email_service import send_email

    link = magic_url(link_token)
    return send_email(
        to=email,
        subject='Your Team Asha login code',
        html=_email_html(code, link),
        text=_email_text(code, link),
    )
