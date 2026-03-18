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


# ========== CRUD Function Test Stubs (Plan 07-02) ==========


@pytest.mark.skip(reason="CRUD functions not yet implemented — Plan 07-02")
def test_crud_personality_profile():
    """Will test upsert, read, soft-delete for personality_profile."""
    pass


@pytest.mark.skip(reason="CRUD functions not yet implemented — Plan 07-02")
def test_crud_guardrail():
    """Will test insert, read, update (version increment), soft-delete for guardrails."""
    pass


@pytest.mark.skip(reason="Seed data not yet created — Plan 07-03")
def test_seed_shriram_profile():
    """Will verify Shriram's seeded coach profile and assignments."""
    pass


@pytest.mark.skip(reason="Seed data not yet created — Plan 07-03")
def test_seed_venki_profile():
    """Will verify Venki's seeded coach profile and assignments."""
    pass
