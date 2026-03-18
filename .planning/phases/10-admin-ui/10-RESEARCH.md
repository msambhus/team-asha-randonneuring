# Phase 10: Admin UI - Research

**Researched:** 2026-03-18
**Domain:** Flask admin blueprint, Jinja2 templates, PostgreSQL CRUD via psycopg2
**Confidence:** HIGH

## Summary

Phase 10 builds the admin UI for managing personality profiles, gear preferences, coach assignments, and guardrail rules. All four backing tables (`personality_profile`, `gear_preference`, `coach_assignment`, `coaching_guardrail`) already exist from Phases 7 and 8. All model CRUD functions already exist in `models.py`. The extraction scripts already exist at `scripts/extract_personality_*.py`. This phase is purely a UI layer on top of already-complete infrastructure.

The existing admin blueprint (`routes/admin.py`) provides the exact pattern to follow: `_require_admin()` guard, `@user_login_required` decorator, Flask flash messages, standard Jinja2 templates extending `base.html`. No new libraries, frameworks, or JS build tooling are needed — the same inline-style Tailwind CSS pattern used across all other admin pages is the correct approach.

The re-extraction trigger (ADMN-05) requires careful scoping. The extraction scripts are CLI-only by architectural decision (Vercel serverless constraint). The admin trigger must call `subprocess.run()` or queue the script as a background task — it cannot block the request. Given Vercel's 60s/300s limit, a fire-and-forget subprocess with a status page refresh is the correct pattern. Alternatively, the re-extraction can be implemented as a POST endpoint that queues the run and returns immediately, with the admin refreshing the page manually to see results.

**Primary recommendation:** Add three new route groups to `routes/admin.py` (personalities, gear, coaches+guardrails) following the existing `mark_status` / `strava_status` pattern exactly. Use standard HTML form POSTs with flash messages — no AJAX required except for the guardrail active/inactive toggle which benefits from inline toggle without full page reload.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| ADMN-01 | Admin views list of all team members with profile completeness indicator | `get_all_riders()` provides rider list; join with `get_all_personality_profiles()` to compute completeness per rider |
| ADMN-02 | Admin views and edits personality traits with structured fields (dropdowns/text) | `upsert_personality_profile()` in models.py; CHECK constraints define valid enum values |
| ADMN-03 | Admin sees source example quotes alongside each trait for verification | `personality_trait_evidence` table (migration 012); query by rider_id + trait_name |
| ADMN-04 | Admin sees confidence badge per trait (warns on LOW confidence) | `extraction_confidence` column in `personality_profile` — already stored as 'high'/'medium'/'low' |
| ADMN-05 | Admin triggers re-extraction per person from source data | `scripts/extract_personality_whatsapp.py` and `scripts/extract_personality_blog.py` — must be called via subprocess, not inline |
| ADMN-06 | Admin views and edits gear preferences per rider | `upsert_gear_preference()` + `get_gear_preference()` in models.py |
| GEAR-01 | Admin captures gear per rider: bike make/model/year/material, wheels/tires, lighting, bags, navigation, kit | `gear_preference` table has all these columns; typed inputs map directly to VARCHAR/INTEGER columns |
| GEAR-02 | Admin sets value orientation per rider (budget/mid-range/premium/buy-once-buy-right) | `value_orientation` column with CHECK constraint in `gear_preference` — use a `<select>` dropdown |
| COACH-01 | Admin views coach roster with persona status and active/inactive toggle | `get_coach_assignments()` + `get_all_personality_profiles(profile_type='coach')` provide all needed data |
| GUARD-02 | Admin configures topic scope per coach (what each coach can/cannot answer) | `coaching_guardrail` with `rule_type='scope'` + `applies_to` column |
| GUARD-03 | Admin configures tone limits (e.g., never shame rider for fitness) | `coaching_guardrail` with `rule_type='tone_limit'` |
| GUARD-04 | Admin configures escalation rules (when to deflect to doctor, RUSA, etc.) | `coaching_guardrail` with `rule_type='escalation'` |
| GUARD-05 | Admin toggles individual guardrail rules active/inactive without code deploy | `update_guardrail()` with `{'is_active': False/True}` — rule_version auto-increments via DB trigger |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Flask Blueprint | Existing | Route organization | Already used; `admin_bp` is the extension point |
| Jinja2 | Existing | HTML templating | Already used for all admin pages |
| psycopg2 | Existing | DB access via models.py | All CRUD functions already written |
| Tailwind CSS | Existing | Styling via inline classes | All admin pages use same pattern |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| subprocess | stdlib | Run extraction scripts | Re-extraction trigger (ADMN-05) only |
| Flask flash | Existing | User feedback on save | All POST actions |
| HTML `<select>` | HTML | Dropdown for enums | tone, humor_type, directness, bike_material, value_orientation |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Full-page POST saves | AJAX JSON API | AJAX adds complexity; flash messages + redirect is simpler and consistent with existing pattern |
| subprocess for re-extraction | Background worker/Celery | Celery is too heavy; subprocess with fire-and-forget is sufficient for infrequent admin action |
| Separate admin framework | Flask-Admin | Adds 3rd-party dependency; existing blueprint pattern is explicit and simple |

