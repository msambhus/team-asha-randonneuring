"""Rule-based, presentation-agnostic narratives for rich ride analysis.

This module turns the plan-vs-actual comparison rows (from
``services.strava_analysis.build_comparison``) plus wind/baseline context into
plain-text coaching narratives. It is deliberately pure — it returns plain
dicts / lists / strings and imports no Flask or Jinja — so both the web route
and a future mobile endpoint can consume it.

Three public functions:

* ``compute_gradient_band_baseline`` — the rider's per-segment historical norm,
  binned by gradient, from cached Strava streams. Expensive → the caller caches;
  this function stays pure and side-effect-free.
* ``build_segment_narratives`` — per-segment factual sentences.
* ``build_overall_narrative`` — whole-ride factual observations.

Units are US display convention (mph, ft, rpm, W). km/h → mph via ``* 0.621371``.
"""

import json
import zlib


# ── Gradient bands ──────────────────────────────────────────────────────
# Each sample is binned by its (signed) grade_smooth percentage:
#   descent : grade < -1%
#   flat    : -1% <= grade <= 1%
#   rolling :  1% <  grade <= 4%
#   climb   : grade > 4%
GRADIENT_BANDS = ('descent', 'flat', 'rolling', 'climb')

MS_TO_MPH = 2.23694
KMH_TO_MPH = 0.621371


def _band_for_grade(grade):
    """Return the gradient band name for a signed grade percent, or None."""
    if grade is None:
        return None
    if grade < -1:
        return 'descent'
    if grade <= 1:
        return 'flat'
    if grade <= 4:
        return 'rolling'
    return 'climb'


def _band_for_row_grade(grade_pct):
    """Band used when narrating a *segment*: uses the same thresholds as the
    per-sample binning so segment averages line up with historical bands."""
    return _band_for_grade(grade_pct)


def compute_gradient_band_baseline(rider_id, exclude_ride_id=None, max_rides=15):
    """Compute the rider's per-gradient-band historical norm from cached streams.

    Reads up to ``max_rides`` most-recent finished rides that have cached
    Strava streams (excluding ``exclude_ride_id``), decompresses each stream
    blob, and bins every index-aligned sample by its grade into one of
    ``GRADIENT_BANDS``. Within each band it accumulates mean watts, mean speed
    (m/s → mph), mean cadence and a sample count.

    This is expensive (decompresses + iterates whole streams). It is pure and
    performs no caching — the CALLER is expected to cache the result.

    Args:
        rider_id: DB rider id.
        exclude_ride_id: ride_id to skip (typically the ride being analysed).
        max_rides: cap on how many recent rides to aggregate.

    Returns:
        ``{band: {avg_watts, avg_speed_mph, avg_cadence, n_samples}}`` — bands
        with no data are omitted. Returns ``{}`` when no stream data is usable.
    """
    from models import get_rider_rides_with_cached_streams

    try:
        # Fetch only what this calculation can consume. Add one slot because
        # the current ride may be present and then excluded below.
        rides = get_rider_rides_with_cached_streams(
            rider_id, limit=max_rides + (1 if exclude_ride_id is not None else 0))
    except Exception:
        return {}
    if not rides:
        return {}

    # Accumulators per band: [watts_sum, watts_n, speed_sum, speed_n,
    #                         cadence_sum, cadence_n, n_samples]
    acc = {b: [0.0, 0, 0.0, 0, 0.0, 0, 0] for b in GRADIENT_BANDS}

    used = 0
    for ride in rides:
        if used >= max_rides:
            break
        if exclude_ride_id is not None and ride.get('ride_id') == exclude_ride_id:
            continue

        blob = ride.get('activity_streams')
        if not blob:
            continue

        # Decompress the SAME way services/strava_analysis.py does on read:
        # zlib.decompress(bytes(blob)) then json.loads.
        try:
            streams = json.loads(zlib.decompress(bytes(blob)))
        except Exception:
            continue
        if not isinstance(streams, dict):
            continue

        grade = streams.get('grade_smooth') or []
        watts = streams.get('watts') or []
        velocity = streams.get('velocity_smooth') or []
        cadence = streams.get('cadence') or []

        n = len(grade)
        if n == 0:
            continue

        # Guard against short / mismatched streams. Grade drives the binning; a
        # metric only contributes when its stream is index-aligned with grade.
        watts_ok = len(watts) == n
        velocity_ok = len(velocity) == n
        cadence_ok = len(cadence) == n
        if not (watts_ok or velocity_ok or cadence_ok):
            continue

        used += 1
        for i in range(n):
            band = _band_for_grade(grade[i])
            if band is None:
                continue
            a = acc[band]
            a[6] += 1
            if watts_ok:
                w = watts[i]
                if w is not None and w > 0:
                    a[0] += w
                    a[1] += 1
            if velocity_ok:
                v = velocity[i]
                if v is not None and v > 0:
                    a[2] += v
                    a[3] += 1
            if cadence_ok:
                c = cadence[i]
                if c is not None and c > 0:
                    a[4] += c
                    a[5] += 1

    result = {}
    for band, a in acc.items():
        watts_sum, watts_n, speed_sum, speed_n, cad_sum, cad_n, n_samples = a
        if n_samples == 0:
            continue
        result[band] = {
            'avg_watts': round(watts_sum / watts_n) if watts_n else None,
            'avg_speed_mph': round((speed_sum / speed_n) * MS_TO_MPH, 1) if speed_n else None,
            'avg_cadence': round(cad_sum / cad_n) if cad_n else None,
            'n_samples': n_samples,
        }
    return result


