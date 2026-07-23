"""BrevetHub club directory API.

`GET /api/clubs` returns the seeded `rp_club` directory the signup picker reads
from. Public (no auth) so the signup page can populate its `<select>` and any
future client can list clubs. Read-only; clubs are seeded by migration 033.
"""
from flask import Blueprint, jsonify

from brevethub import models

clubs_bp = Blueprint('clubs', __name__)


@clubs_bp.route('/api/clubs')
def list_clubs():
    clubs = models.get_all_clubs()
    return jsonify([
        {
            'id': c['id'],
            'name': c['name'],
            'city': c['city'],
            'state': c['state'],
            'rusa_club_id': c['rusa_club_id'],
        }
        for c in clubs
    ])
