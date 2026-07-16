"""Import-safety guard for the /analysis blueprint.

`brevethub.app.create_app()` imports and registers `analysis_bp` at startup, so any
name the blueprint imports from `shared.*` MUST be a real module-level export in BOTH
the canonical `shared/` (used by the repo + Team Asha) and the vendored
`brevethub/shared/` (the ONLY copy the Vercel `brevethub/`-root bundle ships). A
missing export — e.g. `CYCLING_TYPES` — would raise ImportError before any route is
served. This locks that contract so a future edit can't silently drop one.

The AST checks are dependency-free (run in the aidlc container, which has no flask /
requests / psycopg2). The real-import smoke test is skipped there and runs in the
maintainer's local suite, where it proves `create_app()` actually registers the
blueprint (which requires every one of its imports to resolve).
"""
import ast
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Exactly what brevethub/routes/analysis.py imports from the shared package.
_SHARED_STRAVA_NEEDS = {'CYCLING_TYPES', 'fetch_activities', 'fetch_activity_streams'}
_SHARED_ANALYSIS_NEEDS = {'build_map_data', 'detect_stops',
                          '_build_stream_summary', '_compress_streams'}


def _module_level_names(rel_path):
    """The set of names defined at MODULE level (def/class/assignment) in a file."""
    with open(os.path.join(REPO_ROOT, rel_path), 'r', encoding='utf-8') as fh:
        tree = ast.parse(fh.read(), filename=rel_path)
    names = set()
    for node in tree.body:  # top level only — a name nested in a function is NOT exported
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


@pytest.mark.parametrize('shared_dir', ['shared', 'brevethub/shared'])
def test_shared_strava_exports_blueprint_symbols(shared_dir):
    """Both the canonical and the vendored shared.strava export every name the
    analysis blueprint imports (incl. the pre-existing CYCLING_TYPES)."""
    exported = _module_level_names(os.path.join(shared_dir, 'strava.py'))
    missing = _SHARED_STRAVA_NEEDS - exported
    assert not missing, f"{shared_dir}/strava.py is missing exports {sorted(missing)}"


@pytest.mark.parametrize('shared_dir', ['shared', 'brevethub/shared'])
def test_shared_strava_analysis_exports_blueprint_symbols(shared_dir):
    """Both copies of shared.strava_analysis export the engine names the blueprint uses."""
    exported = _module_level_names(os.path.join(shared_dir, 'strava_analysis.py'))
    missing = _SHARED_ANALYSIS_NEEDS - exported
    assert not missing, f"{shared_dir}/strava_analysis.py is missing exports {sorted(missing)}"


def test_analysis_blueprint_imports_and_registers():
    """Real-import smoke test: create_app() registers the analysis blueprint, which
    only succeeds if every shared import the blueprint makes actually resolves.

    Skipped where flask/requests/psycopg2 are absent (the aidlc container); runs in
    the maintainer's local suite as the definitive startup-safety check."""
    pytest.importorskip('flask')
    pytest.importorskip('requests')
    pytest.importorskip('psycopg2')
    from brevethub.app import create_app
    app = create_app()
    assert 'analysis' in app.blueprints, "analysis blueprint failed to register at startup"
