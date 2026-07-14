"""Vercel serverless entry point for BrevetHub.

The BrevetHub Vercel project is rooted at `brevethub/`, but the app imports the
sibling `shared/` package (bundled via `vercel.json` `includeFiles`). To make
both `brevethub.*` and `shared.*` importable at runtime, put the repo root (the
parent of `brevethub/`) on `sys.path`.

BUILD_VERSION: 2026-07-14-v1
"""
import os
import sys

# api/index.py -> brevethub/api -> brevethub -> <repo root>
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from brevethub.app import app  # noqa: E402
