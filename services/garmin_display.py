"""Rider-facing labels for Garmin's internal metric tokens."""

_TRAINING_STATUS_LABELS = {
    "PRODUCTIVE": "Productive",
    "PEAKING": "Peaking",
    "MAINTAINING": "Maintaining",
    "RECOVERY": "Recovery",
    "STRAINED": "Strained",
    "UNPRODUCTIVE": "Unproductive",
    "DETRAINING": "Detraining",
    "OVERREACHING": "Overreaching",
    "PAUSED": "Paused",
    "NO_STATUS": "No Training Status",
    "NO_STATUS_AER_LOW_SHORT": "No Training Status",
}

_NO_STATUS_DESCRIPTION = "Garmin needs more qualifying aerobic activity data."


def training_status_label(value):
    """Convert a Garmin status/feedback token into a concise UI label."""
    if value is None:
        return None
    token = str(value).strip().upper()
    if not token:
        return None
    if token.startswith("NO_STATUS"):
        return "No Training Status"
    return _TRAINING_STATUS_LABELS.get(
        token, token.replace("_", " ").title())


def training_status_description(value):
    """Explain statuses that otherwise need Garmin-internal context."""
    if value is None:
        return None
    token = str(value).strip().upper()
    if token.startswith("NO_STATUS"):
        return _NO_STATUS_DESCRIPTION
    return None
