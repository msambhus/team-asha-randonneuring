"""Compatibility shim — `services.live_radial` now lives in the shared library.

The Radial live-view builders (the rich per-rider telemetry composition, the
privacy-shaped roster, and the server-computed altitude profile) were promoted to
`shared/live_radial.py` so both the parent web app and BrevetHub import the SAME
engine and it can never fork. This shim aliases the old import path to the new
module — via `sys.modules` so every public AND module-private name is the SAME
object — so `from services.live_radial import ...` and `from services import
live_radial` keep working unchanged.
"""
import sys

from shared import live_radial as _live_radial

sys.modules[__name__] = _live_radial
