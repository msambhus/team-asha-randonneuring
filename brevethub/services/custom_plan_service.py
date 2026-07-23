"""
Custom Ride Plan Service

Business logic for merging base plans with user customizations,
recalculating cumulative values, and managing custom plan inheritance.

The pure pacing math (``recalculate_cumulative_values``, ``apply_pace_adjustment``,
the ACP-cutoff helper, and the difficulty helpers the engine calls) now lives in
the standalone ``shared/pacing.py`` so both this app and BrevetHub reuse the SAME
engine. This module is a partial shim: it re-exports that pure API and keeps its
DB-coupled / Team-Asha-specific functions (``get_merged_plan_stops``,
``compare_plans``) here. ``get_merged_plan_stops`` calls
``recalculate_cumulative_values`` by bare name, which resolves to the symbol
imported below — so a test that patches
``services.custom_plan_service.recalculate_cumulative_values`` still takes effect.
"""

from models import (
    get_custom_plan_by_id,
    get_ride_plan_by_slug,
    get_ride_plan_stops,
    get_custom_plan_stops_raw
)

# Pure pacing engine — the source of truth is shared/pacing.py (extracted verbatim
# from this module). Re-exported so existing importers keep working unchanged.
from shared.pacing import (  # noqa: F401  (re-exported for backward compatibility)
    recalculate_cumulative_values,
    apply_pace_adjustment,
    _extract_distance_km,
    _get_cutoff_hours,
    _compute_difficulty_score,
    _difficulty_label,
    _difficulty_color,
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
