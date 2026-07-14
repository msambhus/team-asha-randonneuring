"""The `brevethub/shared/` copy must stay byte-identical to the canonical
`shared/` package.

Vercel deploys BrevetHub with Root Directory = `brevethub/`, and its Python
bundler only includes files *inside* that root — a sibling `shared/` (or a
symlink/pip-path to one) is unreachable. So `brevethub/shared/` is a committed
copy that ships with the function. This test fails the moment the copy drifts
from the source of truth, so the two can never diverge unnoticed. Update the copy
with:  cp shared/*.py brevethub/shared/
"""
import os

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CANON = os.path.join(_REPO, 'shared')
_VENDOR = os.path.join(_REPO, 'brevethub', 'shared')


def _py_files(d):
    return {f for f in os.listdir(d) if f.endswith('.py')}


def test_vendored_shared_matches_canonical():
    canon, vendor = _py_files(_CANON), _py_files(_VENDOR)
    assert canon == vendor, (
        f"file set differs — canonical {canon} vs vendored {vendor}; "
        f"re-run `cp shared/*.py brevethub/shared/`")
    for name in canon:
        with open(os.path.join(_CANON, name), 'rb') as a, \
                open(os.path.join(_VENDOR, name), 'rb') as b:
            assert a.read() == b.read(), (
                f"brevethub/shared/{name} has drifted from shared/{name}; "
                f"re-run `cp shared/*.py brevethub/shared/`")
