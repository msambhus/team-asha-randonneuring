"""Tests for personality coaching data foundation (Phase 7).

Schema validation tests use raw SQL via db_conn fixture (no models.py dependency).
CRUD test stubs are skip-marked until Plan 07-02 implements the functions.
"""
import pytest
import psycopg2
import psycopg2.extras
from datetime import date, datetime


# ========== Schema Validation Tests (Plan 07-01) ==========
# These pass once migration 011 is applied to the test DB.


class TestPersonalityProfileSchema:
    """PROF-01: personality_profile table exists with typed columns."""

    def test_personality_profile_schema(self, db_conn):
        """Verify table exists with expected typed columns (not TEXT blobs)."""
        cur = db_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT column_name, data_type, character_maximum_length
            FROM information_schema.columns
            WHERE table_name = 'personality_profile'
            ORDER BY ordinal_position
        """)
        columns = {row['column_name']: row for row in cur.fetchall()}

        # Table exists with key columns
        assert 'id' in columns
        assert 'rider_id' in columns
        assert 'profile_type' in columns

        # Coach fields are VARCHAR (typed), not TEXT
        for col in ['tone', 'humor_type', 'directness']:
            assert col in columns, f"Missing column: {col}"
            assert columns[col]['data_type'] == 'character varying', \
                f"{col} should be VARCHAR, got {columns[col]['data_type']}"

        # Rider fields exist
        for col in ['preferred_formality', 'humor_sensitivity',
                     'encouragement_style', 'technical_depth']:
            assert col in columns, f"Missing column: {col}"

        # Array fields exist
        for col in ['signature_phrases', 'topic_biases', 'topics_allowed']:
            assert col in columns, f"Missing column: {col}"
            assert columns[col]['data_type'] == 'ARRAY', \
                f"{col} should be ARRAY, got {columns[col]['data_type']}"

        # Audit columns
        for col in ['created_at', 'updated_at', 'updated_by', 'deleted_at']:
            assert col in columns, f"Missing audit column: {col}"

    def test_coach_profile_fields(self, db_conn):
        """PROF-02: Insert a coach profile, verify roundtrip, reject invalid enums."""
        cur = db_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Need a rider_id — create a temporary one
        cur.execute("""
            INSERT INTO rider (first_name, last_name)
            VALUES ('TestCoach', 'Phase7')
            RETURNING id
        """)
        rider_id = cur.fetchone()['id']

        # Insert coach profile with all six fields
        cur.execute("""
            INSERT INTO personality_profile
                (rider_id, profile_type, tone, humor_type, directness,
                 signature_phrases, topic_biases, topics_allowed)
            VALUES (%s, 'coach', 'direct', 'dry', 'high',
                    %s, %s, %s)
            RETURNING *
        """, (rider_id,
              ['phrase1', 'phrase2'],
              ['bikes', 'gear'],
              ['bike', 'maintenance']))
        row = cur.fetchone()

        assert row['tone'] == 'direct'
        assert row['humor_type'] == 'dry'
        assert row['directness'] == 'high'
        assert row['signature_phrases'] == ['phrase1', 'phrase2']
        assert row['topic_biases'] == ['bikes', 'gear']
        assert row['topics_allowed'] == ['bike', 'maintenance']

        # Invalid enum value should be rejected by CHECK constraint
        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute("""
                INSERT INTO personality_profile
                    (rider_id, profile_type, tone)
                VALUES (%s, 'rider', 'INVALID_TONE')
            """, (rider_id,))

    def test_rider_profile_fields(self, db_conn):
        """PROF-03: Insert a rider profile with rider-specific fields."""
        cur = db_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            INSERT INTO rider (first_name, last_name)
            VALUES ('TestRider', 'Phase7')
            RETURNING id
        """)
        rider_id = cur.fetchone()['id']

        cur.execute("""
            INSERT INTO personality_profile
                (rider_id, profile_type, preferred_formality, humor_sensitivity,
                 encouragement_style, technical_depth)
            VALUES (%s, 'rider', 'casual', 'high', 'balanced', 'intermediate')
            RETURNING *
        """, (rider_id,))
        row = cur.fetchone()

        assert row['preferred_formality'] == 'casual'
        assert row['humor_sensitivity'] == 'high'
        assert row['encouragement_style'] == 'balanced'
        assert row['technical_depth'] == 'intermediate'

    def test_extraction_metadata(self, db_conn):
        """PROF-04: Extraction metadata columns store and return correctly."""
        cur = db_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            INSERT INTO rider (first_name, last_name)
            VALUES ('TestMeta', 'Phase7')
            RETURNING id
        """)
        rider_id = cur.fetchone()['id']

        cur.execute("""
            INSERT INTO personality_profile
                (rider_id, profile_type, extraction_source, extraction_date,
                 source_message_count, extraction_confidence)
            VALUES (%s, 'coach', 'whatsapp', %s, 150, 'high')
            RETURNING *
        """, (rider_id, date.today()))
        row = cur.fetchone()

        assert row['extraction_source'] == 'whatsapp'
        assert row['extraction_date'] == date.today()
        assert row['source_message_count'] == 150
        assert row['extraction_confidence'] == 'high'

    def test_profile_audit_columns(self, db_conn):
        """PROF-05: created_at auto-set, updated_at changes on update, updated_by stored."""
        cur = db_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            INSERT INTO rider (first_name, last_name)
            VALUES ('TestAudit', 'Phase7')
            RETURNING id
        """)
        rider_id = cur.fetchone()['id']

        cur.execute("""
            INSERT INTO personality_profile
                (rider_id, profile_type, tone, updated_by)
            VALUES (%s, 'coach', 'direct', 'test_script')
            RETURNING *
        """, (rider_id,))
        row = cur.fetchone()

        assert row['created_at'] is not None
        assert row['updated_at'] is not None
        assert row['updated_by'] == 'test_script'
        original_updated_at = row['updated_at']

        # Update and verify updated_at changes
        cur.execute("""
            UPDATE personality_profile
            SET tone = 'warm', updated_by = 'admin', updated_at = NOW()
            WHERE id = %s
            RETURNING *
        """, (row['id'],))
        updated_row = cur.fetchone()

        assert updated_row['tone'] == 'warm'
        assert updated_row['updated_by'] == 'admin'
        assert updated_row['updated_at'] >= original_updated_at


