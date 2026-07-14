"""Compatibility shim — `utils.rusa_validator` now lives in the shared library.

The RUSA ID validator was extracted to `shared/rusa_validator.py` so both the
Team Asha app and BrevetHub can import it. This shim aliases the old import path
to the new module so every existing importer (`routes/auth.py`,
`routes/api_auth.py`, …) keeps working unchanged.
"""
import sys
from shared import rusa_validator as _rusa_validator

sys.modules[__name__] = _rusa_validator
