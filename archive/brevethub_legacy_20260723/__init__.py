"""BrevetHub — a club-agnostic randonneuring web app.

A standalone Flask application that lives in this monorepo alongside the Team
Asha app but shares no data with it: BrevetHub reads and writes only the `rp_*`
tenant tables and imports club-agnostic logic from the sibling `shared/` package.
It never imports Team Asha's `models`, `routes`, `db`, `config`, or `app`.
"""
