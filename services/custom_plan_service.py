"""
Custom Ride Plan Service

Business logic for merging base plans with user customizations,
recalculating cumulative values, and managing custom plan inheritance.
"""

from models import (
    get_custom_plan_by_id,
    get_ride_plan_by_slug,
    get_ride_plan_stops,
    get_custom_plan_stops_raw
)


def get_merged_plan_stops(custom_plan_id):
    """
    Merge base plan stops with user customizations.
    
    Returns a list of stops with:
    - Base stops (unless hidden)
    - Custom overrides applied (timing, notes)
    - Custom stops injected at proper positions
    - All cumulative values recalculated
    """
    custom_plan = get_custom_plan_by_id(custom_plan_id)
    if not custom_plan:
        return None, None
    
    base_plan_id = custom_plan['base_plan_id']
    base_stops = get_ride_plan_stops(base_plan_id)
    custom_stops_raw = get_custom_plan_stops_raw(custom_plan_id)
    
    # Build override map: {base_stop_id: custom_override}
    overrides = {}
    custom_only_stops = []
    
    for cs in custom_stops_raw:
        if cs.get('base_stop_id'):
            overrides[cs['base_stop_id']] = cs
        elif cs.get('is_custom_stop'):
            custom_only_stops.append(cs)
    
    # Merge base stops with overrides
    # Track accumulated time from removed stops to add to the next visible stop
    merged = []
    accumulated_time_from_removed = 0
    
    for base_stop in base_stops:
        override = overrides.get(base_stop['id'])
        
        # Skip if marked as hidden, but accumulate its time
        if override and override.get('is_hidden'):
            accumulated_time_from_removed += base_stop.get('segment_time_min') or 0
            continue
        
        # Start with base stop data
        stop = dict(base_stop)
        stop['is_modified'] = False
        stop['is_custom_stop'] = False
        stop['custom_stop_id'] = None
        
        # Apply customizations
        if override:
            # Apply overrides with sentinel value handling:
            # - stop_duration_min: -1 = explicitly removed, NULL/0 = inherit from base, >0 = use custom value
            # - stop_name: coupled with stop_duration_min
            #   * If custom duration is NULL/0: inherit both duration and name from base
            #   * If custom duration is -1: clear both (explicitly removed)
            #   * If custom duration > 0: use custom duration, and use custom name if present (not null)
            
            if override.get('segment_time_min') is not None:
                stop['segment_time_min'] = override['segment_time_min']
                stop['is_modified'] = True
            
            # Handle stop_duration_min and stop_name together
            if 'stop_duration_min' in override:
                override_duration = override.get('stop_duration_min')
                
                if override_duration == -1:
                    # Explicitly removed - clear both duration and name
                    stop['stop_duration_min'] = 0
                    stop['stop_name'] = None
                    stop['is_modified'] = True
                    
                elif override_duration is not None and override_duration > 0:
                    # Custom duration > 0: use custom duration
                    stop['stop_duration_min'] = override_duration
                    
                    # For stop_name: use custom if present (not null), otherwise keep base
                    if 'stop_name' in override and override.get('stop_name') is not None:
                        stop['stop_name'] = override['stop_name']
                    # else: keep base stop_name (already in stop from dict(base_stop))
                    
                    base_duration = base_stop.get('stop_duration_min') or 0
                    if stop['stop_duration_min'] != base_duration:
                        stop['is_modified'] = True
                        
                # else: duration is NULL or 0 in override - inherit BOTH duration and name from base
                # (already set via stop = dict(base_stop), so no action needed)
            
            if override.get('location'):
                stop['location'] = override['location']
                stop['is_modified'] = True
            if override.get('notes'):
                stop['notes'] = override['notes']
                stop['is_modified'] = True
            stop['custom_stop_id'] = override['id']
        
        # Add accumulated time from any removed stops before this one
        if accumulated_time_from_removed > 0:
            stop['segment_time_min'] = (stop.get('segment_time_min') or 0) + accumulated_time_from_removed
            stop['is_modified'] = True
            accumulated_time_from_removed = 0
        
        merged.append(stop)
    
    # Add custom stops
    for cs in custom_only_stops:
        stop = dict(cs)
        stop['is_custom_stop'] = True
        stop['is_modified'] = True
        stop['custom_stop_id'] = cs['id']
        merged.append(stop)
    
    # Sort all stops by distance, then by stop_order to ensure correct display order
    merged.sort(key=lambda s: (
        float(s.get('distance_miles') or 0),
        0 if not s.get('is_custom_stop') else 1,  # Base stops before custom at same distance
        int(s.get('stop_order') or 999)
    ))
    
    # Recalculate cumulative values and metadata
    merged_with_calcs = recalculate_cumulative_values(merged, custom_plan)
    
    return merged_with_calcs, custom_plan


