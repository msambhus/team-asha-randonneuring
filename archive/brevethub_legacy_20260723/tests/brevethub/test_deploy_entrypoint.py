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


def test_entrypoint_bundled_shared_layout(tmp_path):
    """Root Directory = brevethub/ WITH "Include files outside the Root Directory"
    enabled — the supported production layout once BrevetHub imports `shared/`
    (Mission 2 added `from shared.strava import ...`). Vercel then bundles the
    whole repo, so a sibling `shared/` package sits under the repo root next to
    `brevethub/`.

    Simulate exactly that — a repo tree with `brevethub/` and `shared/` and
    nothing else discoverable on `sys.path` — and prove the entry point self-adds
    the repo root so BOTH `brevethub.*` and `shared.*` resolve. (The old
    flat-without-shared layout is intentionally no longer covered: it cannot work
    once `shared/` is a runtime dependency, and this test documents that the
    sibling package must be bundled.)"""
    repo = tmp_path / 'repo'
    ign = shutil.ignore_patterns('__pycache__', '*.pyc')
    shutil.copytree(_BREVETHUB_DIR, repo / 'brevethub', ignore=ign)
    shutil.copytree(os.path.join(_REPO_ROOT, 'shared'), repo / 'shared', ignore=ign)
    entry = repo / 'brevethub' / 'api' / 'index.py'

    # PYTHONPATH is empty of our packages; the entry point must add the repo root
    # itself. Assert the app loads AND that `shared` (the new M2 dep) is importable.
    probe = (
        "import importlib.util;"
        f"spec=importlib.util.spec_from_file_location('e', r'{entry}');"
        "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
        "import shared.strava;"
        "print('WSGI_OK' if callable(m.app) else 'WSGI_BAD')"
    )
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    result = subprocess.run(
        [sys.executable, '-c', probe],
        cwd=str(repo / 'brevethub'), env=env, capture_output=True, text=True)
    assert 'WSGI_OK' in result.stdout, (
        f"bundled-shared entry point failed:\nSTDOUT {result.stdout}\nSTDERR {result.stderr}")
