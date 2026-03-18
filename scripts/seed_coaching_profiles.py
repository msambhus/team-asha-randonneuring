#!/usr/bin/env python3
"""Seed personality profiles and coach assignments for Shriram and Venki.

Translates the personality descriptions from CHAT_SYSTEM_PROMPT and the
hardcoded _BIKE_KEYWORDS routing in chat_service.py into structured DB rows.

Run: python3 scripts/seed_coaching_profiles.py
Idempotent: safe to run multiple times (uses ON CONFLICT DO UPDATE).
"""
import os
import sys
import psycopg2
import psycopg2.extras
from datetime import date
from pathlib import Path


def get_database_url():
    """Get database URL from environment."""
    env_file = Path(__file__).parent.parent / '.env'
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                if line.startswith('DATABASE_URL='):
                    return line.strip().split('=', 1)[1]
    return os.getenv('DATABASE_URL')


# Shriram: bike/gear expert, direct style, dry humor
# Derived from _BIKE_KEYWORDS routing in chat_service.py and CHAT_SYSTEM_PROMPT
SHRIRAM_PROFILE = {
    'tone': 'direct',
    'humor_type': 'dry',
    'directness': 'high',
    'encouragement_style': 'data-driven',
    'technical_depth': 'expert',
    'signature_phrases': [
        'recognizes riders by their bikes',
        'loves gear upgrades',
        'opinionated about components',
    ],
    'topic_biases': ['bikes', 'accessories', 'components', 'gear', 'fit'],
    'topics_allowed': [
        'bike', 'gear', 'maintenance', 'components', 'fit',
        'wheels', 'tires', 'lighting',
    ],
    'extraction_source': 'manual',
    'extraction_date': date.today(),
    'extraction_confidence': 'high',
}

# Venki: general coach, playful/sarcastic, big-picture thinker
# Derived from CHAT_SYSTEM_PROMPT tone and default routing in chat_service.py
VENKI_PROFILE = {
    'tone': 'playful',
    'humor_type': 'sarcastic',
    'directness': 'medium',
    'encouragement_style': 'balanced',
    'technical_depth': 'expert',
    'signature_phrases': [
        'tongue-in-cheek humor',
        'big-picture thinker',
        'storyteller',
    ],
    'topic_biases': [
        'training philosophy', 'mental game', 'nutrition',
        'randonneuring strategy',
    ],
    'topics_allowed': [
        'training', 'nutrition', 'randonneuring', 'strategy',
        'pacing', 'mental', 'general',
    ],
    'extraction_source': 'manual',
    'extraction_date': date.today(),
    'extraction_confidence': 'high',
}

# Coach assignments: topic domains each coach handles
SHRIRAM_ASSIGNMENTS = [
    {'topic_domain': 'bikes', 'is_default': False, 'is_active': True},
    {'topic_domain': 'gear', 'is_default': False, 'is_active': True},
    {'topic_domain': 'maintenance', 'is_default': False, 'is_active': True},
]

VENKI_ASSIGNMENTS = [
    {'topic_domain': 'training', 'is_default': False, 'is_active': True},
    {'topic_domain': 'nutrition', 'is_default': False, 'is_active': True},
    {'topic_domain': 'randonneuring', 'is_default': False, 'is_active': True},
    {'topic_domain': 'general', 'is_default': True, 'is_active': True},  # fallback
]


def lookup_rider(cur, first_name_pattern):
    """Look up rider by first name (case-insensitive). Returns id or None."""
    cur.execute(
        "SELECT id, first_name, last_name FROM rider WHERE first_name ILIKE %s LIMIT 1",
        (first_name_pattern,)
    )
    row = cur.fetchone()
    if row:
        print(f"  Found rider: {row['first_name']} {row['last_name']} (id={row['id']})")
    return row['id'] if row else None


