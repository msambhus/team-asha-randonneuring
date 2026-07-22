"""The shared rpv2 elevation-profile partial must stay byte-identical across both apps.

The plan-page "Journey" chart and the snapshot mini-chart both render the live gradient
altitude profile via `templates/partials/_elevation_profile.html` (parent app) and
`brevethub/templates/partials/_elevation_profile.html` (BrevetHub) — the SAME file.
BrevetHub's Vercel bundle only ships files under `brevethub/`, so the partial is a
committed copy there; this test fails the moment the two drift. Re-sync with:
  cp templates/partials/_elevation_profile.html brevethub/templates/partials/_elevation_profile.html
"""
import os

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CANON = os.path.join(_REPO, 'templates', 'partials', '_elevation_profile.html')
_VENDOR = os.path.join(_REPO, 'brevethub', 'templates', 'partials', '_elevation_profile.html')


def test_elevation_partial_is_byte_identical():
    with open(_CANON, 'rb') as a, open(_VENDOR, 'rb') as b:
        assert a.read() == b.read(), (
            'brevethub/templates/partials/_elevation_profile.html has drifted from '
            'templates/partials/_elevation_profile.html; re-run '
            '`cp templates/partials/_elevation_profile.html '
            'brevethub/templates/partials/_elevation_profile.html`')


def test_elevation_partial_has_no_team_asha_branding():
    """The BrevetHub copy is scanned by the branding guards; keep the shared source
    free of parent-app identity so both copies pass."""
    with open(_VENDOR, 'r', encoding='utf-8') as fh:
        assert 'team asha' not in fh.read().lower()


def test_elevation_partial_uses_no_parent_only_filters():
    """The partial must use only Jinja builtins (BrevetHub registers no commafy /
    clean_name), so it renders identically in both apps."""
    import re
    region = re.compile(r'{{.*?}}|{%.*?%}', re.DOTALL)
    filt = re.compile(r'\|\s*(\w+)')
    with open(_CANON, 'r', encoding='utf-8') as fh:
        src = fh.read()
    used = set()
    for r in region.findall(src):
        used |= set(filt.findall(r))
    assert 'commafy' not in used and 'clean_name' not in used
