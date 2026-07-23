"""Compatibility shim — `services.rusa` now lives in the shared library.

The RUSA results scraper was extracted to `shared/rusa.py` so both the Team Asha
app and BrevetHub can import it. This shim aliases the old import path to the new
module so every existing importer (`models.py`, tests, …) keeps working unchanged.
"""
import sys
from shared import rusa as _rusa

# Make `services.rusa` *be* the shared module: preserves module identity and every
# public/private name so `from services.rusa import X` and `import services.rusa`
# both resolve to the real implementation.
sys.modules[__name__] = _rusa
