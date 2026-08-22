from shared.rider_distance import canonical_distance_km, special_distance_km


def test_explicit_special_distance_name_overrides_stale_imported_value():
    assert canonical_distance_km(600, 'Gold Rush 1200K') == 1200
    assert special_distance_km(600, 'Gold Rush 1200K') == 1200
    assert canonical_distance_km(600, 'Golden Gate 1000k') == 1000
    assert special_distance_km(600, 'Golden Gate 1000k') == 1000


def test_numeric_special_distances_remain_distinct_from_600k():
    assert canonical_distance_km(1000, 'Long brevet') == 1000
    assert canonical_distance_km(1200, 'Long brevet') == 1200
    assert special_distance_km(600, 'Regular 600K') is None
