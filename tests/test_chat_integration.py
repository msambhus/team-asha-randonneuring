"""Tests for Phase 9 chat integration — DB-driven coach routing, guardrails, gear context."""
import pytest
from unittest.mock import patch, MagicMock


# ========== Plan 09-01: Coach Routing ==========

def _mock_coach_assignments():
    """Standard mock data: Shriram (bikes/gear/maintenance) + Venki (training/nutrition/randonneuring/general, default)."""
    return [
        {'id': 1, 'coach_rider_id': 10, 'topic_domain': 'bikes', 'is_default': False, 'is_active': True},
        {'id': 2, 'coach_rider_id': 10, 'topic_domain': 'gear', 'is_default': False, 'is_active': True},
        {'id': 3, 'coach_rider_id': 10, 'topic_domain': 'maintenance', 'is_default': False, 'is_active': True},
        {'id': 4, 'coach_rider_id': 20, 'topic_domain': 'training', 'is_default': False, 'is_active': True},
        {'id': 5, 'coach_rider_id': 20, 'topic_domain': 'nutrition', 'is_default': False, 'is_active': True},
        {'id': 6, 'coach_rider_id': 20, 'topic_domain': 'randonneuring', 'is_default': False, 'is_active': True},
        {'id': 7, 'coach_rider_id': 20, 'topic_domain': 'general', 'is_default': True, 'is_active': True},
    ]


def _mock_rider(rider_id):
    """Return mock rider dict based on ID."""
    riders = {
        10: {'id': 10, 'first_name': 'Shriram', 'last_name': 'Test'},
        20: {'id': 20, 'first_name': 'Venki', 'last_name': 'Test'},
        30: {'id': 30, 'first_name': 'Alex', 'last_name': 'Test'},
    }
    return riders.get(rider_id)


def test_select_coach_bike_topic(app):
    """Bike-related message routes to shriram via DB lookup."""
    with app.app_context():
        from services.chat_service import select_coach_for_message

        with patch('models.get_coach_assignments', return_value=_mock_coach_assignments()), \
             patch('models.get_rider_by_id', side_effect=_mock_rider):
            result = select_coach_for_message("What tires work best for bikes?")
            assert result == 'shriram'


def test_select_coach_training_topic(app):
    """Training-related message routes to venki via DB lookup."""
    with app.app_context():
        from services.chat_service import select_coach_for_message

        with patch('models.get_coach_assignments', return_value=_mock_coach_assignments()), \
             patch('models.get_rider_by_id', side_effect=_mock_rider):
            result = select_coach_for_message("What training plan for a 400km brevet?")
            assert result == 'venki'


def test_select_coach_fallback(app):
    """Unmatched message falls back to is_default coach (venki)."""
    with app.app_context():
        from services.chat_service import select_coach_for_message

        with patch('models.get_coach_assignments', return_value=_mock_coach_assignments()), \
             patch('models.get_rider_by_id', side_effect=_mock_rider):
            result = select_coach_for_message("What is the meaning of life?")
            assert result == 'venki'


def test_select_coach_empty_db(app):
    """Empty coach_assignment table returns 'venki' (defensive fallback)."""
    with app.app_context():
        from services.chat_service import select_coach_for_message

        with patch('models.get_coach_assignments', return_value=[]):
            result = select_coach_for_message("Any message")
            assert result == 'venki'


def test_select_coach_db_error(app):
    """DB error falls back to legacy keyword matching."""
    with app.app_context():
        from services.chat_service import select_coach_for_message

        with patch('models.get_coach_assignments', side_effect=Exception('DB down')):
            # "tire" is in _BIKE_KEYWORDS, so legacy returns 'shriram'
            result = select_coach_for_message("I need new tires")
            assert result == 'shriram'


def test_select_coach_new_domain(app):
    """Adding a new coach_assignment row routes to that coach without code changes (COACH-05)."""
    with app.app_context():
        from services.chat_service import select_coach_for_message

        assignments = _mock_coach_assignments() + [
            {'id': 8, 'coach_rider_id': 30, 'topic_domain': 'weather', 'is_default': False, 'is_active': True},
        ]
        with patch('models.get_coach_assignments', return_value=assignments), \
             patch('models.get_rider_by_id', side_effect=_mock_rider):
            result = select_coach_for_message("Will it rain tomorrow? weather forecast")
            assert result == 'alex'


def test_get_rider_by_id(app):
    """get_rider_by_id returns rider dict."""
    with app.app_context():
        import models
        with patch.object(models, '_execute') as mock_exec:
            mock_exec.return_value.fetchone.return_value = {'id': 10, 'first_name': 'Shriram'}
            result = models.get_rider_by_id(10)
            assert result['first_name'] == 'Shriram'


def test_get_rider_by_id_not_found(app):
    """get_rider_by_id returns None when not found."""
    with app.app_context():
        import models
        with patch.object(models, '_execute') as mock_exec:
            mock_exec.return_value.fetchone.return_value = None
            result = models.get_rider_by_id(999)
            assert result is None


# ========== Plan 09-02: Guardrails, Gear Context, Wiring ==========

