"""brevethub/ isolation — BrevetHub must never import Team Asha code.

The whole point of the monorepo split is that BrevetHub shares *data* with Team
Asha only through the `rp_*` tables and shares *code* only through the standalone
`shared/` package. It must import nothing from Team Asha's own `models`, `routes`,
`db`, `config`, or `app`. This scans every `brevethub/` module via AST so it
catches top-level AND in-function imports regardless of style.
"""
import ast
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BREVETHUB_DIR = os.path.join(REPO_ROOT, 'brevethub')

# Team Asha top-level modules BrevetHub must never import. `shared` and
# `brevethub` are explicitly allowed; third-party/stdlib names are ignored.
FORBIDDEN_ROOTS = {'models', 'routes', 'db', 'config', 'app', 'services', 'utils',
                   'cache', 'auth'}


def _brevethub_py_files():
    files = []
    for root, _dirs, names in os.walk(BREVETHUB_DIR):
        if '__pycache__' in root:
            continue
        for n in names:
            if n.endswith('.py'):
                files.append(os.path.join(root, n))
    return files


def _imported_roots(tree):
    """Yield the top-level module name of every import in the AST."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split('.')[0]
        elif isinstance(node, ast.ImportFrom):
            # Ignore relative imports (level > 0) — those stay inside brevethub/.
            if node.level == 0 and node.module:
                yield node.module.split('.')[0]


def test_brevethub_has_python_files():
    assert _brevethub_py_files(), "brevethub/ has no Python modules to check"


def test_brevethub_imports_no_team_asha_module():
    offenders = {}
    for path in _brevethub_py_files():
        with open(path, 'r', encoding='utf-8') as fh:
            tree = ast.parse(fh.read(), filename=path)
        bad = {root for root in _imported_roots(tree) if root in FORBIDDEN_ROOTS}
        if bad:
            offenders[os.path.relpath(path, REPO_ROOT)] = sorted(bad)
    assert not offenders, f"brevethub/ modules import Team Asha code: {offenders}"