def build_segment_narratives(rows, stop_wind=None, ride_baseline=None,
                             band_baseline=None, same_route_baseline=None):
    """Build a rule-based factual narrative for each planned segment.

    Composes 1-3 short sentences per segment from whatever signals are present.
    Sentences whose inputs are ``None`` are skipped. Only planned rows
    (``is_extra`` falsy) with at least one usable sentence appear in the result.

    Args:
        rows: comparison rows from ``build_comparison`` (see module contract).
        stop_wind: optional ``{location: {wind_speed_mph, wind_type, ...,
            headwind_kmh, crosswind_kmh}}``.
        ride_baseline: optional rider baseline dict (unused per-segment today but
            accepted for parity / future use).
        band_baseline: optional gradient-band baseline from
            ``compute_gradient_band_baseline``.

    Returns:
        ``{location: narrative_str}`` for each segment with >= 1 sentence.
    """
    stop_wind = stop_wind or {}
    band_baseline = band_baseline or {}
    same_route_baseline = same_route_baseline or {}

    narratives = {}
    for row in rows or []:
        if row.get('is_extra'):
            continue
        location = row.get('location')
        if not location:
            continue

        sentences = []

        watts = row.get('actual_avg_watts')
        cadence = row.get('actual_avg_cadence')
        speed = row.get('actual_speed_mph')
        grade = row.get('actual_grade_pct')
        elev = row.get('actual_elev_gain_ft')
        vs_prev = row.get('vs_prev') or {}
        wind = stop_wind.get(location) or {}

        watts_pct = vs_prev.get('watts_pct')
        speed_pct = vs_prev.get('speed_pct')
        cadence_pct = vs_prev.get('cadence_pct')

        is_flat = grade is not None and abs(grade) < 1.5
        is_climb = grade is not None and grade > 3

        # 1) Power vs previous segment.
        if watts is not None and watts_pct is not None and watts_pct != 0:
            direction = 'higher' if watts_pct > 0 else 'lower'
            sentences.append(
                f"You averaged {watts} W here — {abs(watts_pct)}% {direction} "
                f"than the previous segment."
            )

        # 1b) Same-route history: time at this waypoint vs the rider's average on
        # prior rides of the SAME route.
        sr = same_route_baseline.get(location) or {}
        seg_min = row.get('actual_segment_min')
        sr_min = sr.get('avg_segment_min')
        if seg_min is not None and sr_min:
            diff = round(seg_min - sr_min)
            if abs(diff) >= 2:
                slower = 'slower' if diff > 0 else 'faster'
                n = sr.get('n_rides', 0)
                sentences.append(
                    f"You rode this segment in {seg_min} min — about {abs(diff)} min "
                    f"{slower} than your usual {round(sr_min)} min on this route "
                    f"(over {n} prior ride{'s' if n != 1 else ''})."
                )

        # 2) Slow-despite-flat + headwind.
        if (is_flat and speed_pct is not None and speed_pct < 0
                and wind.get('wind_type') == 'headwind'):
            headwind_kmh = wind.get('headwind_kmh')
            if headwind_kmh is not None:
                # Wind comes from the DB (ride_wind_data NUMERIC) as Decimal;
                # coerce so Decimal * float doesn't blow up the whole narrative.
                headwind_mph = round(float(headwind_kmh) * KMH_TO_MPH, 1)
                sentences.append(
                    f"Your speed dropped {abs(speed_pct)}% on flat ground — you "
                    f"were into a {headwind_mph} mph headwind."
                )
            else:
                sentences.append(
                    f"Your speed dropped {abs(speed_pct)}% on flat ground into a "
                    f"headwind."
                )

        # 3) Climb context.
        if is_climb:
            if elev is not None:
                sentences.append(
                    f"This was a climb ({grade}%, {elev} ft of gain)."
                )
            else:
                sentences.append(f"This was a climb ({grade}%).")

            # 3b) Higher power but slower → climbing/headwind explanation.
            if (watts_pct is not None and watts_pct > 0
                    and speed_pct is not None and speed_pct < 0):
                sentences.append(
                    "You put out more power but moved slower — the gradient was "
                    "working against you."
                )

        # 4) Cadence drop.
        if cadence is not None and cadence_pct is not None and cadence_pct <= -8:
            sentences.append(f"Cadence dropped to {cadence} rpm.")

        # 5) vs historical gradient band.
        band = _band_for_row_grade(grade)
        if band and watts is not None:
            band_stats = band_baseline.get(band) or {}
            band_watts = band_stats.get('avg_watts')
            if band_watts:
                diff_pct = round((watts - band_watts) / band_watts * 100)
                band_label = {
                    'descent': 'descents',
                    'flat': 'the flats',
                    'rolling': 'rolling terrain',
                    'climb': 'climbs',
                }[band]
                if diff_pct <= -5:
                    sentences.append(
                        f"That's {abs(diff_pct)}% below your usual power on "
                        f"{band_label}."
                    )
                elif diff_pct >= 5:
                    sentences.append(
                        f"That's {diff_pct}% above your usual power on "
                        f"{band_label}."
                    )

        if sentences:
            # Keep it concise — at most 3 sentences per segment.
            narratives[location] = ' '.join(sentences[:3])

    return narratives


