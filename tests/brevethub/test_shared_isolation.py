"""shared/ isolation — the redteam guard.

Every module in `shared/` must be standalone: it may import third-party packages
and the stdlib, but NOTHING from the Team Asha app (`services.*`, `models`,
`routes`, `db`, `config`, `app`) and it must never touch Flask's `current_app`.
Both the Team Asha app and BrevetHub import these modules, so a hidden coupling
would break one of them. This scans via AST so it catches top-level AND
in-function imports regardless of style.
"""
import ast
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHARED_DIR = os.path.join(REPO_ROOT, 'shared')

# Team Asha modules that must never appear in shared/.
FORBIDDEN_ROOTS = {'services', 'models', 'routes', 'db', 'config', 'app', 'utils', 'cache', 'auth'}


def _shared_py_files():
    files = []
    for root, _dirs, names in os.walk(SHARED_DIR):
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
            # Ignore relative imports (level > 0) — those stay inside shared/.
            if node.level == 0 and node.module:
                yield node.module.split('.')[0]


def test_shared_has_python_files():
    assert _shared_py_files(), "shared/ has no Python modules to check"


def test_shared_imports_no_team_asha_module():
    offenders = {}
    for path in _shared_py_files():
        with open(path, 'r', encoding='utf-8') as fh:
            tree = ast.parse(fh.read(), filename=path)
        bad = {root for root in _imported_roots(tree) if root in FORBIDDEN_ROOTS}
        if bad:
            offenders[os.path.relpath(path, REPO_ROOT)] = sorted(bad)
    assert not offenders, f"shared/ modules import Team Asha code: {offenders}"


def test_shared_never_uses_current_app():
    for path in _shared_py_files():
        with open(path, 'r', encoding='utf-8') as fh:
            source = fh.read()
        assert 'current_app' not in source, (
            f"{os.path.relpath(path, REPO_ROOT)} references Flask current_app; "
            "shared/ must stay framework-agnostic"
        )
