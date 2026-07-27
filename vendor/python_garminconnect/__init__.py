"""Vendored Garmin Connect authentication core.

Source: cyberjunky/python-garminconnect at the revision recorded in PROVENANCE.md.
Only the authentication client and exception types are incorporated; Team Asha's
read-only performance API lives in services/garmin_connect.py.
"""

from .client import Client
from .exceptions import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)

__all__ = [
    "Client",
    "GarminConnectAuthenticationError",
    "GarminConnectConnectionError",
    "GarminConnectNotFoundError",
    "GarminConnectTooManyRequestsError",
]
