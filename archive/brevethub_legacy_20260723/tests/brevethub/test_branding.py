"""Branding — BrevetHub is fully de-branded, zero Team Asha identity.

Two guarantees:
1. Every BrevetHub template and static file is free of Team Asha identity (name,
   club copy). A file scan catches copy that slips in.
2. The rendered landing and login pages show the neutral product name
   "BrevetHub" and never "Team Asha".
"""
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BREVETHUB_DIR = os.path.join(REPO_ROOT, 'brevethub')

# Case-insensitive Team Asha identity markers that must never appear in BrevetHub.
FORBIDDEN = re.compile(r'team\s*asha|teamasha', re.IGNORECASE)


def _asset_files():
    files = []
    for sub in ('templates', 'static'):
        base = os.path.join(BREVETHUB_DIR, sub)
        for root, _dirs, names in os.walk(base):
            if '__pycache__' in root:
                continue
            for n in names:
                if n.endswith(('.html', '.css', '.js')):
                    files.append(os.path.join(root, n))
    return files


def test_no_team_asha_identity_in_assets():
    offenders = {}
    for path in _asset_files():
        with open(path, 'r', encoding='utf-8') as fh:
            if FORBIDDEN.search(fh.read()):
                offenders[os.path.relpath(path, REPO_ROOT)] = 'contains Team Asha identity'
    assert not offenders, f"BrevetHub assets contain Team Asha branding: {offenders}"


def test_landing_renders_neutral_brand(client):
    resp = client.get('/')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'BrevetHub' in body
    assert not FORBIDDEN.search(body)


def test_login_renders_neutral_brand(client):
    resp = client.get('/auth/login')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'BrevetHub' in body
    assert 'Sign in with Google' in body
    assert not FORBIDDEN.search(body)
