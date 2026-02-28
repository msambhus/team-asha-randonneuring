"""Eddington Number calculation for cycling achievements.

The Eddington Number (E) for cycling is the largest number E such that
you have ridden at least E miles (or km) on at least E different days.

For example, an Eddington number of 50 means you've ridden 50+ miles
on 50+ different days.
"""

from collections import defaultdict
from datetime import datetime


def calculate_eddington_number(activities, unit='miles'):
    """Calculate Eddington number from Strava activities.

    Args:
        activities: List of activity dicts with 'distance' (meters) and 'start_date'
        unit: 'miles' or 'km' for distance unit

    Returns:
        int: Eddington number (largest E where you rode ≥E on ≥E days)
    """
    if not activities:
        return 0

    # Group activities by date and sum distances
    daily_distances = defaultdict(float)

    for activity in activities:
        # Only count rides (not runs, walks, etc.)
        if activity.get('activity_type') != 'Ride':
            continue

        distance_meters = activity.get('distance', 0)
        if not distance_meters:
            continue

        # Convert to miles or km
        if unit == 'miles':
            distance = distance_meters / 1609.34  # meters to miles
        else:
            distance = distance_meters / 1000  # meters to km

        # Get the date (YYYY-MM-DD) from start_date
        start_date = activity.get('start_date_local') or activity.get('start_date')
        if not start_date:
            continue

        # Parse date
        if isinstance(start_date, str):
            try:
                date_obj = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                date_key = date_obj.date().isoformat()
            except (ValueError, AttributeError):
                continue
        else:
            # Already a datetime object
            date_key = start_date.date().isoformat()

        daily_distances[date_key] += distance

    if not daily_distances:
        return 0

    # Sort daily distances in descending order
    distances = sorted(daily_distances.values(), reverse=True)

    # Find largest E where at least E days have distance ≥ E
    # Algorithm: For each potential E (starting from len(distances)),
    # check if at least E days have distance ≥ E

    eddington = 0
    for i, distance in enumerate(distances):
        # i+1 is the number of days with distance ≥ current threshold
        # Check if distance >= (i+1)
        if distance >= (i + 1):
            eddington = i + 1
        else:
            # Once we find a distance < (i+1), we can stop
            # because all subsequent distances are smaller
            break

    return eddington


def get_eddington_progress(activities, current_eddington, unit='miles'):
    """Get progress towards next Eddington number.

    Args:
        activities: List of activity dicts
        current_eddington: Current Eddington number
        unit: 'miles' or 'km'

    Returns:
        dict with:
            - next_target: Next Eddington goal (current + 1)
            - days_needed: How many more days of (next_target) miles needed
            - days_completed: How many days already qualify
            - progress_pct: Percentage progress (0-100)
    """
    next_target = current_eddington + 1

    # Count how many days already meet the next target
    daily_distances = defaultdict(float)

    for activity in activities:
        if activity.get('activity_type') != 'Ride':
            continue
        distance_meters = activity.get('distance', 0)
        if not distance_meters:
            continue

        if unit == 'miles':
            distance = distance_meters / 1609.34
        else:
            distance = distance_meters / 1000

        start_date = activity.get('start_date_local') or activity.get('start_date')
        if not start_date:
            continue

        if isinstance(start_date, str):
            try:
                date_obj = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                date_key = date_obj.date().isoformat()
            except (ValueError, AttributeError):
                continue
        else:
            date_key = start_date.date().isoformat()

        daily_distances[date_key] += distance

    # Count days with distance >= next_target
    days_completed = sum(1 for dist in daily_distances.values() if dist >= next_target)
    days_needed = max(0, next_target - days_completed)
    progress_pct = int((days_completed / next_target) * 100) if next_target > 0 else 0

    return {
        'next_target': next_target,
        'days_needed': days_needed,
        'days_completed': days_completed,
        'progress_pct': progress_pct,
    }


def get_eddington_badge_level(eddington):
    """Get badge level for Eddington number.

    Returns:
        dict with 'level', 'color', 'label'
    """
    if eddington >= 100:
        return {
            'level': 'legendary',
            'color': '#FFD700',  # Gold
            'label': 'Legendary',
            'emoji': '🏆'
        }
    elif eddington >= 75:
        return {
            'level': 'exceptional',
            'color': '#C0C0C0',  # Silver
            'label': 'Exceptional',
            'emoji': '⭐'
        }
    elif eddington >= 50:
        return {
            'level': 'strong',
            'color': '#CD7F32',  # Bronze
            'label': 'Strong',
            'emoji': '💪'
        }
    elif eddington >= 25:
        return {
            'level': 'solid',
            'color': '#3498db',  # Blue
            'label': 'Solid',
            'emoji': '🚴'
        }
    elif eddington >= 10:
        return {
            'level': 'building',
            'color': '#95a5a6',  # Gray
            'label': 'Building',
            'emoji': '📈'
        }
    else:
        return {
            'level': 'starting',
            'color': '#bdc3c7',  # Light gray
            'label': 'Getting Started',
            'emoji': '🌱'
        }
