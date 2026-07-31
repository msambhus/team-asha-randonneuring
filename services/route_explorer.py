"""Shared payload builders for coordinated route maps and elevation profiles."""
import math


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def track_from_strava_streams(streams, max_points=1000):
    """Build [{lat,lng,dist_m,e_m}] from index-aligned Strava streams."""
    streams = streams or {}
    latlng = streams.get("latlng") or []
    distance = streams.get("distance") or []
    altitude = streams.get("altitude") or []
    count = min(len(latlng), len(distance), len(altitude))
    if count < 2:
        return []
    step = max(1, math.ceil(count / max_points)) if max_points else 1
    indexes = list(range(0, count, step))
    if indexes[-1] != count - 1:
        indexes.append(count - 1)
    track = []
    for index in indexes:
        point = latlng[index]
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        lat = _float(point[0])
        lng = _float(point[1])
        dist_m = _float(distance[index])
        elevation_m = _float(altitude[index])
        if None in (lat, lng, dist_m, elevation_m):
            continue
        track.append({
            "lat": lat,
            "lng": lng,
            "dist_m": dist_m,
            "e_m": elevation_m,
        })
    return track


def route_points(track, max_points=1000):
    """Return a bounded Leaflet [lat,lng] polyline from a normalized track."""
    usable = []
    for point in track or []:
        lat = _float(point.get("lat"))
        lng = _float(point.get("lng"))
        if lat is not None and lng is not None:
            usable.append([lat, lng])
    if len(usable) <= max_points or not max_points:
        return usable
    step = math.ceil(len(usable) / max_points)
    result = usable[::step]
    if result[-1] != usable[-1]:
        result.append(usable[-1])
    return result


def explorer_stops(profile_markers):
    """Reduce profile markers to the fields needed by the shared browser UI."""
    return [{
        "index": marker.get("i"),
        "name": marker.get("name"),
        "distance_miles": marker.get("cumul_mi"),
        "color": marker.get("color"),
        "eta": marker.get("eta"),
        "break_min": marker.get("break_min"),
    } for marker in (profile_markers or [])]
