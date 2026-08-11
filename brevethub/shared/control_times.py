"""Official RUSA/ACP control closing-time calculations.

Long brevets do not distribute the overall cutoff linearly.  Controls through
600 km use 15 km/h; the 600-1000 km band uses 11.428 km/h; and the
1000-1300 km band uses 13.333 km/h.  The event finish is always capped at its
canonical overall cutoff.
"""

MILES_TO_KM = 1.609344


def control_open_time_minutes(distance, distance_unit='miles'):
    """Return official ACP/RUSA opening time from the event start.

    Opening uses the ACP/RUSA maximum-speed bands: 34 km/h through 200 km,
    32 km/h through 400 km, 30 km/h through 600 km, 28 km/h through 1,000 km,
    then 26 km/h through 1,300 km. These are distinct from the minimum-speed
    bands used for closing times.
    """
    if distance is None or float(distance) < 0:
        return None
    distance_km = float(distance) if distance_unit == 'km' else float(distance) * MILES_TO_KM
    bands = ((200.0, 34.0), (400.0, 32.0), (600.0, 30.0),
             (1000.0, 28.0), (1300.0, 26.0))
    hours = 0.0
    previous_km = 0.0
    for upper_km, speed_kmh in bands:
        segment_km = max(0.0, min(distance_km, upper_km) - previous_km)
        hours += segment_km / speed_kmh
        previous_km = upper_km
        if distance_km <= upper_km:
            break
    if distance_km > previous_km:
        hours += (distance_km - previous_km) / 26.0
    return round(hours * 60)


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
