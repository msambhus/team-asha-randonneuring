"""Test scaffolds for coach roster and guardrail admin functionality (Plan 10-03)."""
import pytest


class TestCoachRoster:
    """Tests for coach roster admin page."""

    @pytest.mark.skip(reason="scaffold — implement after plan 10-03 tasks 1-2")
    def test_coaches_page_shows_assignments(self):
        """Coaches page renders with assignment data."""
        pass

    @pytest.mark.skip(reason="scaffold — implement after plan 10-03 tasks 1-2")
    def test_coach_toggle_flips_is_active(self):
        """POST to toggle route flips is_active on coach_assignment."""
        pass


class TestGuardrailAdmin:
    """Tests for guardrail admin CRUD."""

    @pytest.mark.skip(reason="scaffold — implement after plan 10-03 tasks 1-2")
    def test_guardrails_list_shows_all(self):
        """Guardrails page shows active and inactive rules."""
        pass

    @pytest.mark.skip(reason="scaffold — implement after plan 10-03 tasks 1-2")
    def test_guardrail_toggle_changes_state(self):
        """POST to toggle route flips is_active."""
        pass

    @pytest.mark.skip(reason="scaffold — implement after plan 10-03 tasks 1-2")
    def test_guardrail_create_inserts_row(self):
        """POST to new route creates guardrail."""
        pass

    @pytest.mark.skip(reason="scaffold — implement after plan 10-03 tasks 1-2")
    def test_guardrail_soft_delete(self):
        """POST to delete route sets deleted_at."""
        pass
