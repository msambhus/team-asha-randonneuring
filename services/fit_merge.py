"""Compatibility shim — `services.fit_merge` now lives in the shared library.

The self-hosted FIT merge engine was extracted to `shared/fit_merge.py` so both
the Team Asha app and BrevetHub can import it. This shim aliases the old import
path to the new module so every existing importer (`routes/tools.py`, the
`test_fit_merge` suite that imports private helpers, …) keeps working unchanged.
"""
import sys
from shared import fit_merge as _fit_merge

sys.modules[__name__] = _fit_merge
