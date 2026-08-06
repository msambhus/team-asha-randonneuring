"""Official RUSA/ACP control closing-time calculations.

Long brevets do not distribute the overall cutoff linearly.  Controls through
600 km use 15 km/h; the 600-1000 km band uses 11.428 km/h; and the
1000-1300 km band uses 13.333 km/h.  The event finish is always capped at its
canonical overall cutoff.
"""

MILES_TO_KM = 1.609344


def control_close_time_minutes(distance, total_distance, cutoff_hours,
                               event_distance_km=None, distance_unit='miles'):
    """Return the advisory control closing time in minutes from the start.

    Existing <=600 km plans retain their historical linear calculation.  The
    official piecewise schedule applies to 1000 km and 1200 km plans.
    """
    if not cutoff_hours or not total_distance or not distance or distance <= 0:
        return None

    cutoff_minutes = round(float(cutoff_hours) * 60)
    if event_distance_km is None:
        event_distance_km = {75: 1000, 90: 1200}.get(round(float(cutoff_hours)))

    if not event_distance_km or float(event_distance_km) < 1000:
        return round((float(distance) / float(total_distance)) * cutoff_minutes)

    distance_km = (float(distance) if distance_unit == 'km'
                   else float(distance) * MILES_TO_KM)
    nominal_km = float(event_distance_km)
    if distance_km >= nominal_km:
        return cutoff_minutes

    if distance_km <= 600:
        hours = distance_km / 15.0
    elif distance_km <= 1000:
        hours = 40.0 + ((distance_km - 600.0) / 11.428)
    else:
        hours = 75.0 + ((distance_km - 1000.0) / 13.333)

    return min(round(hours * 60), cutoff_minutes)
