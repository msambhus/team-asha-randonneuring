"""Compatibility shim — `services.live_telemetry` now lives in the shared library.

The pure per-rider live telemetry math (haversine / route projection / plan
delta / next control / required speed / OTL margin / ascent split) was promoted to
`shared/live_telemetry.py` so both the parent web app and BrevetHub import the SAME
engine and it can never fork. This shim aliases the old import path to the new
module — via `sys.modules` so every public AND module-private name is the SAME
object — so every existing importer (`from services.live_telemetry import ...` and
`from services import live_telemetry`) keeps working unchanged.
"""
import sys

from shared import live_telemetry as _live_telemetry

sys.modules[__name__] = _live_telemetry