**Installation:**
No new packages required. Everything runs on the existing stack.

## Architecture Patterns

### Recommended Project Structure
```
routes/
└── admin.py              # Add new route groups here (no new files needed)

templates/admin/
├── personalities.html     # ADMN-01: team list with completeness
├── personality_edit.html  # ADMN-02/03/04/05: edit traits + evidence + re-extract
├── gear.html              # ADMN-06/GEAR-01/GEAR-02: per-rider gear list
├── gear_edit.html         # GEAR-01/02: gear form for a single rider
├── coaches.html           # COACH-01: coach roster + domain assignments
└── guardrails.html        # GUARD-02/03/04/05: guardrail CRUD with toggle
```

### Pattern 1: Admin Route Group (follow existing admin.py pattern)

**What:** Each admin section follows: GET list page, GET/POST edit page, POST action endpoint.
**When to use:** All six new admin pages.

```python
# In routes/admin.py — append new route groups

@admin_bp.route('/personalities')
@user_login_required
def personalities():
    _require_admin()
    riders = get_all_riders()
    profiles = {p['rider_id']: p for p in get_all_personality_profiles()}
    # Compute completeness per rider
    return render_template('admin/personalities.html', riders=riders, profiles=profiles)

@admin_bp.route('/personalities/<int:rider_id>', methods=['GET', 'POST'])
@user_login_required
def personality_edit(rider_id):
    _require_admin()
    if request.method == 'POST':
        fields = {k: v for k, v in request.form.items() if v.strip()}
        # Handle array fields (signature_phrases)
        upsert_personality_profile(rider_id, 'coach', fields, updated_by='admin')
        flash('Profile saved.', 'success')
        return redirect(url_for('admin.personality_edit', rider_id=rider_id))
    profile = get_personality_profile(rider_id, 'coach')
    evidence = get_trait_evidence(rider_id)   # new model function needed
    return render_template('admin/personality_edit.html',
                           rider=get_rider_by_id(rider_id),
                           profile=profile, evidence=evidence)
```

### Pattern 2: Completeness Indicator (ADMN-01)

**What:** Count non-null trait fields vs total trait fields to compute X/N filled.
**When to use:** Team member list page.

```python
# In route handler or Jinja2 macro
TRAIT_FIELDS = ['tone', 'humor_type', 'directness', 'signature_phrases',
                'topic_biases', 'topics_allowed', 'encouragement_style', 'technical_depth']

def profile_completeness(profile):
    if not profile:
        return 0, len(TRAIT_FIELDS)
    filled = sum(1 for f in TRAIT_FIELDS if profile.get(f))
    return filled, len(TRAIT_FIELDS)
```

### Pattern 3: Guardrail Toggle (GUARD-05)

**What:** Single-field POST to toggle `is_active` — no full form reload needed.
**When to use:** Guardrail list page active/inactive toggle.

