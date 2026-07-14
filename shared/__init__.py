"""BrevetHub shared library — club-agnostic services usable by any app in this
monorepo.

Every module here is intentionally **standalone**: it imports nothing from the
Team Asha app (`services.*`, `models`, `routes`, `db`, `config`, `app`) and never
touches Flask's `current_app`. That isolation is a hard contract — both the Team
Asha web app and the new BrevetHub app import these modules, so a hidden coupling
would break one of them. `tests/brevethub/test_shared_isolation.py` enforces it.
"""
