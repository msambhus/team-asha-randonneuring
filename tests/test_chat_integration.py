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
