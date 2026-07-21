"""Pure pace-strategy math shared by Team Asha and BrevetHub.

The Strategies tab offers three ways to ride a brevet — Comfort (+6% slower riding,
long sleep), Standard (the base plan), and Push (−6% faster, short sleep) — each with
recomputed ETAs and a time bank against the ACP cutoff. The computation is pure, so it
is promoted here out of Team Asha's ``routes/riders.py`` and reused by BrevetHub's
read-only Strategies tab; Team Asha keeps byte-identical behavior via a re-export shim
and BrevetHub ships a byte-identical vendored copy under ``brevethub/shared/``.

Isolation contract: stdlib only — no ``services`` / ``models`` / ``routes`` imports and
never the web framework's app/request globals (guarded by test_shared_isolation).
"""

# Pace variants shared between compute_pace_strategies() and the
# /ride-plan/<slug>/v2/strategy POST endpoint. Each entry is
# (factor, sleep_minutes_override_for_night_halt_or_None).
_PACE_VARIANTS = {
    'comfort':  {'factor': 1.06, 'sleep_min': 300, 'name': 'Comfort'},
    'standard': {'factor': 1.0,  'sleep_min': None, 'name': 'Standard'},
    'push':     {'factor': 0.94, 'sleep_min': 90,  'name': 'Push'},
}


