# Local Development Workflow

## Pre-PR Checklist (Mandatory)

Every PR must follow this sequence:

1. **Run full test suite:** `python3 -m pytest tests/ -x -q` — all tests must pass
2. **Claude subagent code review:** Before creating any PR, run a `Task` subagent with `subagent_type=general-purpose` to review all changed files. The review must check:
   - Security issues (SQL injection, XSS, secrets exposure)
   - Test coverage for new code paths
   - Convention violations (see project CLAUDE.md)
   - Error handling completeness
   - Breaking changes to existing functionality
3. **Merge from main:** `git merge main` to ensure no conflicts
4. **Create Linear ticket:** Before merging, create a Linear ticket describing the work. Assign to the implementer (Mihir Sambhus). Link the ticket in the PR body.
5. **Create PR:** Use `gh pr create` with summary, test plan, and Linear ticket links
6. **Never merge directly to main** without a PR, even for "small" changes

## Git Best Practices

### Branching
- Always branch from `main`: `git checkout -b feature/descriptive-name main`
- One feature per branch. Don't mix unrelated changes.
- Keep branches short-lived. Merge and delete promptly.

### Commits
- Format: `type: description` where type is `feat`, `fix`, `docs`, `chore`, `test`, `refactor`
- Imperative mood: "add weather service" not "added weather service"
- One logical change per commit. Don't squash unrelated work.
- Never commit `.env`, credentials, or API keys. Check `.gitignore` first.
- Include `Co-Authored-By` when Claude Code generates the commit.

### PR Creation
- Title: Under 70 characters, type-prefixed (`feat:`, `fix:`)
- Body: Summary bullets, test plan checklist, Linear ticket links
- Always link to relevant Linear tickets (TA-XX)

### Dangerous Operations (Never Do Without Explicit User Approval)
- `git push --force` (especially to main)
- `git reset --hard`
- `git branch -D` on branches with unmerged work
- `git checkout .` or `git restore .` (discards all changes)

## Running the App Locally

```bash
# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Set up environment
cp .env.example .env  # Then fill in values

# Run Flask dev server
python3 app.py

# Build Tailwind CSS (separate terminal)
npm run build:css
```

## Testing Practices

### Running Tests
```bash
python3 -m pytest tests/ -x -q          # Full suite, stop on first failure
python3 -m pytest tests/test_weather.py  # Single file
python3 -m pytest tests/ -k "weather"    # By keyword
```

### Writing Tests
- Place in `tests/` directory, file named `test_<module>.py`
- Use `conftest.py` fixtures: `app`, `client`, `db_conn`
- Mock external APIs (OpenAI, Strava, RWGPS, Open-Meteo) — never make real API calls in tests
- Test error paths, not just happy paths
- Use `**kwargs` in mock helper functions to be forward-compatible
- Assert specific values, not just "truthy" results

### Test Patterns
```python
# Mock OpenAI client
with patch('services.chat_service._get_client') as mock_client:
    mock_client.return_value.chat.completions.create.return_value = mock_response
    result = function_under_test()

# DB test with auto-rollback
def test_query(db_conn):
    cur = db_conn.cursor()
    cur.execute("INSERT INTO ...")
    # Auto-rolled back after test
```

## Architecture Guidelines

### Adding a New Service
1. Create `services/new_service.py` with pure functions (no class instances)
2. Add tests in `tests/test_new_service.py`
3. Import in the route or chat pipeline where needed
4. If it's a chat tool, add to `ALLOWED_QUERIES` in `chat_tools.py`

### Adding a New Chat Intent
1. Add intent name to `IntentResult` Literal type in `chat_service.py`
2. Update `INTENT_CLASSIFICATION_PROMPT` with description and disambiguation
3. Add handler branch in `run_agent_loop()`
4. Add tests for intent classification and execution
5. Update `test_agent_pipeline.py` intent literal validation

### Adding a New SQL Query for Chat
1. Add named query to `ALLOWED_QUERIES` dict in `chat_tools.py`
2. Use `%s` parameterized placeholders only
3. Update test that validates ALLOWED_QUERIES count
4. Never expose raw SQL to the LLM

### Adding a New API Integration
1. Create service module in `services/`
2. Use `requests` library with timeout parameter
3. Handle all error codes gracefully (4xx, 5xx, timeout)
4. Add caching if appropriate (Flask-Caching memoize)
5. Mock all HTTP calls in tests

## Code Review Checklist (For Subagent Reviews)

When reviewing code before a PR, check:

- [ ] All new functions have tests
- [ ] No raw SQL strings outside `models.py` or `ALLOWED_QUERIES`
- [ ] No hardcoded secrets or API keys
- [ ] Error handling on all external API calls
- [ ] Cache invalidation after write operations
- [ ] `RideStatus` enum used instead of raw strings
- [ ] No `print()` statements left in production code (use `app.logger`)
- [ ] Template filters used for display formatting
- [ ] Parameterized SQL queries (no string formatting)
- [ ] Mock helpers accept `**kwargs`
- [ ] Max tokens enforced on LLM calls (<=800)
- [ ] Imports are at module level, not inside functions (unless circular)

## Environment Variables

Required for local development:
- `DATABASE_URL` — Supabase PostgreSQL connection string (port 6543)
- `SECRET_KEY` — Flask session secret
- `OPENAI_API_KEY` — For chat features (optional, falls back to rule-based)
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` — OAuth login
- `STRAVA_CLIENT_ID` / `STRAVA_CLIENT_SECRET` — Strava integration
- `RWGPS_API_KEY` / `RWGPS_AUTH_TOKEN` — RideWithGPS route data

Optional:
- `BRAINTRUST_API_KEY` — Observability (graceful degradation if missing)
- `CRON_SECRET` — For cron endpoint auth
- `LINEAR_API_KEY` — Project management API
