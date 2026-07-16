"""shared/pacing.py — club-agnostic brevet pacing engine.

The pure pacing math extracted from Team Asha's ``services/custom_plan_service``
so BOTH the Team Asha app and BrevetHub can reuse the SAME proven engine (the
extract -> shim -> vendor pattern already used by shared/fitness, shared/strava,
shared/rusa). ``services/custom_plan_service`` now imports these functions from
here, and ``brevethub/shared/pacing.py`` is a byte-identical vendored copy.

Every function below is stdlib-only (no Flask, no DB): given a list of stop dicts
it recomputes segment distance, average speed, cumulative/arrival time, and the
time bank vs an ACP control cutoff. The math is unit-agnostic — the time bank is a
distance *fraction* and ``avg_speed`` is distance-units per hour — so a caller may
pass kilometres straight through the ``distance_miles``-named field and read
``avg_speed`` back as km/h (BrevetHub does exactly this). The field keeps its
original name so the extraction stays byte-identical to the Team Asha source and no
existing caller or test changes.
"""


def recalculate_cumulative_values(stops, custom_plan, cutoff_hours=None, total_mi=None):
    """
    Recalculate all cumulative and derived values for stops.

    Handles:
    - Cumulative time
    - Segment distance
    - Average speed per segment
    - Ft/mile for each segment
    - Time bank (if cutoff_hours available)

    Args:
        cutoff_hours: canonical event cutoff (e.g. ride.time_limit_hours). When given,
            it is used directly. When None, fall back to parsing a distance class out of
            ``custom_plan['name']`` — which yields None for a custom plan whose name has
            no distance (e.g. "Mihir's Push pace"), silently zeroing the time bank. Callers
            that know the real cutoff (routes/live.py) should always pass it.
        total_mi: the plan's total distance in miles, used as the time-bank fraction basis.
            When None, fall back to the largest per-stop cumulative distance.
    """
    if not stops:
        return stops

    # Prefer the caller's canonical cutoff; only parse the (custom) plan name as a fallback.
    if cutoff_hours is None:
        distance_km = _extract_distance_km(custom_plan.get('name', ''))
        cutoff_hours = _get_cutoff_hours(distance_km)
    if cutoff_hours:
        cutoff_hours = float(cutoff_hours)

    # Calculate total distance for time bank proportions
    total_distance = float(total_mi) if total_mi else 0.0
    if total_distance <= 0:
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