def compute_pace_strategies(stops, plan, start_time_str, cutoff_hours,
                            base_stops=None, your_plan_name=None, seg_meta=None):
    """Three pace strategies (comfort/standard/push) with recomputed ETAs.

    Comfort: +6% slower riding, 5h sleep.
    Standard: baseline (existing stops as-is).
    Push: -6% faster riding, 1.5h sleep.

    When `base_stops` is provided (i.e. the rider is viewing their own custom
    plan), the cards are REBASELINED to the custom plan: the middle card is
    "your plan" (the custom baseline), the real team/base plan is shown as a
    card on the correct side (slower→Comfort side, faster→Push side based on
    which direction the custom plan moved), and one derived variant fills the
    opposite side. `your_plan_name` labels the baseline card.

    `seg_meta` (index-aligned with stops) carries per-segment wind/toughness
    from the enriched v2 stops so the cards can flag the tough sections.

    Returns a list of three dicts the v2 template can iterate over.
    """
    # seg_meta is keyed by rounded cumulative distance (route-constant), NOT by
    # list index — so the team card (built from base_stops) and the custom cards
    # line up even when the custom plan hid or added stops.
    seg_meta = seg_meta or {}
    try:
        start_hr, start_min = (int(x) for x in start_time_str.split(':')[:2])
    except (ValueError, AttributeError):
        start_hr, start_min = 6, 0
    start_minutes = start_hr * 60 + start_min

    total_mi = plan.get('total_distance_miles') or 0

    def fmt_eta(arrive_min):
        d, t = divmod(int(arrive_min), 24 * 60)
        hh, mm = divmod(t, 60)
        out = f"{hh:02d}:{mm:02d}"
        return f"{out}+{d}" if d >= 1 else out

    def fmt_bank(bank_min):
        if bank_min is None:
            return ''
        sign = '+' if bank_min >= 0 else '-'
        am = abs(int(bank_min))
        return f"{sign}{am // 60}:{am % 60:02d}"

    def fmt_hm(min_total):
        if min_total is None:
            return '—'
        h, m = divmod(int(min_total), 60)
        return f"{h}:{m:02d}"

    def stop_design_type(s, idx, total):
        if idx == 0:
            return 'start'
        if idx == total - 1:
            return 'finish'
        db_type = (s.get('stop_type') or '').lower().strip()
        if db_type in ('control', 'rest', 'waypoint'):
            return db_type
        loc = s.get('location') or s.get('name') or ''
        if 'control' in loc.lower():
            return 'control'
        if (s.get('stop_duration_min') or 0) >= 15:
            return 'rest'
        return 'waypoint'

    def meta_for(mi):
        return seg_meta.get(round(mi or 0, 1), {})

    def compute_variant(factor, sleep_min_override, src_stops=None):
        src = src_stops if src_stops is not None else stops
        cum = 0
        halt_min_used = 0
        out_stops = []
        last_bank = None  # preserve None when cutoff is missing
        prev_mi = 0.0
        for i, s in enumerate(src):
            seg = int(round((s.get('segment_time_min') or 0) * factor))
            break_m = s.get('stop_duration_min') or 0
            if break_m >= 120:
                break_m = sleep_min_override
                halt_min_used = break_m
            cum += seg
            arrival = cum
            cum += break_m
            mi = s.get('distance_miles') or 0
            if cutoff_hours and total_mi > 0 and mi:
                bookend = round((mi / total_mi) * cutoff_hours * 60)
                bank = bookend - arrival
            else:
                bank = None
            last_bank = bank
            stype = stop_design_type(s, i, len(src))
            # Per-segment signals: distance / climb / implied speed (vary with
            # pace) + wind & toughness (route-constant, from seg_meta by index).
            seg_dist = round(mi - prev_mi, 1)
            prev_mi = mi
            elev = s.get('elevation_gain') or 0
            fpm = int(round(elev / seg_dist)) if elev and seg_dist > 0 else 0
            seg_speed = round(seg_dist / (seg / 60.0), 1) if seg_dist > 0 and seg > 0 else None
            m = meta_for(mi)
            out_stops.append({
                'i': i,
                'type': stype,
                'name': s.get('location') or s.get('name') or '',
                'cumul_mi': round(mi, 1),
                'eta': fmt_eta(start_minutes + arrival),
                'bank': fmt_bank(bank),
                'bank_min': bank if bank is not None else 0,
                'is_key': stype in ('start', 'control', 'finish'),
                'seg_mi': seg_dist,
                'fpm': fpm,
                'seg_speed': seg_speed if seg_speed is not None else 0,
                'seg_speed_known': seg_speed is not None,
                'headwind_mph': m.get('headwind_mph', 0),
                'wind_label': m.get('wind_label', ''),
                'wind_arrow_deg': m.get('wind_arrow_deg', 0),
                'wind_known': m.get('wind_known', False),
                'tough_class': m.get('tough_class', ''),
                'tough_known': m.get('tough_known', False),
            })
        total_elapsed = cum
        return out_stops, total_elapsed, halt_min_used, last_bank

    standard_halt = next(
        (s.get('stop_duration_min') for s in stops if (s.get('stop_duration_min') or 0) >= 120),
        0,
    )

    cutoff_min = int(cutoff_hours * 60) if cutoff_hours else None

    def compute_fitted_variant(factor, sleep_min_override):
        """Compute a variant, trimming sleep (then tightening pace) so the
        total never exceeds the brevet cutoff. Returns the same tuple as
        compute_variant plus the (possibly-reduced) factor actually used.
        """
        stops_out, total, sleep_used, bank = compute_variant(factor, sleep_min_override or 0)
        if cutoff_min is None or total <= cutoff_min:
            return stops_out, total, sleep_used, bank, factor
        # First reduce sleep to absorb the overshoot.
        overshoot = total - cutoff_min
        if (sleep_min_override or 0) > 0:
            trimmed_sleep = max(0, (sleep_min_override or 0) - overshoot)
            stops_out, total, sleep_used, bank = compute_variant(factor, trimmed_sleep)
            if total <= cutoff_min:
                return stops_out, total, sleep_used, bank, factor
        # Still overshooting with zero sleep: tighten the pace factor so the
        # riding portion exactly hits cutoff. ride_min = total - sleep_used;
        # we want ride_min * (target/ride_min) == cutoff_min - sleep_used.
        ride_min = total - sleep_used
        if ride_min > 0:
            scaled_factor = factor * max(0.0, (cutoff_min - sleep_used)) / ride_min
            scaled_factor = max(0.5, scaled_factor)  # don't go absurdly fast
            stops_out, total, sleep_used, bank = compute_variant(scaled_factor, sleep_used)
            return stops_out, total, sleep_used, bank, scaled_factor
        return stops_out, total, sleep_used, bank, factor

    std_stops, std_total, std_sleep, std_bank, _std_f = compute_fitted_variant(
        _PACE_VARIANTS['standard']['factor'], standard_halt or 0)
    com_stops, com_total, com_sleep, com_bank, _com_f = compute_fitted_variant(
        _PACE_VARIANTS['comfort']['factor'], _PACE_VARIANTS['comfort']['sleep_min'])
    psh_stops, psh_total, psh_sleep, psh_bank, _psh_f = compute_fitted_variant(
        _PACE_VARIANTS['push']['factor'], _PACE_VARIANTS['push']['sleep_min'])

    has_halt = bool(standard_halt)

    def bank_is_good(b):
        return b is not None and b >= 0

    # ── Rebaselined cards when viewing a custom plan ──
    # Middle card = the custom plan (baseline). The real team/base plan is a
    # card on the correct side based on which way the custom plan moved, and a
    # derived variant fills the opposite side.
    if base_stops is not None:
        def _halt_of(src):
            return next((s.get('stop_duration_min') for s in src
                         if (s.get('stop_duration_min') or 0) >= 120), 0) or 0

        def _card(stop_set, factor, sleep_override, cid, name, color,
                  summary, recommended, risk):
            cstops, total, sleep_used, bank = compute_variant(
                factor, sleep_override, stop_set)
            has_s = bool(_halt_of(stop_set))
            return {
                'id': cid, 'name': name, 'color': color, 'summary': summary,
                'total': fmt_hm(total),
                'sleep': fmt_hm(sleep_used) if has_s else '',
                'has_sleep': has_s,
                'bank': fmt_bank(bank), 'bank_good': bank_is_good(bank),
                'risk': risk, 'recommended': recommended, 'stops': cstops,
                '_total': total,
            }

        your_card = _card(stops, 1.0, _halt_of(stops), 'yours',
                          your_plan_name or 'Your plan', '#1a365d',
                          'Your saved custom plan', False,
                          'Your own pacing and breaks.')
        team_card = _card(base_stops, 1.0, _halt_of(base_stops), 'team',
                          'Team plan', '#1d4ed8', 'The base team plan', True,
                          'The route owner’s recommended pacing.')
        if your_card['_total'] <= team_card['_total']:
            # Your plan is quicker → team is the slower (Comfort-side) option;
            # the extra card is an even-faster Push of your plan.
            extra = _card(stops, _PACE_VARIANTS['push']['factor'],
                          _PACE_VARIANTS['push']['sleep_min'] or 0, 'push',
                          'Push', '#dc2626', '−6% time · faster than your plan',
                          False, 'High fatigue risk in the final stretch.')
            cards = [team_card, your_card, extra]
        else:
            # Your plan is slower → team is the faster (Push-side) option; the
            # extra card is an even-slower Comfort of your plan.
            extra = _card(stops, _PACE_VARIANTS['comfort']['factor'],
                          _PACE_VARIANTS['comfort']['sleep_min'] or 0, 'comfort',
                          'Comfort', '#16a34a', '+6% margin · slower than your plan',
                          False, 'Comfortable margin — easiest finish.')
            cards = [extra, your_card, team_card]
        for c in cards:
            c.pop('_total', None)
        return cards

    def comfort_risk(b):
        if b is None:
            return 'Comfortable pace — safety buffer.'
        return ('Tight cutoff if conditions sour at the final controls.'
                if b < 60 else 'Comfortable margin — easiest finish.')

    def sleep_summary(actual_min, nominal_min, suffix):
        """Build the per-variant summary text from the *actual* sleep used,
        flagging when we trimmed below the nominal to fit the cutoff."""
        if not has_halt:
            return suffix
        actual_min = int(actual_min or 0)
        h = actual_min // 60
        m = actual_min % 60
        if m == 0:
            label = f'{h} h sleep'
        else:
            label = f'{h}h {m:02d}m sleep'
        if nominal_min and actual_min < nominal_min:
            return f'{label} · trimmed to fit cutoff'
        return f'{label} · {suffix}'

    return [
        {
            'id': 'comfort', 'name': 'Comfort', 'color': '#16a34a',
            'summary': sleep_summary(com_sleep, _PACE_VARIANTS['comfort']['sleep_min'], 'safety margin')
                       if has_halt else '+6% margin · safety buffer',
            'total': fmt_hm(com_total),
            'sleep': fmt_hm(com_sleep) if has_halt else '',
            'has_sleep': has_halt,
            'bank': fmt_bank(com_bank), 'bank_good': bank_is_good(com_bank),
            'risk': comfort_risk(com_bank),
            'recommended': False,
            'stops': com_stops,
        },
        {
            'id': 'standard', 'name': 'Standard', 'color': '#1a365d',
            'summary': sleep_summary(std_sleep, standard_halt, 'team plan')
                       if has_halt else 'Team plan',
            'total': fmt_hm(std_total),
            'sleep': fmt_hm(std_sleep) if has_halt else '',
            'has_sleep': has_halt,
            'bank': fmt_bank(std_bank), 'bank_good': bank_is_good(std_bank),
            'risk': 'Most riders pick this pace.',
            'recommended': True,
            'stops': std_stops,
        },
        {
            'id': 'push', 'name': 'Push', 'color': '#dc2626',
            'summary': sleep_summary(psh_sleep, _PACE_VARIANTS['push']['sleep_min'], 'faster pace')
                       if has_halt else '-6% time · faster pace',
            'total': fmt_hm(psh_total),
            'sleep': fmt_hm(psh_sleep) if has_halt else '',
            'has_sleep': has_halt,
            'bank': fmt_bank(psh_bank), 'bank_good': bank_is_good(psh_bank),
            'risk': 'High fatigue risk in the final stretch.',
            'recommended': False,
            'stops': psh_stops,
        },
    ]
