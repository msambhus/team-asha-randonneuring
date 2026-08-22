"""Canonical distance labels for brevet and Grand Randonnee results."""

import re


_SPECIAL_DISTANCE_RE = re.compile(
    r"(?<!\d)(1200|1000)\s*(?:k|km)\b", re.IGNORECASE)


def canonical_distance_km(distance_km, name=""):
    """Preserve 1000K/1200K as special rides for display and totals.

    Some imported rows retain a legacy 600K distance even when the linked
    event or plan name explicitly identifies a 1000K or 1200K.
    """
    match = _SPECIAL_DISTANCE_RE.search(str(name or ""))
    if match:
        return int(match.group(1))
    try:
        value = float(distance_km)
    except (TypeError, ValueError):
        return distance_km
    if value >= 1150:
        return 1200
    if value >= 900:
        return 1000
    return int(round(value)) if value.is_integer() else value


def special_distance_km(distance_km, name=""):
    """Return 1000 or 1200 for a special ride, otherwise ``None``."""
    distance = canonical_distance_km(distance_km, name)
    return distance if distance in (1000, 1200) else None
