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


# ========== PER-RIDE SCORING ==========

_CYCLING_TYPES = ('Ride', 'VirtualRide', 'EBikeRide')

_GRADE_MAP = [
    (70, 'A', '#38a169'),   # green
    (50, 'B', '#2b6cb0'),   # blue
    (30, 'C', '#d69e2e'),   # amber
    (15, 'D', '#e53e3e'),   # red
    (0,  'F', '#9b2c2c'),   # dark red
]


def _grade_from_score(score):
    """Return (grade, color) tuple for a 0-100 score."""
    for threshold, grade, color in _GRADE_MAP:
        if score >= threshold:
            return grade, color
    return 'F', '#9b2c2c'


def calculate_per_ride_score(activity, previous_activities):
    """Score a single training ride 0-100.

    Components:
      Distance       (30): 60 km single ride = full marks
      Elevation      (20): 1000 m gain = full marks
      Intensity      (25): Adaptive (HR / power / suffer / duration fallback)
      Prog. Overload (25): Compare vs average of previous 14-day rides

    Returns dict with total, grade, color, trend, and component scores.
    """
    dist_m = activity.get('distance') or 0
    dist_km = dist_m / 1000.0
    elev_m = activity.get('total_elevation_gain') or 0
    moving_sec = activity.get('moving_time') or 0

    # --- Distance (0-30) ---
    distance_pts = min(30, round(dist_km / 60.0 * 30))

    # --- Elevation (0-20) ---
    elevation_pts = min(20, round(elev_m / 1000.0 * 20))

    # --- Intensity (0-25) — adaptive ---
    intensity_pts = 0
    intensity_max = 0

    if activity.get('has_heartrate') and activity.get('average_heartrate'):
        intensity_max += 10
        max_hr = activity.get('max_heartrate') or 190
        hr_pct = activity['average_heartrate'] / max_hr
        intensity_pts += min(10, max(0, round((hr_pct - 0.55) / 0.30 * 10)))

    if activity.get('device_watts') and activity.get('weighted_average_watts'):
        intensity_max += 10
        intensity_pts += min(10, round(activity['weighted_average_watts'] / 180.0 * 10))

    if activity.get('suffer_score') and activity['suffer_score'] > 0:
        intensity_max += 10
        intensity_pts += min(10, round(activity['suffer_score'] / 80.0 * 10))

    if intensity_max > 0:
        intensity_pts = round(intensity_pts / intensity_max * 25)
    else:
        # Fallback: duration-based (2h ride = moderate)
        duration_min = moving_sec / 60.0
        intensity_pts = min(15, round(duration_min / 120.0 * 15))

    # --- Progressive Overload (0-25) ---
    overload_pts = 15  # neutral default if no prior data
    trend = 'maintaining'

    prev_rides = [a for a in previous_activities
                  if a.get('activity_type') in _CYCLING_TYPES]

    if prev_rides:
        avg_prev_dist = sum((a.get('distance') or 0) for a in prev_rides) / len(prev_rides) / 1000.0
        avg_prev_elev = sum((a.get('total_elevation_gain') or 0) for a in prev_rides) / len(prev_rides)
        avg_prev_dur = sum((a.get('moving_time') or 0) for a in prev_rides) / len(prev_rides)

        ratios = []
        if avg_prev_dist > 0:
            ratios.append(dist_km / avg_prev_dist)
        if avg_prev_elev > 0:
            ratios.append(elev_m / avg_prev_elev)
        if avg_prev_dur > 0:
            ratios.append(moving_sec / avg_prev_dur)

        if ratios:
            avg_ratio = sum(ratios) / len(ratios)
            # Ratio 1.0 = maintaining, 1.2+ = building, 0.8- = declining
            if avg_ratio >= 1.15:
                trend = 'building'
            elif avg_ratio <= 0.85:
                trend = 'declining'
            else:
                trend = 'maintaining'
            # Map ratio to 0-25: ratio 0.5→0, 1.0→15, 1.5→25
            overload_pts = min(25, max(0, round((avg_ratio - 0.5) * 25)))

    total = min(100, max(0, distance_pts + elevation_pts + intensity_pts + overload_pts))
    grade, color = _grade_from_score(total)

    return {
        'total': total,
        'grade': grade,
        'color': color,
        'trend': trend,
        'distance_pts': distance_pts,
        'elevation_pts': elevation_pts,
        'intensity_pts': intensity_pts,
        'overload_pts': overload_pts,
    }


