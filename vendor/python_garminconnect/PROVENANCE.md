# python-garminconnect authentication core

The files `client.py` and `exceptions.py` were copied from
[`cyberjunky/python-garminconnect`](https://github.com/cyberjunky/python-garminconnect)
at commit `e4e9748cf3fa62f997e77171addee3acc333232c` (2026-07-27).

Upstream version at that revision: `0.3.8`.

Copyright © 2020–2026 Ron Klinkien and contributors. Used under the MIT
License, reproduced in `LICENSE`.

Team Asha intentionally did not copy the upstream all-purpose `Garmin` API
wrapper, workout writers, activity mutation endpoints, demos, or exports.
The local `services.garmin_connect` module owns the narrow read-only API surface.

Local adaptation: `Client.export_mfa_state()` and `Client.import_mfa_state()`
serialize only the challenge cookies and routing metadata needed to resume MFA
across Vercel invocations. They deliberately exclude Garmin credentials.
