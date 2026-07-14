"""Deployability guard for the Vercel serverless entry point.

The entry point (`brevethub/api/index.py`) must yield the WSGI app under BOTH
Vercel Root-Directory layouts:

- **repo-root present** — the real `brevethub/` package is importable;
- **flat** — Root Directory = `brevethub/` with only its *contents* deployed, so
  there is no `brevethub/` package on disk.

The flat case is the one that silently breaks a `brevethub`-rooted Vercel project,
so we simulate it here by copying the app into a throwaway directory that has no
importable `brevethub` package on `sys.path`.
"""
import importlib.util
import os
import shutil
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BREVETHUB_DIR = os.path.join(_REPO_ROOT, 'brevethub')


def _load_entrypoint(path):
    spec = importlib.util.spec_from_file_location('bh_entry_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_entrypoint_repo_root_layout():
    """With the repo checked out (repo root reachable), the entry point loads."""
    module = _load_entrypoint(os.path.join(_BREVETHUB_DIR, 'api', 'index.py'))
    assert callable(module.app), "entry point did not expose a callable WSGI app"


def test_entrypoint_flat_layout(tmp_path):
    """Root Directory = brevethub/ (flat): only brevethub's contents are deployed,
    with no importable `brevethub` package. Run the entry point in a fresh Python
    process whose sys.path cannot see the real package, proving it self-heals."""
    deploy_root = tmp_path / 'deploy_root'
    shutil.copytree(_BREVETHUB_DIR, deploy_root,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    entry = deploy_root / 'api' / 'index.py'

    # A subprocess with cwd inside the deploy root and PYTHONPATH scoped to it —
    # the real repo `brevethub` package is not importable here.
    probe = (
        "import importlib.util;"
        f"spec=importlib.util.spec_from_file_location('e', r'{entry}');"
        "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
        "print('WSGI_OK' if callable(m.app) else 'WSGI_BAD')"
    )
    env = dict(os.environ, PYTHONPATH=str(deploy_root))
    result = subprocess.run(
        [sys.executable, '-c', probe],
        cwd=str(deploy_root), env=env, capture_output=True, text=True)
    assert 'WSGI_OK' in result.stdout, (
        f"flat-layout entry point failed:\nSTDOUT {result.stdout}\nSTDERR {result.stderr}")