```python
@admin_bp.route('/guardrails/<int:guardrail_id>/toggle', methods=['POST'])
@user_login_required
def toggle_guardrail(guardrail_id):
    _require_admin()
    # Read current state, flip it
    rules = get_active_guardrails()  # Note: need a get_guardrail_by_id helper or fetch all
    update_guardrail(guardrail_id, {'is_active': not current_active}, updated_by='admin')
    flash('Guardrail updated.', 'success')
    return redirect(url_for('admin.guardrails'))
```

### Pattern 4: Re-Extraction Trigger (ADMN-05)

**What:** POST endpoint fires extraction script as subprocess and redirects immediately.
**When to use:** "Re-extract" button on personality edit page.

```python
@admin_bp.route('/personalities/<int:rider_id>/re-extract', methods=['POST'])
@user_login_required
def re_extract_personality(rider_id):
    _require_admin()
    import subprocess
    rider = get_rider_by_id(rider_id)
    # Fire-and-forget: returns before script finishes
    subprocess.Popen(
        ['python', 'scripts/extract_personality_whatsapp.py',
         '--sender', rider['first_name'],
         '--profile-type', 'coach'],
        cwd='/path/to/project'
    )
    flash('Re-extraction started. Refresh in 30 seconds to see updated traits.', 'info')
    return redirect(url_for('admin.personality_edit', rider_id=rider_id))
```

**Note:** This pattern works for local dev but NOT on Vercel (serverless, no persistent processes). On Vercel, re-extraction must be a local CLI operation only. The admin UI button should either be hidden in production or display a "run locally" instruction. A simpler alternative: omit the subprocess pattern entirely and document that re-extraction is always CLI-only. The admin page can show a "Last extracted" timestamp and provide CLI command text to copy.

### Pattern 5: Array Fields (signature_phrases, topic_biases)

**What:** Postgres TEXT[] fields need special handling from HTML forms.
**When to use:** personality_profile.signature_phrases, topic_biases, topics_allowed.

```python
# In POST handler: split comma-separated textarea into list
raw = request.form.get('signature_phrases', '')
phrases = [p.strip() for p in raw.split('\n') if p.strip()]
fields['signature_phrases'] = phrases  # psycopg2 serializes list as TEXT[]
```

```html
<!-- In template: join array for textarea display -->
<textarea name="signature_phrases">{{ profile.signature_phrases | join('\n') if profile and profile.signature_phrases else '' }}</textarea>
```

### Anti-Patterns to Avoid
- **Calling extraction scripts inline in Flask requests:** These are CLI-only scripts by architectural decision (Vercel constraint, long runtime). Never block a request waiting for GPT-4o.
- **Caching admin model functions:** All personality/coaching models explicitly say "NOT cached — admin edits must be immediately visible." Never wrap these in `@cache.memoize`.
- **Free-text blob fields:** All trait fields have CHECK constraints; always use `<select>` dropdowns for enum columns, not free text inputs.
- **Adding a new blueprint:** All admin routes belong in the existing `admin_bp`. Creating a new blueprint for personalities or coaches would break the existing auth pattern.
- **Skipping `_require_admin()`:** Every new route in admin.py must call `_require_admin()` after `@user_login_required`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Enum validation | Custom validator | HTML `<select>` with valid options + DB CHECK constraint rejects invalid values | DB already enforces valid values on INSERT/UPDATE |
| Flash messages | Custom notification system | Flask `flash()` + base.html already renders flash blocks | Already wired in base.html |
| Soft delete | Custom archive table | Set `deleted_at = NOW()` — pattern already in all tables and model functions | Already implemented in `soft_delete_guardrail()`, `soft_delete_personality_profile()` |
| Coach identification | New "is_coach" flag | `profile_type = 'coach'` in `personality_profile` identifies coaches; `coach_assignment` links coach to domains | Tables already structured correctly |
| CSRF protection | Token management | The existing admin requires session-based login; `_require_admin()` is the access guard | No CSRF library needed — existing pattern is consistent |

**Key insight:** The entire data layer is already built. Phase 10 is a presentation layer phase — routing + templates only.

## Common Pitfalls

### Pitfall 1: The UNIQUE constraint on personality_profile changed

