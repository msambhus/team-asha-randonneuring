"""Test scaffolds for gear admin functionality (Plan 10-02)."""
import pytest


class TestGearAdmin:
    """Tests for gear admin pages."""

    @pytest.mark.skip(reason="scaffold — implement after plan 10-02 task 1")
    def test_gear_list_shows_riders(self):
        """Gear list page renders with rider data."""
        pass

    @pytest.mark.skip(reason="scaffold — implement after plan 10-02 task 1")
    def test_gear_edit_saves_preference(self):
        """POST to gear edit calls upsert_gear_preference."""
        pass

    @pytest.mark.skip(reason="scaffold — implement after plan 10-02 task 1")
    def test_gear_edit_converts_year_to_int(self):
        """bike_year form value converted from string to integer."""
        pass

    @pytest.mark.skip(reason="scaffold — implement after plan 10-02 task 1")
    def test_gear_edit_empty_fields_as_none(self):
        """Empty string fields saved as None."""
        pass
