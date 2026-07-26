"""Shared view-model contract for the Team Asha/BrevetHub Strava ride index."""


def ride_card(**values):
    """Return the canonical card shape consumed by my_strava_analysis.html."""
    card = {
        'ride_id': None,
        'ride_name': '',
        'date': None,
        'distance_km': None,
        'elevation_ft': None,
        'finish_time': None,
        'has_plan': False,
        'has_match': False,
        'is_brevet': False,
        'activity': None,
    }
    card.update(values)
    return card


def season_group(season, is_current, cards):
    """Return one canonical season block, excluding empty groups."""
    normalized = [ride_card(**card) for card in cards or []]
    if not normalized:
        return None
    return {
        'season': season,
        'is_current': bool(is_current),
        'ride_cards': normalized,
    }