**What goes wrong:** Calling `upsert_personality_profile()` without an `extraction_source` when the DB now requires `(rider_id, profile_type, extraction_source)` as the unique key (migration 012).
**Why it happens:** Migration 011 had `UNIQUE(rider_id, profile_type)`. Migration 012 changed it to `UNIQUE(rider_id, profile_type, extraction_source)`. The model function builds an ON CONFLICT clause against the new constraint.
**How to avoid:** Always pass `extraction_source='manual'` when saving admin edits through the UI. This creates/updates the 'manual' row, leaving 'whatsapp' and 'merged' rows intact.
**Warning signs:** `psycopg2.errors.UniqueViolation` or `ON CONFLICT` failing when saving a profile.

### Pitfall 2: get_active_guardrails excludes inactive rows

**What goes wrong:** The guardrail admin page needs to show ALL rules (active AND inactive) so admin can toggle them. But `get_active_guardrails()` filters to `is_active = TRUE`.
**Why it happens:** `get_active_guardrails()` was designed for runtime use, not admin display.
**How to avoid:** Write a new model function `get_all_guardrails(rule_type=None)` that excludes only `deleted_at IS NULL` but does NOT filter on `is_active`. The guardrail admin page uses this, not `get_active_guardrails()`.
**Warning signs:** Admin can't see inactive guardrails; toggling active/inactive appears to "delete" rules.

### Pitfall 3: get_personality_profile returns only one row per rider

**What goes wrong:** After Phase 8 extraction, a rider may have multiple `personality_profile` rows (one per extraction_source: 'whatsapp', 'blog', 'merged'). `get_personality_profile()` uses `LIMIT 1` implicitly via `fetchone()` — it may not return the 'merged' row.
**Why it happens:** `get_personality_profile()` queries by `(rider_id, profile_type)` without specifying `extraction_source`. With the new unique constraint, multiple rows can exist.
**How to avoid:** For admin display, prefer the 'merged' row if it exists, fall back to 'whatsapp', then 'manual'. Add an `extraction_source` parameter to the query: `AND extraction_source = 'merged'`. Or display all rows per rider on the edit page.
**Warning signs:** Admin saves traits into the wrong extraction_source row; merged profile gets overwritten.

### Pitfall 4: coach_assignment is_default is per-row, not per-coach

**What goes wrong:** Displaying "fallback coach" requires finding which coach has an assignment with `is_default = TRUE`. There is no global fallback configuration — it's encoded as a coach_assignment row.
**Why it happens:** The schema uses `is_default BOOLEAN` on coach_assignment rows, where one row per coach can be marked default (meaning: use this coach when no domain matches).
**How to avoid:** The coach admin page must show which coach has the `is_default=TRUE` row and allow the admin to toggle it. Enforcing only one default across all coaches requires either a DB trigger or application-level validation before save.
**Warning signs:** Multiple coaches have `is_default=TRUE`, causing unpredictable routing.

### Pitfall 5: Re-extraction can't run on Vercel

**What goes wrong:** Clicking "Re-extract" button in production triggers a subprocess that either times out or fails silently.
**Why it happens:** Vercel serverless functions have no persistent file system, no background processes, and tight execution limits. The extraction scripts are explicitly CLI-only by architecture decision.
**How to avoid:** Either (a) hide the re-extract button in production (only show in debug mode), or (b) implement it as "copy this CLI command" text rather than a live trigger. The simplest safe approach: show the last extraction timestamp and a copyable CLI command.
**Warning signs:** Flash "extraction started" but profile never updates; Vercel function timeout errors.

## Code Examples

### New Model Functions Needed

The admin UI requires two model functions not yet in `models.py`:

