"""Eddington Number calculation for cycling achievements.

The Eddington Number (E) for cycling is the largest number E such that
you have ridden at least E miles (or km) on at least E different days.

For example, an Eddington number of 50 means you've ridden 50+ miles
on 50+ different days.
"""

from collections import defaultdict
from datetime import datetime

# All Strava activity types that count as cycling
CYCLING_TYPES = {
    'Ride', 'VirtualRide', 'MountainBikeRide', 'GravelRide',
    'EBikeRide', 'Handcycle', 'Velomobile',
}


def _get_daily_distances(activities, unit='miles', activity_types=None):
    """Build {date_key: total_distance} dict from activities.

    Args:
        activities: List of activity dicts
        unit: 'miles' or 'km'
        activity_types: Set of types to include, or 'all' for no filter.
                        Defaults to CYCLING_TYPES.

    Returns:
        dict mapping 'YYYY-MM-DD' strings to total distance (miles or km)
    """
    if activity_types is None:
        activity_types = CYCLING_TYPES

    daily = defaultdict(float)
    for activity in activities:
        if activity_types != 'all':
            if activity.get('activity_type') not in activity_types:
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
        daily[date_key] += distance
    return daily


def _eddington_from_distances(daily_distances):
    """Compute Eddington number from a {date: distance} dict."""
    if not daily_distances:
        return 0
    distances = sorted(daily_distances.values(), reverse=True)
    eddington = 0
    for i, distance in enumerate(distances):
        if distance >= (i + 1):
            eddington = i + 1
        else:
            break
    return eddington


def calculate_eddington_number(activities, unit='miles', activity_types=None):
    """Calculate Eddington number from Strava activities.

    Args:
        activities: List of activity dicts with 'distance' (meters) and 'start_date'
        unit: 'miles' or 'km' for distance unit
        activity_types: Set of types to include, or 'all' for no filter.
                        Defaults to CYCLING_TYPES.

    Returns:
        int: Eddington number (largest E where you rode ≥E on ≥E days)
    """
    daily = _get_daily_distances(activities, unit, activity_types)
    return _eddington_from_distances(daily)


def calculate_eddington_by_year(activities, unit='miles', activity_types=None):
    """Calculate Eddington number per calendar year.

    Args:
        activities: List of activity dicts with 'distance' (meters) and 'start_date'
        unit: 'miles' or 'km'
        activity_types: Set of types to include, or 'all' for no filter.
                        Defaults to CYCLING_TYPES.

    Returns:
        dict mapping year (int) to {'eddington': int, 'eddington_cumulative': int,
        'ride_days': int, 'rides': int} sorted by year descending
    """
    if activity_types is None:
        activity_types = CYCLING_TYPES

    daily = _get_daily_distances(activities, unit, activity_types)

    # Group daily distances by year
    by_year = defaultdict(dict)
    for date_key, dist in daily.items():
        year = int(date_key[:4])
        by_year[year][date_key] = dist

    # Count total activities per year (for "rides" count — multiple per day possible)
    activity_count_by_year = defaultdict(int)
    for activity in activities:
        if activity_types != 'all':
            if activity.get('activity_type') not in activity_types:
                continue
        start_date = activity.get('start_date_local') or activity.get('start_date')
        if not start_date:
            continue
        if isinstance(start_date, str):
            try:
                date_obj = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                year = date_obj.year
            except (ValueError, AttributeError):
                continue
        else:
            year = start_date.year
        activity_count_by_year[year] += 1

    # Build per-year and cumulative Eddington
    # Process years chronologically for cumulative calculation
    cumulative_daily = {}
    cumulative_by_year = {}
    for year in sorted(by_year.keys()):
        cumulative_daily.update(by_year[year])
        cumulative_by_year[year] = _eddington_from_distances(cumulative_daily)

    results = {}
    for year in sorted(by_year.keys(), reverse=True):
        year_daily = by_year[year]
        results[year] = {
            'eddington': _eddington_from_distances(year_daily),
            'eddington_cumulative': cumulative_by_year[year],
            'ride_days': len(year_daily),
            'rides': activity_count_by_year.get(year, 0),
        }

    return results


def get_eddington_progress(activities, current_eddington, unit='miles', activity_types=None):
    """Get progress towards next Eddington number.

    Args:
        activities: List of activity dicts
        current_eddington: Current Eddington number
        unit: 'miles' or 'km'
        activity_types: Set of types to include, or 'all' for no filter.
                        Defaults to CYCLING_TYPES.

    Returns:
        dict with:
            - next_target: Next Eddington goal (current + 1)
            - days_needed: How many more days of (next_target) miles needed
            - days_completed: How many days already qualify
            - progress_pct: Percentage progress (0-100)
    """
    next_target = current_eddington + 1

    daily_distances = _get_daily_distances(activities, unit, activity_types)

    # Count days with distance >= next_target
    days_completed = sum(1 for dist in daily_distances.values() if dist >= next_target)
    days_needed = max(0, next_target - days_completed)
    progress_pct = min(100, int((days_completed / next_target) * 100)) if next_target > 0 else 0

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