def upsert_profile(cur, rider_id, profile_data):
    """Upsert a personality profile. Returns 'created' or 'updated'."""
    col_names = list(profile_data.keys())
    col_values = list(profile_data.values())
    all_cols = ['rider_id', 'profile_type', 'updated_by'] + col_names
    all_placeholders = ['%s', '%s', '%s'] + ['%s'] * len(col_names)
    all_values = [rider_id, 'coach', 'seed_script'] + col_values

    set_parts = [f"{c} = EXCLUDED.{c}" for c in col_names]
    set_parts.append("updated_by = EXCLUDED.updated_by")
    set_parts.append("updated_at = NOW()")

    # Check if exists first for reporting
    cur.execute(
        "SELECT id FROM personality_profile WHERE rider_id = %s AND profile_type = 'coach' AND deleted_at IS NULL",
        (rider_id,)
    )
    existed = cur.fetchone() is not None

    cur.execute(
        f"""INSERT INTO personality_profile ({', '.join(all_cols)})
            VALUES ({', '.join(all_placeholders)})
            ON CONFLICT (rider_id, profile_type) DO UPDATE SET
            {', '.join(set_parts)}""",
        all_values
    )
    return 'updated' if existed else 'created'


def upsert_assignment(cur, coach_rider_id, assignment):
    """Upsert a coach assignment. Returns 'created' or 'updated'."""
    topic_domain = assignment['topic_domain']
    is_default = assignment['is_default']
    is_active = assignment['is_active']

    cur.execute(
        "SELECT id FROM coach_assignment WHERE coach_rider_id = %s AND topic_domain = %s AND deleted_at IS NULL",
        (coach_rider_id, topic_domain)
    )
    existed = cur.fetchone() is not None

    cur.execute(
        """INSERT INTO coach_assignment (coach_rider_id, topic_domain, is_default, is_active, updated_by)
           VALUES (%s, %s, %s, %s, 'seed_script')
           ON CONFLICT (coach_rider_id, topic_domain) DO UPDATE SET
           is_default = EXCLUDED.is_default,
           is_active = EXCLUDED.is_active,
           updated_by = EXCLUDED.updated_by,
           updated_at = NOW()""",
        (coach_rider_id, topic_domain, is_default, is_active)
    )
    return 'updated' if existed else 'created'


def seed():
    """Main seed function."""
    print("=" * 60)
    print("Seeding Personality Profiles & Coach Assignments")
    print("=" * 60)
    print()

    db_url = get_database_url()
    if not db_url:
        print("✗ Error: DATABASE_URL not found")
        print("  Set DATABASE_URL environment variable or create .env file")
        return False

    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        profile_count = 0
        assignment_count = 0

        # Seed Shriram
        print("Looking up Shriram...")
        shriram_id = lookup_rider(cur, 'Shriram')
        if shriram_id:
            status = upsert_profile(cur, shriram_id, SHRIRAM_PROFILE)
            print(f"  Profile: {status}")
            profile_count += 1
            for a in SHRIRAM_ASSIGNMENTS:
                s = upsert_assignment(cur, shriram_id, a)
                print(f"  Assignment '{a['topic_domain']}': {s}")
                assignment_count += 1
        else:
            print("  ⚠️  Shriram not found in rider table — skipping")

        print()

        # Seed Venki
        print("Looking up Venki...")
        venki_id = lookup_rider(cur, 'Venk%')
        if venki_id:
            status = upsert_profile(cur, venki_id, VENKI_PROFILE)
            print(f"  Profile: {status}")
            profile_count += 1
            for a in VENKI_ASSIGNMENTS:
                s = upsert_assignment(cur, venki_id, a)
                print(f"  Assignment '{a['topic_domain']}': {s}")
                assignment_count += 1
        else:
            print("  ⚠️  Venki not found in rider table — skipping")

        conn.commit()

        print()
        print("=" * 60)
        print(f"Seeded {profile_count} profiles, {assignment_count} coach assignments")
        print("=" * 60)
        return True

    except psycopg2.Error as e:
        print(f"✗ Database error: {e}")
        if conn:
            conn.rollback()
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


if __name__ == '__main__':
    try:
        success = seed()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nSeed cancelled by user")
        sys.exit(1)
