# Plan 10-03 Summary: Coach Roster + Guardrail CRUD

## What was built
- **routes/admin.py**: Added GUARDRAIL_ENUMS constant, coach roster routes (list + toggle), guardrail CRUD routes (list, toggle, new, edit, delete)
- **templates/admin/coaches.html**: Grouped by coach with persona status badge, FALLBACK badge, domain assignment table with ON/OFF toggle
- **templates/admin/guardrails.html**: Rule list with type, truncated value, applies_to, version, toggle, edit/delete actions
- **templates/admin/guardrail_edit.html**: Create/edit form with rule_type/applies_to dropdowns and rule_value textarea
- **templates/admin/dashboard.html**: Added "Coaches" and "Guardrails" links
- **tests/test_admin_coaches.py**: 6 skipped test stubs across 2 classes

## Routes added
- `GET /admin/coaches` — coach roster with domain assignments
- `POST /admin/coaches/<id>/<domain>/toggle` — toggle coach assignment active/inactive
- `GET /admin/guardrails` — list all guardrail rules
- `POST /admin/guardrails/<id>/toggle` — toggle guardrail active/inactive
- `GET/POST /admin/guardrails/new` — create new guardrail
- `GET/POST /admin/guardrails/<id>/edit` — edit guardrail
- `POST /admin/guardrails/<id>/delete` — soft-delete guardrail

## Requirements covered
COACH-01, GUARD-02, GUARD-03, GUARD-04, GUARD-05

## Test results
171 passed, 44 skipped