def recalculate_cumulative_values(stops, custom_plan):
    """
    Recalculate all cumulative and derived values for stops.
    
    Handles:
    - Cumulative time
    - Segment distance
    - Average speed per segment
    - Ft/mile for each segment
    - Time bank (if cutoff_hours available)
    """
    if not stops:
        return stops
    
    # Extract distance class for time bank calculation
    base_plan_name = custom_plan.get('name', '')
    distance_km = _extract_distance_km(base_plan_name)
    cutoff_hours = _get_cutoff_hours(distance_km)
    if cutoff_hours:
        cutoff_hours = float(cutoff_hours)
    
    # Calculate total distance for time bank proportions
    total_distance = float(max((float(s.get('distance_miles') or 0) for s in stops), default=0))
    
    cum_time_min = 0
    prev_dist = 0.0
    
    for i, stop in enumerate(stops):
        # Convert Decimal to float for calculations
        cur_dist = float(stop.get('distance_miles') or 0)
        elev_gain = int(stop.get('elevation_gain') or 0)
        seg_time = int(stop.get('segment_time_min') or 0)
        stop_duration = int(stop.get('stop_duration_min') or 0)
        
        # Calculate segment distance
        seg_dist = round(cur_dist - prev_dist, 1)
        stop['seg_dist'] = seg_dist
        
        # Calculate ft/mile for this segment
        if elev_gain and seg_dist > 0:
            stop['ft_per_mi'] = int(round(elev_gain / seg_dist))
        else:
            stop['ft_per_mi'] = None
        
        # Calculate average speed for this segment (based on segment time only, not including stop duration)
        if seg_time and seg_time > 0 and seg_dist > 0:
            stop['avg_speed'] = round(seg_dist / (seg_time / 60.0), 1)
        else:
            stop['avg_speed'] = None
        
        # Cumulative time includes both segment time (riding) and stop duration (rest)
        if seg_time:
            cum_time_min += seg_time
        if stop_duration:
            cum_time_min += stop_duration
        stop['cum_time_min'] = cum_time_min
        
        # Arrival time: cumulative time minus stop duration (time you arrive, before resting)
        stop['arrival_time_min'] = cum_time_min - stop_duration
        
        # Time bank calculation (bookend time - arrival time, not including stop duration)
        if cutoff_hours and total_distance > 0 and cur_dist > 0:
            fraction = cur_dist / total_distance
            bookend_time_min = round(fraction * cutoff_hours * 60)
            stop['bookend_time_min'] = bookend_time_min
            stop['time_bank_min'] = bookend_time_min - stop['arrival_time_min']
        else:
            stop['bookend_time_min'] = None
            stop['time_bank_min'] = None
        
        # Difficulty scoring
        stop['difficulty_score'] = _compute_difficulty_score(stop['ft_per_mi'], stop.get('notes'))
        stop['difficulty_label'] = _difficulty_label(stop['difficulty_score'])
        stop['difficulty_color'] = _difficulty_color(stop['ft_per_mi'])
        
        prev_dist = cur_dist
    
    return stops


def apply_pace_adjustment(stops, avg_moving_speed):
    """
    Recalculate segment times based on a new average moving speed.
    
    Only adjusts segments where distance > 0 (actual riding segments).
    Preserves break/rest stop times (seg_dist = 0).
    
    Args:
        stops: List of stop dictionaries
        avg_moving_speed: New average speed in mph
    
    Returns:
        List of stops with adjusted segment_time_min
    """
    if not avg_moving_speed or avg_moving_speed <= 0:
        return stops
    
    # Convert to float in case it's Decimal from database
    avg_moving_speed = float(avg_moving_speed)
    
    adjusted = []
    for stop in stops:
        stop_copy = dict(stop)
        seg_dist = float(stop_copy.get('seg_dist', 0) or 0)
        
        # Only adjust riding segments (seg_dist > 0)
        if seg_dist and seg_dist > 0:
            # Calculate new time: distance / speed * 60 minutes
            new_time_min = int(round((seg_dist / avg_moving_speed) * 60))
            stop_copy['segment_time_min'] = new_time_min
            stop_copy['is_modified'] = True
        
        adjusted.append(stop_copy)
    
    return adjusted


