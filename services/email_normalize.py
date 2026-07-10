"""services/email_normalize.py — canonicalize an email for identity matching.

Gmail ignores dots and +tags in the local part (mihir.sambhus@ and
mihirsambhus+foo@ are the same inbox as mihirsambhus@gmail.com), but our
app_user rows are keyed on the literal string, so those variants would create
duplicate accounts. This maps such variants to one canonical form used for
account lookup (see models.get_user_by_normalized_email and the
app_user.email_normalized column, migration 031).

Normalization is intentionally conservative: only gmail.com / googlemail.com get
dot/+tag stripping (that aliasing is Gmail-specific and documented). Every other
domain is just lowercased, so we never merge two genuinely different addresses.
The SQL backfill in migration 031 mirrors this logic — keep them in sync.
"""

_GMAIL_DOMAINS = {'gmail.com', 'googlemail.com'}


def normalize_email(email):
    """Return the canonical form of ``email`` for identity matching.

    Lowercases and trims. For Gmail addresses, strips a ``+tag`` suffix and all
    dots from the local part and collapses googlemail.com → gmail.com. Returns
    the lowercased input unchanged if it isn't a parseable ``local@domain``.
    """
    e = (email or '').strip().lower()
    if '@' not in e:
        return e
    local, _, domain = e.rpartition('@')
    if not local or not domain:
        return e
    if domain in _GMAIL_DOMAINS:
        local = local.split('+', 1)[0].replace('.', '')
        domain = 'gmail.com'
    return f'{local}@{domain}'
