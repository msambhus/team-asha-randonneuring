from shared.control_times import MILES_TO_KM, control_close_time_minutes, control_open_time_minutes


def _miles(km):
    return km / MILES_TO_KM


def test_1200k_control_closes_use_piecewise_long_brevet_rates():
    total_mi = _miles(1200)
    assert control_close_time_minutes(_miles(600), total_mi, 90, 1200) == 40 * 60
    assert control_close_time_minutes(_miles(800), total_mi, 90, 1200) == 3450
    assert control_close_time_minutes(_miles(1000), total_mi, 90, 1200) == 75 * 60
    assert control_close_time_minutes(_miles(1100), total_mi, 90, 1200) == 4950
    assert control_close_time_minutes(_miles(1200), total_mi, 90, 1200) == 90 * 60


def test_overdistance_finish_is_capped_at_nominal_cutoff():
    total_mi = _miles(1206.3)
    assert control_close_time_minutes(total_mi, total_mi, 90, 1200) == 90 * 60


def test_kilometre_callers_can_select_km_units():
    assert control_close_time_minutes(
        800, 1200, 90, 1200, distance_unit='km') == 3450


def test_shorter_plans_keep_existing_linear_behavior():
    assert control_close_time_minutes(100, 200, 20, 300) == 600


def test_opening_uses_official_maximum_arrival_rates():
    assert control_open_time_minutes(0, 'km') == 0
    assert control_open_time_minutes(200, 'km') == round((200 / 34) * 60)
    assert control_open_time_minutes(600, 'km') == round((200 / 34 + 200 / 32 + 200 / 30) * 60)