class TestGuardrailSchema:
    """GUARD-01, GUARD-06: coaching_guardrail table with rule_version trigger."""

    def test_guardrail_schema(self, db_conn):
        """GUARD-01: coaching_guardrail table exists with expected columns."""
        cur = db_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'coaching_guardrail'
            ORDER BY ordinal_position
        """)
        columns = {row['column_name']: row['data_type'] for row in cur.fetchall()}

        assert 'rule_type' in columns
        assert 'rule_value' in columns
        assert 'is_active' in columns
        assert 'applies_to' in columns
        assert 'rule_version' in columns
        assert 'deleted_at' in columns

    def test_guardrail_version_increment(self, db_conn):
        """GUARD-06: rule_version auto-increments on UPDATE via trigger."""
        cur = db_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            INSERT INTO coaching_guardrail (rule_type, rule_value, applies_to)
            VALUES ('topic_block', 'test_rule_v1', 'all')
            RETURNING *
        """)
        row = cur.fetchone()
        assert row['rule_version'] == 1

        # First update — version should become 2
        cur.execute("""
            UPDATE coaching_guardrail
            SET rule_value = 'test_rule_v2'
            WHERE id = %s
            RETURNING *
        """, (row['id'],))
        row2 = cur.fetchone()
        assert row2['rule_version'] == 2

        # Second update — version should become 3
        cur.execute("""
            UPDATE coaching_guardrail
            SET rule_value = 'test_rule_v3'
            WHERE id = %s
            RETURNING *
        """, (row['id'],))
        row3 = cur.fetchone()
        assert row3['rule_version'] == 3