def score_all_activities(activities):
    """Score every activity with progressive-overload context.

    Args:
        activities: list of strava_activity dicts (any order)

    Returns:
        list of dicts (activity merged with score) sorted by date DESC
    """
    if not activities:
        return []

    # Sort by date ascending for progressive context
    sorted_acts = sorted(activities, key=lambda a: str(a.get('start_date_local') or ''))

    scored = []
    for i, act in enumerate(sorted_acts):
        if act.get('activity_type') not in _CYCLING_TYPES:
            continue

        # Previous 14-day window for overload comparison
        act_dt = _parse_dt(act.get('start_date_local') or act.get('start_date'))
        prev = []
        if act_dt:
            cutoff = act_dt - timedelta(days=14)
            for j in range(i):
                pdt = _parse_dt(sorted_acts[j].get('start_date_local') or sorted_acts[j].get('start_date'))
                if pdt and pdt >= cutoff and sorted_acts[j].get('activity_type') in _CYCLING_TYPES:
                    prev.append(sorted_acts[j])

        score = calculate_per_ride_score(act, prev)
        row = dict(act)
        row.update(score)
        scored.append(row)

    # Return most recent first
    scored.reverse()
    return scored


# ========== READINESS ASSESSMENT ==========

def assess_readiness(activities, ride):
    """Assess rider readiness for an upcoming ride.

    Args:
        activities: list of strava_activity dicts (last 28 days)
        ride: dict with distance_km, distance_miles, elevation_ft, time_limit_hours

    Returns:
        dict with score, level, color, dimension scores, advice list
    """
    if not activities:
        return {
            'score': 0, 'level': 'not_ready', 'color': '#e53e3e',
            'distance': 0, 'elevation': 0, 'volume': 0, 'fitness': 0,
            'advice': ['No recent training data. Connect Strava to see readiness assessment.'],
        }

    rides = [a for a in activities if a.get('activity_type') in _CYCLING_TYPES]

    # Target distances
    target_km = (ride.get('distance_km') or 0)
    if not target_km and ride.get('distance_miles'):
        target_km = ride['distance_miles'] / 0.621371
    target_elev_m = (ride.get('elevation_ft') or 0) / 3.28084  # ft → m

    # --- Distance readiness (0-35) ---
    # Longest single ride >= 60% of target distance = full marks
    longest_km = max((r.get('distance') or 0) / 1000.0 for r in rides) if rides else 0
    dist_threshold = target_km * 0.6 if target_km > 0 else 60
    distance_score = min(35, round(longest_km / dist_threshold * 35)) if dist_threshold > 0 else 35

    # --- Elevation readiness (0-25) ---
    # Max single-ride elevation >= 50% of target = full marks
    max_elev = max(r.get('total_elevation_gain') or 0 for r in rides) if rides else 0
    elev_threshold = target_elev_m * 0.5 if target_elev_m > 0 else 750
    elevation_score = min(25, round(max_elev / elev_threshold * 25)) if elev_threshold > 0 else 25

    # --- Volume readiness (0-20) ---
    # Weekly mileage vs target-based expectation
    weekly_targets = {200: 150, 300: 200, 400: 250, 600: 300, 1000: 350}
    target_weekly_km = 150  # default
    for dist, weekly in sorted(weekly_targets.items()):
        if target_km <= dist:
            target_weekly_km = weekly
            break
    else:
        target_weekly_km = 350

    total_km = sum((r.get('distance') or 0) / 1000.0 for r in rides)
    weeks_active = 4  # 28-day window
    actual_weekly = total_km / weeks_active
    volume_score = min(20, round(actual_weekly / target_weekly_km * 20)) if target_weekly_km > 0 else 20

    # --- Fitness readiness (0-20) ---
    fitness = calculate_fitness_score(activities)
    fitness_total = fitness['total'] if fitness else 0
    fitness_score = min(20, round(fitness_total / 100.0 * 20))

    total = min(100, max(0, distance_score + elevation_score + volume_score + fitness_score))

    if total >= 70:
        level, color = 'ready', '#38a169'
    elif total >= 40:
        level, color = 'caution', '#d69e2e'
    else:
        level, color = 'not_ready', '#e53e3e'

    return {
        'score': total,
        'level': level,
        'color': color,
        'distance': distance_score,
        'elevation': elevation_score,
        'volume': volume_score,
        'fitness': fitness_score,
        'distance_max': 35,
        'elevation_max': 25,
        'volume_max': 20,
        'fitness_max': 20,
        'longest_km': longest_km,
        'max_elev_m': max_elev,
        'actual_weekly_km': actual_weekly,
        'target_weekly_km': target_weekly_km,
    }


