"""Compatibility shim — `services.fitness` now lives in the shared library.

Fitness scoring was extracted to `shared/fitness.py` so both the Team Asha app
and BrevetHub can import it. This shim aliases the old import path to the new
module so every existing importer keeps working unchanged.
"""
import sys
from shared import fitness as _fitness

sys.modules[__name__] = _fitness
