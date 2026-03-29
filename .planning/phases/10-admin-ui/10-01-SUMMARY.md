# Plan 10-01 Summary: Personality Admin Pages

## What was built
- **models.py**: Added `get_trait_evidence(rider_id, extraction_source=None)` and `get_all_guardrails(rule_type=None)` helper functions
- **routes/admin.py**: Added `compute_completeness()` helper, `PERSONALITY_ENUMS` and `COACH_TRAIT_FIELDS` constants, 3 routes (GET list, GET/POST edit)
- **templates/admin/personalities.html**: Team list with X/8 completeness, confidence badges (HIGH/MED/LOW), source and last updated
- **templates/admin/personality_edit.html**: Trait edit form with enum dropdowns, textarea for array fields, evidence quotes (up to 3 per trait), confidence badge, re-extraction CLI command
- **templates/admin/dashboard.html**: Added "Personalities" link
- **tests/test_admin_personality.py**: 8 skipped test stubs across 4 classes

## Routes added
- `GET /admin/personalities` — list all riders with profile completeness
- `GET /admin/personalities/<id>` — view/edit personality traits
- `POST /admin/personalities/<id>` — save traits with extraction_source='manual'

## Key decisions
- Profile priority: merged > manual > whatsapp > blog (for display)
- POST saves use extraction_source='manual' to prevent overwriting merged/whatsapp rows
- Re-extraction shown as copyable CLI command, not a live button (Vercel constraint)

## Requirements covered
ADMN-01, ADMN-02, ADMN-03, ADMN-04, ADMN-05

## Test results
171 passed, 34 skipped (including 8 new scaffolds)
