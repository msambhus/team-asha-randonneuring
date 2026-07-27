"""Shared public rider-directory view model.

Callers are responsible for authorization and must pass only public source data.
The adapter standardizes metrics and naming without reading either product's DB.
"""


_DEFAULTS = {
    'id': None,
    'rider_id': None,
    'rusa_id': None,
    'display_name': '',
    'first_name': '',
    'last_name': '',
    'photo_filename': None,
    'total_rides': 0,
    'total_kms': 0,
    'total_km': 0,
    'count': 0,
    'season_rides': 0,
    'season_kms': 0,
    'sr_count': 0,
    'sr_200': 0,
    'sr_300': 0,
    'sr_400': 0,
    'sr_600': 0,
    'pbp_years': (),
    'pbp_count': 0,
    'is_pbp_ancien': False,
    'permanent_count': 0,
    'populaire_count': 0,
    'rides_1000_plus': 0,
    'eddington': None,
    'upcoming': (),
}


def public_rider_row(record, **overrides):
    """Normalize one already-authorized public rider summary."""
    row = dict(record or {})
    for key, value in _DEFAULTS.items():
        row.setdefault(key, value)
    row.update(overrides)

    if not row.get('display_name'):
        row['display_name'] = ' '.join(filter(None, [
            row.get('first_name'), row.get('last_name')])).strip()
    if not row.get('total_km') and row.get('total_kms'):
        row['total_km'] = row['total_kms']
    if not row.get('total_kms') and row.get('total_km'):
        row['total_kms'] = row['total_km']
    if not row.get('count') and row.get('total_rides'):
        row['count'] = row['total_rides']
    if not row.get('total_rides') and row.get('count'):
        row['total_rides'] = row['count']
    return row