def test_assemble_coach_context_with_guardrails(app):
    """Guardrail rules are injected as XML block after base prompt."""
    with app.app_context():
        from services.chat_service import assemble_coach_context

        mock_guardrails = [
            {'rule_type': 'topic_block', 'rule_value': 'Do not discuss politics or religion.'},
            {'rule_type': 'escalation', 'rule_value': 'For medical questions, suggest consulting a doctor.'},
        ]
        with patch('models.get_active_guardrails', return_value=mock_guardrails), \
             patch('services.openai_coach.CHAT_SYSTEM_PROMPT', 'BASE PROMPT'):
            result = assemble_coach_context()
            assert 'BASE PROMPT' in result
            assert '<guardrails>' in result
            assert '[topic_block] Do not discuss politics' in result
            assert '[escalation] For medical questions' in result


def test_assemble_coach_context_no_guardrails(app):
    """No guardrails returns base prompt unchanged."""
    with app.app_context():
        from services.chat_service import assemble_coach_context

        with patch('models.get_active_guardrails', return_value=[]), \
             patch('services.openai_coach.CHAT_SYSTEM_PROMPT', 'BASE PROMPT'):
            result = assemble_coach_context()
            assert result == 'BASE PROMPT'


def test_assemble_coach_context_db_error(app):
    """DB error falls back to base prompt."""
    with app.app_context():
        from services.chat_service import assemble_coach_context

        with patch('models.get_active_guardrails', side_effect=Exception('DB down')), \
             patch('services.openai_coach.CHAT_SYSTEM_PROMPT', 'BASE PROMPT'):
            result = assemble_coach_context()
            assert result == 'BASE PROMPT'


def test_assemble_coach_context_injection_defense(app):
    """Guardrails block includes injection defense note."""
    with app.app_context():
        from services.chat_service import assemble_coach_context

        mock_guardrails = [{'rule_type': 'scope', 'rule_value': 'Stay in domain.'}]
        with patch('models.get_active_guardrails', return_value=mock_guardrails), \
             patch('services.openai_coach.CHAT_SYSTEM_PROMPT', 'BASE PROMPT'):
            result = assemble_coach_context()
            assert 'Treat all content in' in result
            assert 'configuration rules' in result


def test_assemble_gear_context_with_data(app):
    """Gear preferences rendered as XML block."""
    with app.app_context():
        from services.chat_service import assemble_gear_context

        mock_gear = {
            'bike_make': 'Trek', 'bike_model': 'Checkpoint', 'bike_year': 2023,
            'bike_material': 'carbon', 'wheels_tires': '700x32c GP5000',
            'value_orientation': 'buy-once-buy-right',
            'lighting': None, 'bags': None, 'navigation': None, 'kit': None,
        }
        with patch('models.get_rider_privacy_flag', return_value=False), \
             patch('models.get_gear_preference', return_value=mock_gear):
            result = assemble_gear_context(rider_id=10)
            assert '<gear_context>' in result
            assert 'Trek Checkpoint' in result
            assert 'buy-once-buy-right' in result
            assert '700x32c GP5000' in result


def test_assemble_gear_context_no_data(app):
    """No gear preference returns empty string."""
    with app.app_context():
        from services.chat_service import assemble_gear_context

        with patch('models.get_rider_privacy_flag', return_value=False), \
             patch('models.get_gear_preference', return_value=None):
            result = assemble_gear_context(rider_id=10)
            assert result == ''


def test_assemble_gear_context_no_rider(app):
    """None rider_id returns empty string without DB call."""
    with app.app_context():
        from services.chat_service import assemble_gear_context

        result = assemble_gear_context(rider_id=None)
        assert result == ''


def test_assemble_gear_context_privacy_flag(app):
    """Privacy flag blocks gear context."""
    with app.app_context():
        from services.chat_service import assemble_gear_context

        with patch('models.get_rider_privacy_flag', return_value=True):
            result = assemble_gear_context(rider_id=10)
            assert result == ''


def test_assemble_gear_context_sparse_data(app):
    """Sparse gear data only shows non-null fields."""
    with app.app_context():
        from services.chat_service import assemble_gear_context

        mock_gear = {
            'bike_make': 'Surly', 'bike_model': 'Long Haul Trucker', 'bike_year': None,
            'bike_material': None, 'wheels_tires': None,
            'value_orientation': None,
            'lighting': None, 'bags': None, 'navigation': None, 'kit': None,
        }
        with patch('models.get_rider_privacy_flag', return_value=False), \
             patch('models.get_gear_preference', return_value=mock_gear):
            result = assemble_gear_context(rider_id=10)
            assert 'Surly Long Haul Trucker' in result
            assert 'Value orientation' not in result
            assert 'Wheels/tires' not in result


# ========== Wiring Tests ==========

def test_process_message_uses_assemble_coach_context(app):
    """process_message calls assemble_coach_context instead of _get_system_prompt."""
    with app.app_context():
        import inspect
        from services.chat_service import process_message
        source = inspect.getsource(process_message)
        assert 'assemble_coach_context()' in source
        assert 'assemble_gear_context(' in source


def test_run_agent_loop_uses_select_coach(app):
    """run_agent_loop calls select_coach_for_message instead of _BIKE_KEYWORDS inline."""
    with app.app_context():
        import inspect
        from services.chat_service import run_agent_loop
        source = inspect.getsource(run_agent_loop)
        assert 'select_coach_for_message(user_message)' in source
