"""Vercel serverless entry point for BrevetHub.

Works under BOTH Vercel deployment layouts, so the app imports cleanly however
the project's Root Directory is configured:

- **Repo-root present** (Root Directory = `brevethub/` with "Include files outside
  the Root Directory" ON, or Root Directory = repo root): the real `brevethub/`
  package sits under the repo root, so putting the repo root on `sys.path` makes
  `import brevethub.*` (and, for later missions, `import shared.*`) resolve
  normally.
- **Flat** (Root Directory = `brevethub/`, toggle OFF): only this directory's
  *contents* are deployed, so there is no `brevethub/` package to import. We then
  synthesize a `brevethub` package object pointing at this directory, so the app's
  absolute `from brevethub.X import ...` statements keep working without a rewrite.

BUILD_VERSION: 2026-07-14-v2
"""
import os
import sys
import types

# api/index.py -> brevethub/api -> brevethub -> <repo root>
_BREVETHUB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_BREVETHUB_DIR)

# Make both layouts importable: repo root must stay ahead of the BrevetHub
# directory.  `brevethub/app.py` imports the Team Asha factory as ``from app``;
# if the BrevetHub directory is first, that import resolves back to this shell
# and recursively calls ``create_app`` until the Vercel function crashes.
# Iterating in reverse while inserting at index zero preserves the documented
# order in both repo-root and flat Vercel deployments.
for _p in reversed((_REPO_ROOT, _BREVETHUB_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Flat deploy: no importable `brevethub` package on disk — alias this directory as
# the package so `from brevethub.app import app` resolves to the deployed files.
if 'brevethub' not in sys.modules:
    try:
        import brevethub  # noqa: F401
    except ModuleNotFoundError:
        _pkg = types.ModuleType('brevethub')
        _pkg.__path__ = [_BREVETHUB_DIR]
        sys.modules['brevethub'] = _pkg

from brevethub.app import app  # noqa: E402