```python
# Add to models.py — get_trait_evidence for ADMN-03
def get_trait_evidence(rider_id, extraction_source=None):
    """Get personality trait evidence for a rider, grouped by trait_name."""
    if extraction_source:
        return _execute(
            """SELECT * FROM personality_trait_evidence
               WHERE rider_id = %s AND extraction_source = %s
               ORDER BY trait_name, created_at DESC""",
            (rider_id, extraction_source)
        ).fetchall()
    return _execute(
        """SELECT * FROM personality_trait_evidence
           WHERE rider_id = %s
           ORDER BY trait_name, created_at DESC""",
        (rider_id,)
    ).fetchall()


# Add to models.py — get_all_guardrails for admin display (includes inactive)
def get_all_guardrails(rule_type=None):
    """Get all non-deleted guardrails including inactive ones (for admin display)."""
    conditions = ["deleted_at IS NULL"]
    params = []
    if rule_type is not None:
        conditions.append("rule_type = %s")
        params.append(rule_type)
    where = " AND ".join(conditions)
    return _execute(
        f"SELECT * FROM coaching_guardrail WHERE {where} ORDER BY rule_type, id",
        tuple(params)
    ).fetchall()
```

### Enum Values (for `<select>` dropdowns)

Sourced directly from migration 011 CHECK constraints:

```python
# Reference for building select options in templates or route context
PERSONALITY_ENUMS = {
    'tone': ['direct', 'warm', 'playful', 'serious', 'sarcastic'],
    'humor_type': ['none', 'dry', 'sarcastic', 'gentle', 'self-deprecating'],
    'directness': ['low', 'medium', 'high'],
    'preferred_formality': ['casual', 'mixed', 'formal'],
    'humor_sensitivity': ['low', 'medium', 'high'],
    'encouragement_style': ['data-driven', 'emotional', 'balanced', 'tough-love'],
    'technical_depth': ['beginner', 'intermediate', 'expert'],
    'response_length_tendency': ['brief', 'moderate', 'verbose'],
    'question_asking_behavior': ['rarely', 'sometimes', 'frequently'],
    'extraction_source': ['whatsapp', 'blog', 'manual', 'merged'],
    'extraction_confidence': ['high', 'medium', 'low'],
}

GEAR_ENUMS = {
    'bike_material': ['aluminum', 'steel', 'titanium', 'carbon', 'other'],
    'value_orientation': ['budget', 'mid-range', 'premium', 'buy-once-buy-right'],
}

GUARDRAIL_ENUMS = {
    'rule_type': ['topic_block', 'tone_limit', 'escalation', 'scope'],
    'applies_to': ['all', 'shriram', 'venki'],
}
```

### Completeness Indicator (ADMN-01, ADMN-04)

```python
# In route handler or passed as template context
COACH_TRAIT_FIELDS = [
    'tone', 'humor_type', 'directness', 'signature_phrases',
    'topic_biases', 'topics_allowed', 'response_length_tendency',
    'question_asking_behavior',
]

def compute_completeness(profile):
    """Returns (filled_count, total_count, confidence_level)."""
    if not profile:
        return 0, len(COACH_TRAIT_FIELDS), None
    filled = sum(1 for f in COACH_TRAIT_FIELDS
                 if profile.get(f) not in (None, [], ''))
    confidence = profile.get('extraction_confidence')
    return filled, len(COACH_TRAIT_FIELDS), confidence
```

### Trait Evidence Display (ADMN-03)

```jinja2
{# In personality_edit.html — show evidence alongside each trait #}
{% for trait_name in ['tone', 'humor_type', 'directness', 'signature_phrases'] %}
<div style="margin-bottom:20px;">
  <label><strong>{{ trait_name }}</strong>
    {% if profile and profile.extraction_confidence == 'low' %}
    <span style="background:#fed7d7;color:#9b2c2c;padding:2px 6px;border-radius:4px;font-size:0.75rem;">LOW</span>
    {% elif profile and profile.extraction_confidence == 'medium' %}
    <span style="background:#fef3c7;color:#78350f;padding:2px 6px;border-radius:4px;font-size:0.75rem;">MED</span>
    {% endif %}
  </label>
  <!-- Evidence quotes -->
  {% set quotes = evidence | selectattr('trait_name', 'equalto', trait_name) | list %}
  {% if quotes %}
  <div style="background:#f7fafc;border-left:3px solid #cbd5e0;padding:8px 12px;margin:4px 0;font-size:0.82rem;color:var(--text-light);">
    {% for q in quotes[:3] %}<p style="margin:2px 0;">"{{ q.source_quote }}"</p>{% endfor %}
  </div>
  {% endif %}
</div>
{% endfor %}
```

