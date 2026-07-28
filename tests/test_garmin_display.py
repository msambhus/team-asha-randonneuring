from services.garmin_display import (
    training_status_description,
    training_status_label,
)


def test_internal_no_status_feedback_becomes_rider_facing_copy():
    token = "NO_STATUS_AER_LOW_SHORT"

    assert training_status_label(token) == "No Training Status"
    assert training_status_description(token) == (
        "Garmin needs more qualifying aerobic activity data.")


def test_standard_training_statuses_remain_concise():
    assert training_status_label("PRODUCTIVE") == "Productive"
    assert training_status_label("OVERREACHING") == "Overreaching"
    assert training_status_description("PRODUCTIVE") is None


def test_future_no_status_variants_do_not_leak_internal_tokens():
    assert training_status_label("NO_STATUS_SOME_NEW_REASON") == (
        "No Training Status")
