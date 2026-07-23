"""Compatibility shim — `services.plan_match` now lives in the shared library.

Ride↔plan name matching was extracted to `shared/plan_match.py` so both the Team
Asha app and BrevetHub can import it. This shim aliases the old import path to the
new module so every existing importer (`from services.plan_match import ...` and
`from services import plan_match`) keeps working unchanged.
"""
import sys
from shared import plan_match as _plan_match

sys.modules[__name__] = _plan_match