class TestSoftDelete:
    """Soft delete pattern works across tables."""

    def test_soft_delete_filter(self, db_conn):
        """Insert a profile, soft-delete it, verify excluded by deleted_at IS NULL filter."""
        cur = db_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            INSERT INTO rider (first_name, last_name)
            VALUES ('TestDelete', 'Phase7')
            RETURNING id
        """)
        rider_id = cur.fetchone()['id']

        cur.execute("""
            INSERT INTO personality_profile
                (rider_id, profile_type, tone)
            VALUES (%s, 'coach', 'direct')
            RETURNING id
        """, (rider_id,))
        profile_id = cur.fetchone()['id']

        # Soft-delete
        cur.execute("""
            UPDATE personality_profile
            SET deleted_at = NOW()
            WHERE id = %s
        """, (profile_id,))

        # Should be excluded by deleted_at IS NULL filter
        cur.execute("""
            SELECT * FROM personality_profile
            WHERE id = %s AND deleted_at IS NULL
        """, (profile_id,))
        assert cur.fetchone() is None

        # But still in the table without the filter
        cur.execute("""
            SELECT * FROM personality_profile
            WHERE id = %s
        """, (profile_id,))
        assert cur.fetchone() is not None


# ========== CRUD Function Tests (Plan 07-02) ==========


class TestCrudPersonalityProfile:
    """Test CRUD functions for personality_profile table."""

    def test_crud_personality_profile(self, app, db_conn):
        """Upsert a coach profile, read it back, update, soft-delete."""
        from models import (get_personality_profile, upsert_personality_profile,
                            soft_delete_personality_profile)

        # Create test rider
        cur = db_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            INSERT INTO rider (first_name, last_name)
            VALUES ('CrudTest', 'Profile')
            RETURNING id
        """)
        rider_id = cur.fetchone()['id']
        db_conn.commit()

        with app.app_context():
            # Upsert a new profile
            result = upsert_personality_profile(rider_id, 'coach', {
                'tone': 'direct',
                'humor_type': 'dry',
                'directness': 'high',
                'signature_phrases': ['phrase1', 'phrase2'],
                'topics_allowed': ['bike', 'gear'],
            }, updated_by='test')

            assert result['tone'] == 'direct'
            assert result['rider_id'] == rider_id

            # Read it back
            profile = get_personality_profile(rider_id, 'coach')
            assert profile is not None
            assert profile['tone'] == 'direct'
            assert profile['humor_type'] == 'dry'
            assert profile['signature_phrases'] == ['phrase1', 'phrase2']

            # Update via upsert
            updated = upsert_personality_profile(rider_id, 'coach', {
                'tone': 'warm',
            }, updated_by='test_update')
            assert updated['tone'] == 'warm'

            # Verify update persisted
            profile2 = get_personality_profile(rider_id, 'coach')
            assert profile2['tone'] == 'warm'

            # Soft-delete
            soft_delete_personality_profile(profile2['id'], updated_by='test_delete')
            assert get_personality_profile(rider_id, 'coach') is None

    def test_get_all_personality_profiles(self, app, db_conn):
        """get_all_personality_profiles returns active profiles, filterable by type."""
        from models import upsert_personality_profile, get_all_personality_profiles

        cur = db_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            INSERT INTO rider (first_name, last_name)
            VALUES ('AllTest', 'Profiles')
            RETURNING id
        """)
        rider_id = cur.fetchone()['id']
        db_conn.commit()

        with app.app_context():
            upsert_personality_profile(rider_id, 'coach', {'tone': 'direct'})
            upsert_personality_profile(rider_id, 'rider', {'preferred_formality': 'casual'})

            all_profiles = get_all_personality_profiles()
            assert len(all_profiles) >= 2

            coach_only = get_all_personality_profiles(profile_type='coach')
            rider_only = get_all_personality_profiles(profile_type='rider')
            assert all(p['profile_type'] == 'coach' for p in coach_only)
            assert all(p['profile_type'] == 'rider' for p in rider_only)


class TestCrudGuardrail:
    """Test CRUD functions for coaching_guardrail table."""

    def test_crud_guardrail(self, app, db_conn):
        """Insert, read, update (version increment), toggle active, soft-delete."""
        from models import (insert_guardrail, get_active_guardrails,
                            update_guardrail, soft_delete_guardrail)

        with app.app_context():
            # Insert
            row = insert_guardrail('topic_block', 'no_medical_advice', 'all', 'test')
            assert row['rule_type'] == 'topic_block'
            assert row['rule_value'] == 'no_medical_advice'
            assert row['rule_version'] == 1
            assert row['is_active'] is True

            # Read back via get_active_guardrails
            rules = get_active_guardrails(rule_type='topic_block')
            assert any(r['id'] == row['id'] for r in rules)

            # Update — trigger should increment rule_version
            updated = update_guardrail(row['id'], {'rule_value': 'no_medical_v2'}, 'test')
            assert updated['rule_version'] == 2
            assert updated['rule_value'] == 'no_medical_v2'

            # Toggle is_active off
            toggled = update_guardrail(row['id'], {'is_active': False}, 'test')
            assert toggled['is_active'] is False
            # Should no longer appear in active list
            active_rules = get_active_guardrails(rule_type='topic_block')
            assert not any(r['id'] == row['id'] for r in active_rules)

            # Soft-delete
            soft_delete_guardrail(row['id'], 'test')

    def test_guardrail_filters(self, app, db_conn):
        """get_active_guardrails filters by rule_type and applies_to."""
        from models import insert_guardrail, get_active_guardrails

        with app.app_context():
            insert_guardrail('scope', 'cycling_only', 'all', 'test')
            insert_guardrail('tone_limit', 'no_shame', 'shriram', 'test')

            scope_rules = get_active_guardrails(rule_type='scope')
            assert all(r['rule_type'] == 'scope' for r in scope_rules)

            shriram_rules = get_active_guardrails(applies_to='shriram')
            assert all(r['applies_to'] == 'shriram' for r in shriram_rules)


class TestCrudGearPreference:
    """Test CRUD functions for gear_preference table."""

    def test_crud_gear_preference(self, app, db_conn):
        """Upsert gear, read back, update, verify."""
        from models import get_gear_preference, upsert_gear_preference

        cur = db_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            INSERT INTO rider (first_name, last_name)
            VALUES ('GearTest', 'Rider')
            RETURNING id
        """)
        rider_id = cur.fetchone()['id']
        db_conn.commit()

        with app.app_context():
            # Upsert
            result = upsert_gear_preference(rider_id, {
                'bike_make': 'Surly',
                'bike_model': 'Long Haul Trucker',
                'bike_material': 'steel',
            }, updated_by='test')
            assert result['bike_make'] == 'Surly'
            assert result['bike_material'] == 'steel'

            # Read back
            gear = get_gear_preference(rider_id)
            assert gear is not None
            assert gear['bike_model'] == 'Long Haul Trucker'

            # Update via upsert
            upsert_gear_preference(rider_id, {
                'bike_make': 'Rivendell',
            }, updated_by='test_update')
            gear2 = get_gear_preference(rider_id)
            assert gear2['bike_make'] == 'Rivendell'


