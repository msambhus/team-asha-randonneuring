"""Fitness score calculation from Strava activities.

Score is 0-100, computed from four weighted components:
  Frequency (25): rides per week
  Volume    (35): total distance + elevation
  Intensity (25): HR zones, power, suffer score (adaptive)
  Recency   (15): exponential decay from last ride
"""
from datetime import datetime, timedelta, timezone
import math


def calculate_fitness_score(activities):
    """Calculate fitness score (0-100) from recent activities.

    Args:
        activities: list of strava_activity dicts (last 28 days)

    Returns:
        dict with total, frequency, volume, intensity, recency scores
        or None if no activities
    """
    if not activities:
        return None

    # Only count cycling activities for a randonneuring club
    rides = [a for a in activities
             if a.get('activity_type') in ('Ride', 'VirtualRide', 'EBikeRide')]

    if not rides:
        return {'total': 0, 'frequency': 0, 'volume': 0, 'intensity': 0, 'recency': 0}

    now = datetime.now(timezone.utc)

    # === FREQUENCY (0-25) ===
    # Target: 4+ rides per week = full score
    weeks = {}
    for r in rides:
        dt = _parse_dt(r.get('start_date_local') or r.get('start_date'))
        if dt:
            week_key = dt.isocalendar()[1]
            weeks[week_key] = weeks.get(week_key, 0) + 1

    if weeks:
        avg_rides_per_week = sum(weeks.values()) / max(len(weeks), 1)
        frequency = min(25, round(avg_rides_per_week / 4.0 * 25))
    else:
        frequency = 0

    # === VOLUME (0-35) ===
    # Distance (20pts): 400km in 4 weeks = full
    # Elevation (15pts): 4000m in 4 weeks = full
    total_distance_km = sum((r.get('distance') or 0) / 1000 for r in rides)
    total_elevation_m = sum(r.get('total_elevation_gain') or 0 for r in rides)

    distance_score = min(20, round(total_distance_km / 400.0 * 20))
    elevation_score = min(15, round(total_elevation_m / 4000.0 * 15))
    volume = distance_score + elevation_score

    # === INTENSITY (0-25) ===
    # Adaptively weights based on available data
    intensity = 0
    intensity_max = 0

    # Heart rate signal (0-10): avg HR as % of max observed
    hr_rides = [r for r in rides if r.get('has_heartrate') and r.get('average_heartrate')]
    if hr_rides:
        intensity_max += 10
        max_hr_observed = max((r.get('max_heartrate') or 0) for r in hr_rides)
        if max_hr_observed > 0:
            avg_hr_pct = sum(r['average_heartrate'] for r in hr_rides) / len(hr_rides) / max_hr_observed
            # Map 0.55-0.85 range to 0-10
            hr_score = min(10, max(0, round((avg_hr_pct - 0.55) / 0.30 * 10)))
            intensity += hr_score

    # Power signal (0-10): weighted average watts
    power_rides = [r for r in rides if r.get('device_watts') and r.get('weighted_average_watts')]
    if power_rides:
        intensity_max += 10
        avg_npower = sum(r['weighted_average_watts'] for r in power_rides) / len(power_rides)
        # 180W normalized power = full score for amateur endurance cyclists
        power_score = min(10, round(avg_npower / 180.0 * 10))
        intensity += power_score

    # Suffer score signal (0-10): Strava's own intensity metric
    suffer_rides = [r for r in rides if r.get('suffer_score') and r['suffer_score'] > 0]
    if suffer_rides:
        intensity_max += 10
        avg_suffer = sum(r['suffer_score'] for r in suffer_rides) / len(suffer_rides)
        suffer_score_val = min(10, round(avg_suffer / 80.0 * 10))
        intensity += suffer_score_val

    # Normalize intensity to 0-25 range
    if intensity_max > 0:
        intensity = round(intensity / intensity_max * 25)
    else:
        # No sensor data — fallback to avg ride duration
        avg_duration_min = sum((r.get('moving_time') or 0) / 60 for r in rides) / len(rides)
        intensity = min(15, round(avg_duration_min / 120.0 * 15))

    # === RECENCY (0-15) ===
    # Exponential decay: 15 * exp(-days_since / 10)
    most_recent = None
    for r in rides:
        dt = _parse_dt(r.get('start_date_local') or r.get('start_date'))
        if dt and (most_recent is None or dt > most_recent):
            most_recent = dt

    if most_recent:
        # Ensure timezone-aware comparison
        if most_recent.tzinfo is None:
            most_recent = most_recent.replace(tzinfo=timezone.utc)
        days_since = max(0, (now - most_recent).days)
        recency = max(0, round(15 * math.exp(-days_since / 10.0)))
    else:
        recency = 0

    total = min(100, max(0, frequency + volume + intensity + recency))

    return {
        'total': total,
        'frequency': frequency,
        'volume': volume,
        'intensity': intensity,
        'recency': recency,
    }


def _parse_dt(val):
    """Parse a datetime value that may be a string, datetime, or None."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        # Handle ISO 8601 formats from Strava
        try:
            return datetime.fromisoformat(val.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            return None
    return None
