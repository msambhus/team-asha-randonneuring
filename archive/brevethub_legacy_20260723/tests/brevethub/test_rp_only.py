"""rp-only — every table BrevetHub's model layer touches is rp_-prefixed.

BrevetHub reads and writes ONLY the `rp_*` tenant tables; it must never read or
write a Team Asha table (rides, members, ride_plans, strava_activity, …). This
scans the SQL string literals in `brevethub/models.py` and fails the build if any
table name that is not `rp_`-prefixed appears after FROM / JOIN / INTO / UPDATE.
"""
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELS_PATH = os.path.join(REPO_ROOT, 'brevethub', 'models.py')

# Grab the identifier that follows a table-introducing SQL keyword.
TABLE_REF = re.compile(
    r'\b(?:FROM|JOIN|INTO|UPDATE)\s+("?[A-Za-z_][A-Za-z0-9_]*"?)',
    re.IGNORECASE,
)


def _sql_string_literals(source):
    """Yield the contents of every single- or double-quoted string literal."""
    for match in re.finditer(r'"([^"]*)"|\'([^\']*)\'', source):
        yield match.group(1) if match.group(1) is not None else match.group(2)


def test_models_reference_only_rp_tables():
    with open(MODELS_PATH, 'r', encoding='utf-8') as fh:
        source = fh.read()

    offenders = set()
    seen_any = False
    for literal in _sql_string_literals(source):
        for ref in TABLE_REF.findall(literal):
            table = ref.strip('"').lower()
            seen_any = True
            if not table.startswith('rp_'):
                offenders.add(table)

    assert seen_any, "no table references found in brevethub/models.py — scan is broken"
    assert not offenders, (
        f"brevethub/models.py references non-rp_ tables: {sorted(offenders)}"
    )