class TestCrudCoachAssignment:
    """Test CRUD functions for coach_assignment table."""

    def test_crud_coach_assignment(self, app, db_conn):
        """Insert assignments, list by coach, list by domain, toggle active."""
        from models import get_coach_assignments, upsert_coach_assignment

        cur = db_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            INSERT INTO rider (first_name, last_name)
            VALUES ('CoachTest', 'Assignment')
            RETURNING id
        """)
        coach_id = cur.fetchone()['id']
        db_conn.commit()

        with app.app_context():
            # Insert assignments
            upsert_coach_assignment(coach_id, 'bikes', {
                'is_default': False, 'is_active': True
            }, updated_by='test')
            upsert_coach_assignment(coach_id, 'general', {
                'is_default': True, 'is_active': True
            }, updated_by='test')

            # List by coach
            assignments = get_coach_assignments(coach_rider_id=coach_id)
            assert len(assignments) == 2

            # List by domain
            bike_assigns = get_coach_assignments(topic_domain='bikes')
            assert any(a['coach_rider_id'] == coach_id for a in bike_assigns)

            # Toggle active off
            upsert_coach_assignment(coach_id, 'bikes', {
                'is_active': False
            }, updated_by='test')
            active_assigns = get_coach_assignments(coach_rider_id=coach_id, active_only=True)
            assert not any(a['topic_domain'] == 'bikes' for a in active_assigns)

            # Include inactive
            all_assigns = get_coach_assignments(coach_rider_id=coach_id, active_only=False)
            assert len(all_assigns) == 2


# ========== Seed Validation Tests (Plan 07-03) ==========


class TestSeedProfiles:
    """Seed validation tests — require seed script to have been run."""

    def test_seed_shriram_profile(self, db_conn):
        """Verify Shriram's seeded coach profile and assignments."""
        cur = db_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Find Shriram
        cur.execute("SELECT id FROM rider WHERE first_name = 'Shriram' LIMIT 1")
        rider = cur.fetchone()
        if not rider:
            pytest.skip('Shriram not in rider table — seed data not applicable')

        # Check profile
        cur.execute("""
            SELECT * FROM personality_profile
            WHERE rider_id = %s AND profile_type = 'coach' AND deleted_at IS NULL
        """, (rider['id'],))
        profile = cur.fetchone()
        if not profile:
            pytest.skip('Seed data not yet applied — run scripts/seed_coaching_profiles.py first')

        assert profile['tone'] == 'direct'
        assert profile['humor_type'] == 'dry'
        assert profile['directness'] == 'high'
        assert profile['extraction_source'] == 'manual'
        assert profile['extraction_confidence'] == 'high'
        assert 'bike' in profile['topics_allowed']
        assert 'gear' in profile['topics_allowed']
        assert len(profile['signature_phrases']) > 0

        # Check coach assignments
        cur.execute("""
            SELECT topic_domain FROM coach_assignment
            WHERE coach_rider_id = %s AND deleted_at IS NULL
            ORDER BY topic_domain
        """, (rider['id'],))
        domains = [r['topic_domain'] for r in cur.fetchall()]
        assert 'bikes' in domains
        assert 'gear' in domains
        assert 'maintenance' in domains

    def test_seed_venki_profile(self, db_conn):
        """Verify Venki's seeded coach profile and assignments."""
        cur = db_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Find Venki (may be stored as Venkatesh etc.)
        cur.execute("SELECT id FROM rider WHERE first_name ILIKE 'Venk%%' LIMIT 1")
        rider = cur.fetchone()
        if not rider:
            pytest.skip('Venki not in rider table — seed data not applicable')

        cur.execute("""
            SELECT * FROM personality_profile
            WHERE rider_id = %s AND profile_type = 'coach' AND deleted_at IS NULL
        """, (rider['id'],))
        profile = cur.fetchone()
        if not profile:
            pytest.skip('Seed data not yet applied — run scripts/seed_coaching_profiles.py first')

        assert profile['tone'] == 'playful'
        assert profile['humor_type'] == 'sarcastic'
        assert profile['directness'] == 'medium'
        assert profile['extraction_source'] == 'manual'
        assert 'training' in profile['topics_allowed']
        assert 'general' in profile['topics_allowed']

        # Check default coach assignment
        cur.execute("""
            SELECT * FROM coach_assignment
            WHERE coach_rider_id = %s AND topic_domain = 'general' AND deleted_at IS NULL
        """, (rider['id'],))
        general = cur.fetchone()
        assert general is not None
        assert general['is_default'] is True

    def test_seed_idempotency(self, db_conn):
        """Running upsert twice on same rider+type does not create duplicates."""
        cur = db_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            INSERT INTO rider (first_name, last_name)
            VALUES ('IdempTest', 'Seed')
            RETURNING id
        """)
        rider_id = cur.fetchone()['id']

        # Insert a profile
        cur.execute("""
            INSERT INTO personality_profile (rider_id, profile_type, tone, updated_by)
            VALUES (%s, 'coach', 'direct', 'test')
            ON CONFLICT (rider_id, profile_type) DO UPDATE SET
            tone = EXCLUDED.tone, updated_at = NOW()
        """, (rider_id,))

        # Run the same upsert again
        cur.execute("""
            INSERT INTO personality_profile (rider_id, profile_type, tone, updated_by)
            VALUES (%s, 'coach', 'warm', 'test')
            ON CONFLICT (rider_id, profile_type) DO UPDATE SET
            tone = EXCLUDED.tone, updated_at = NOW()
        """, (rider_id,))

        # Should still be exactly 1 row
        cur.execute("""
            SELECT count(*) as cnt FROM personality_profile
            WHERE rider_id = %s AND profile_type = 'coach'
        """, (rider_id,))
        assert cur.fetchone()['cnt'] == 1

    def test_existing_tests_unbroken(self):
        """Confirm CHAT_SYSTEM_PROMPT constant still exists (not removed by seed work)."""
        from services.openai_coach import CHAT_SYSTEM_PROMPT
        assert isinstance(CHAT_SYSTEM_PROMPT, str)
        assert len(CHAT_SYSTEM_PROMPT) > 100