def build_overall_narrative(summary, hr_power=None, ride_baseline=None):
    """Build whole-ride, rule-based observations.

    Args:
        summary: the ``summary`` dict from ``build_comparison``.
        hr_power: optional ``hr_power`` dict from ``build_comparison`` (avg NP, etc.).
        ride_baseline: optional rider baseline dict.

    Returns:
        ``list[str]`` of factual observations. Empty when nothing is meaningful.
    """
    summary = summary or {}
    hr_power = hr_power or {}
    ride_baseline = ride_baseline or {}

    observations = []

    # 1) Pace vs plan.
    speed_delta = summary.get('speed_delta_mph')
    if speed_delta is not None and abs(speed_delta) >= 0.3:
        if speed_delta > 0:
            observations.append(
                f"You averaged {abs(speed_delta)} mph faster than your plan."
            )
        else:
            observations.append(
                f"You averaged {abs(speed_delta)} mph slower than your plan."
            )

    # 2) Pace vs the rider's historical average.
    actual_speed = summary.get('actual_avg_speed_mph')
    baseline_speed = ride_baseline.get('avg_speed_mph')
    if actual_speed is not None and baseline_speed:
        diff = round(actual_speed - baseline_speed, 1)
        if abs(diff) >= 0.5:
            if diff > 0:
                observations.append(
                    f"That's {abs(diff)} mph above your typical long-ride pace "
                    f"({round(baseline_speed, 1)} mph)."
                )
            else:
                observations.append(
                    f"That's {abs(diff)} mph below your typical long-ride pace "
                    f"({round(baseline_speed, 1)} mph)."
                )

    # 3) Total stopped time.
    stopped = summary.get('actual_stopped_time_min')
    if stopped is not None and stopped > 0:
        break_delta = summary.get('break_delta_min')
        if break_delta is not None and break_delta > 15:
            observations.append(
                f"You spent {round(stopped)} min stopped — {round(break_delta)} "
                f"min more than planned."
            )
        elif break_delta is not None and break_delta < -15:
            observations.append(
                f"You spent {round(stopped)} min stopped — {abs(round(break_delta))} "
                f"min less than planned."
            )
        else:
            observations.append(f"You spent {round(stopped)} min stopped in total.")

    # 4) NP / intensity vs baseline.
    np_watts = hr_power.get('weighted_avg_watts')
    baseline_np = ride_baseline.get('avg_np_watts')
    if np_watts and baseline_np:
        diff_pct = round((np_watts - baseline_np) / baseline_np * 100)
        if diff_pct >= 5:
            observations.append(
                f"Your normalized power ({round(np_watts)} W) ran {diff_pct}% "
                f"above your usual — a harder-than-typical effort."
            )
        elif diff_pct <= -5:
            observations.append(
                f"Your normalized power ({round(np_watts)} W) ran {abs(diff_pct)}% "
                f"below your usual — a more conservative effort."
            )

    return observations