### Guardrail CRUD Template Pattern

```html
<!-- In guardrails.html — list all rules with inline toggle -->
{% for rule in guardrails %}
<tr style="{% if not rule.is_active %}opacity:0.5;{% endif %}">
  <td>{{ rule.rule_type }}</td>
  <td>{{ rule.rule_value }}</td>
  <td>{{ rule.applies_to }}</td>
  <td>v{{ rule.rule_version }}</td>
  <td>
    <form method="post" action="{{ url_for('admin.toggle_guardrail', guardrail_id=rule.id) }}" style="display:inline;">
      <button type="submit" class="btn" style="padding:3px 10px;font-size:0.8rem;
        background:{% if rule.is_active %}#38a169{% else %}#718096{% endif %};color:#fff;">
        {{ 'ON' if rule.is_active else 'OFF' }}
      </button>
    </form>
    <a href="{{ url_for('admin.edit_guardrail', guardrail_id=rule.id) }}" style="margin-left:6px;">Edit</a>
    <form method="post" action="{{ url_for('admin.delete_guardrail', guardrail_id=rule.id) }}" style="display:inline;" onsubmit="return confirm('Soft-delete this rule?');">
      <button type="submit" class="btn" style="padding:3px 10px;font-size:0.8rem;background:#e53e3e;color:#fff;margin-left:4px;">Del</button>
    </form>
  </td>
</tr>
{% endfor %}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Coaches hardcoded in prompt | DB-driven routing via `coach_assignment` table | Phase 9 | Admin can now change routing without code deploy |
| Guardrails hardcoded in prompts | `coaching_guardrail` table with `rule_version` trigger | Phase 7 | Admin can toggle rules; evals correlate to versions |
| UNIQUE(rider_id, profile_type) | UNIQUE(rider_id, profile_type, extraction_source) | Phase 8 | Multiple source rows per rider; admin sees merged vs raw |

**Deprecated/outdated:**
- `_BIKE_KEYWORDS` in chat_service.py: replaced by `select_coach_for_message()` in Phase 9. Admin UI manages `coach_assignment` rows, not keywords.
- Hardcoded `applies_to` values ('shriram', 'venki'): these match rider `first_name` lowercase. If coaches are renamed or added, the CHECK constraint on `coaching_guardrail.applies_to` must be updated via migration.

## Open Questions

1. **Re-extraction trigger on production (Vercel)**
   - What we know: Extraction scripts are CLI-only by architecture decision; Vercel has no background processes.
   - What's unclear: Whether ADMN-05 means "UI button that runs extraction" or "UI shows what to run locally."
   - Recommendation: Implement as a "copy CLI command" display with last-extraction timestamp. Do NOT implement as a live subprocess trigger. Document in plan that ADMN-05 is satisfied by showing extraction metadata and copyable CLI command.

2. **Coach roster vs all riders**
   - What we know: There is no "is_coach" flag on the `rider` table. Coaches are identified by having a `personality_profile` with `profile_type='coach'` AND rows in `coach_assignment`.
   - What's unclear: How to display the "add new coach" flow (COACH-01 includes managing the roster).
   - Recommendation: The coach roster page shows all riders who have at least one `coach_assignment` row. "Add coach" means: pick a rider from the dropdown, assign a topic domain, and create a `coach_assignment` row. No new schema needed.

3. **Applies_to values for guardrails are hardcoded**
   - What we know: The CHECK constraint on `coaching_guardrail.applies_to` is `CHECK (applies_to IN ('all', 'shriram', 'venki'))`. Adding a third coach requires a migration.
   - What's unclear: Whether COACH-05 ("Adding a new coach does not require code changes") applies to guardrails as well.
   - Recommendation: For Phase 10, use the existing CHECK constraint values. Document that adding a new coach to guardrail scope requires a future migration to loosen the constraint.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (existing, no new install) |
| Config file | `pytest.ini` at project root |
| Quick run command | `pytest tests/test_coaching_models.py -x -q` |
| Full suite command | `pytest tests/ -x -q` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ADMN-01 | Completeness indicator computed correctly | unit | `pytest tests/test_admin_personality.py::TestCompleteness -x` | Wave 0 |
| ADMN-02 | upsert_personality_profile with extraction_source='manual' saves correctly | integration | `pytest tests/test_coaching_models.py::TestCrudPersonalityProfile -x` | exists |
| ADMN-03 | get_trait_evidence returns quotes grouped by trait_name | integration | `pytest tests/test_admin_personality.py::TestTraitEvidence -x` | Wave 0 |
| ADMN-04 | Confidence badge rendering — LOW/MED/HIGH display | unit | `pytest tests/test_admin_personality.py::TestConfidenceBadge -x` | Wave 0 |
| ADMN-05 | Re-extract button shows copyable CLI command (no subprocess) | unit/smoke | `pytest tests/test_admin_personality.py::TestReExtractDisplay -x` | Wave 0 |
| ADMN-06 | Gear edit page POSTs call upsert_gear_preference correctly | integration | `pytest tests/test_admin_gear.py -x` | Wave 0 |
| GEAR-01 | All gear fields saved and retrieved from gear_preference | integration | `pytest tests/test_coaching_models.py::TestCrudGearPreference -x` | exists |
| GEAR-02 | value_orientation dropdown saves valid enum; rejects invalid | integration | `pytest tests/test_coaching_models.py::TestCrudGearPreference -x` | exists |
| COACH-01 | Coach roster page shows coaches with assignment domains | smoke | `pytest tests/test_admin_coaches.py -x` | Wave 0 |
| GUARD-02 | scope-type guardrail creates and appears in active list | integration | `pytest tests/test_coaching_models.py::TestCrudGuardrail -x` | exists |
| GUARD-03 | tone_limit-type guardrail creates and appears in active list | integration | `pytest tests/test_coaching_models.py::TestCrudGuardrail -x` | exists |
| GUARD-04 | escalation-type guardrail creates and appears in active list | integration | `pytest tests/test_coaching_models.py::TestCrudGuardrail -x` | exists |
| GUARD-05 | toggle_guardrail changes is_active; rule_version increments | integration | `pytest tests/test_coaching_models.py::TestCrudGuardrail::test_crud_guardrail -x` | exists |

### Sampling Rate
- **Per task commit:** `pytest tests/test_coaching_models.py -x -q`
- **Per wave merge:** `pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_admin_personality.py` — covers ADMN-01, ADMN-03, ADMN-04, ADMN-05
- [ ] `tests/test_admin_gear.py` — covers ADMN-06 route POST behavior
- [ ] `tests/test_admin_coaches.py` — covers COACH-01 route display
- New model functions: `get_trait_evidence()` and `get_all_guardrails()` need to be added to models.py before tests can be written.

## Sources

### Primary (HIGH confidence)
- Direct codebase inspection: `routes/admin.py` — existing admin pattern
- Direct codebase inspection: `models.py` lines 2480-2687 — all CRUD functions
- Direct codebase inspection: `migrations/011_personality_coaching_tables.sql` — full schema with CHECK constraints
- Direct codebase inspection: `migrations/012_personality_extraction_fields.sql` — UNIQUE constraint change, evidence table
- Direct codebase inspection: `scripts/personality_helpers.py` — extraction function signatures

### Secondary (MEDIUM confidence)
- Direct codebase inspection: `services/chat_service.py` `select_coach_for_message()` — how coach routing uses DB, confirming `is_default` pattern for fallback coach
- Direct codebase inspection: `templates/admin/mark_status.html` — template pattern (flash messages, inline styles, form POSTs)

### Tertiary (LOW confidence)
- None — all findings sourced from project code directly.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new libraries; existing pattern confirmed from codebase
- Architecture: HIGH — schema is fully defined; model functions exist; route patterns are established
- Pitfalls: HIGH — all pitfalls sourced from actual schema constraints and existing model code
- Test map: MEDIUM — existing test files confirmed; new test files listed as Wave 0 gaps

**Research date:** 2026-03-18
**Valid until:** 2026-04-18 (schema stable; no external dependency drift possible)