def generate_training_advice(readiness, ride, weeks_until_ride):
    """Generate specific training advice based on readiness gaps.

    Args:
        readiness: dict from assess_readiness()
        ride: dict with ride details
        weeks_until_ride: int, weeks until the event

    Returns:
        list of advice strings
    """
    advice = []
    target_km = ride.get('distance_km') or 0
    if not target_km and ride.get('distance_miles'):
        target_km = ride['distance_miles'] / 0.621371
    target_miles = target_km * 0.621371
    target_elev_ft = ride.get('elevation_ft') or 0

    if weeks_until_ride <= 1:
        # Taper / rest advice
        advice.append("Focus on rest and recovery — your fitness is set.")
        advice.append("Stay hydrated and eat well the day before.")
        if target_miles >= 125:  # 200k+
            advice.append("Prep your bike: check tires, brakes, chain. Pack lights and spares.")
            advice.append("Plan your nutrition: aim for 200-300 calories/hour on the ride.")
        return advice

    # Distance gap
    if readiness['distance'] < readiness['distance_max'] * 0.7:
        longest_mi = readiness.get('longest_km', 0) * 0.621371
        target_long = target_miles * 0.6
        gap_mi = target_long - longest_mi
        if gap_mi > 0:
            advice.append(
                f"Build your long ride to {target_long:.0f} mi "
                f"(current longest: {longest_mi:.0f} mi). "
                f"Add ~{min(gap_mi / max(weeks_until_ride - 1, 1), 20):.0f} mi each week."
            )

    # Elevation gap
    if readiness['elevation'] < readiness['elevation_max'] * 0.7:
        max_elev_ft = readiness.get('max_elev_m', 0) * 3.28084
        target_single_elev = target_elev_ft * 0.5
        if target_single_elev > max_elev_ft:
            advice.append(
                f"Add hillier routes — aim for {target_single_elev:.0f} ft gain in a single ride "
                f"(current max: {max_elev_ft:.0f} ft)."
            )

    # Volume gap
    if readiness['volume'] < readiness['volume_max'] * 0.7:
        actual_weekly_mi = readiness.get('actual_weekly_km', 0) * 0.621371
        target_weekly_mi = readiness.get('target_weekly_km', 150) * 0.621371
        advice.append(
            f"Increase weekly mileage to ~{target_weekly_mi:.0f} mi/week "
            f"(currently {actual_weekly_mi:.0f} mi/week). "
            f"Add an extra mid-week ride."
        )

    # Fitness gap
    if readiness['fitness'] < readiness['fitness_max'] * 0.7:
        advice.append(
            "Boost overall fitness: mix in tempo efforts and interval sessions "
            "alongside your endurance rides."
        )

    if not advice:
        advice.append("You're on track! Maintain your current training and stay consistent.")

    return advice