def compare_plans(base_stops, custom_stops):
    """
    Compare base plan with custom plan and return differences.
    
    Returns dict with:
    - total_time_diff: Difference in total time (minutes)
    - stops_added: Number of custom stops added
    - stops_hidden: Number of base stops hidden
    - stops_modified: Number of stops with timing changes
    - segment_diffs: List of per-segment differences
    """
    base_total_time = sum(s.get('segment_time_min') or 0 for s in base_stops)
    custom_total_time = sum(s.get('segment_time_min') or 0 for s in custom_stops)
    
    stops_added = sum(1 for s in custom_stops if s.get('is_custom_stop'))
    stops_hidden = len(base_stops) - len([s for s in custom_stops if not s.get('is_custom_stop')])
    stops_modified = sum(1 for s in custom_stops if s.get('is_modified') and not s.get('is_custom_stop'))
    
    # Build segment-by-segment comparison
    segment_diffs = []
    base_map = {s['id']: s for s in base_stops}
    
    for custom_stop in custom_stops:
        if custom_stop.get('is_custom_stop'):
            segment_diffs.append({
                'location': custom_stop['location'],
                'type': 'added',
                'time_diff': custom_stop.get('segment_time_min', 0)
            })
        elif custom_stop.get('custom_stop_id'):
            base_stop = base_map.get(custom_stop.get('base_stop_id') or custom_stop['id'])
            if base_stop:
                base_time = base_stop.get('segment_time_min', 0)
                custom_time = custom_stop.get('segment_time_min', 0)
                time_diff = custom_time - base_time
                
                if time_diff != 0:
                    segment_diffs.append({
                        'location': custom_stop['location'],
                        'type': 'modified',
                        'time_diff': time_diff,
                        'base_time': base_time,
                        'custom_time': custom_time
                    })
    
    return {
        'total_time_diff': custom_total_time - base_total_time,
        'stops_added': stops_added,
        'stops_hidden': stops_hidden,
        'stops_modified': stops_modified,
        'segment_diffs': segment_diffs
    }


# ========== HELPER FUNCTIONS (from routes/riders.py) ==========

def _extract_distance_km(name):
    """Extract brevet distance in km from plan name (e.g., '200' from 'Davis 200K')."""
    import re
    m = re.search(r'(\d{3,4})\s*k', name, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _get_cutoff_hours(distance_km):
    """Return ACP cutoff hours for a brevet distance."""
    if not distance_km:
        return None
    if distance_km <= 200:
        return 13.5
    elif distance_km <= 300:
        return 20
    elif distance_km <= 400:
        return 27
    elif distance_km <= 600:
        return 40
    elif distance_km <= 1000:
        return 75
    return None


def _compute_difficulty_score(ft_per_mi, notes):
    """Compute difficulty score for a segment."""
    if not ft_per_mi:
        return 0
    
    score = ft_per_mi
    
    # Boost for steep/technical notes
    if notes:
        notes_lower = notes.lower()
        if any(word in notes_lower for word in ['steep', 'climb', 'grade', 'technical']):
            score *= 1.2
    
    return round(score, 1)


def _difficulty_label(score):
    """Convert difficulty score to label."""
    if not score or score <= 0:
        return 'flat'
    elif score < 30:
        return 'easy'
    elif score < 50:
        return 'moderate'
    elif score < 80:
        return 'challenging'
    else:
        return 'steep'


def _difficulty_color(ft_per_mi):
    """
    Return color for difficulty visualization (gradient scale).
    Uses a smooth gradient from gray -> green -> yellow -> red -> dark red.
    """
    if not ft_per_mi:
        return '#94a3b8'  # gray (flat)
    
    if ft_per_mi <= 20:
        return '#22c55e'  # green (easy)
    elif ft_per_mi <= 40:
        # Interpolate green to yellow
        return '#84cc16'  # lime
    elif ft_per_mi <= 60:
        return '#f59e0b'  # amber/orange
    elif ft_per_mi <= 80:
        return '#ef4444'  # red
    else:
        return '#991b1b'  # dark red (very steep)
