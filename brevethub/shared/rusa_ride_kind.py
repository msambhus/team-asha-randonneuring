"""Classify official RUSA result rows for shared rider-history presentation."""


def rusa_ride_kind(ride):
    """Return ``permanent``, ``populaire``, or ``brevet`` for a RUSA result."""
    source = ' '.join([
        str((ride or {}).get('event_type') or ''),
        str((ride or {}).get('route_name') or ''),
        str((ride or {}).get('permanent_name') or ''),
    ]).lower()
    if 'perm' in source:
        return 'permanent'
    if 'populaire' in source or 'popular' in source:
        return 'populaire'
    return 'brevet'


def ride_kind_counts(rides):
    """Count result rows by kind without changing the source records."""
    counts = {'brevet': 0, 'permanent': 0, 'populaire': 0}
    for ride in rides or []:
        counts[rusa_ride_kind(ride)] += 1
    return counts
