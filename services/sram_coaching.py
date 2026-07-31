"""Conservative coaching metrics derived from private SRAM AXS telemetry.

AXS reports positional gear indexes, not cassette tooth counts. The helpers in
this module intentionally describe positions and sample distributions without
claiming gear ratios, elapsed time in gear, or drivetrain efficiency.
"""
from collections import Counter
from bisect import bisect_left


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _position_samples(component, key):
    values = component.get(key) or []
    timestamps = component.get("timestamps") or []
    samples = []
    for index, value in enumerate(values):
        numeric = _number(value)
        if numeric is None or numeric <= 0:
            continue
        timestamp = timestamps[index] if index < len(timestamps) else index
        samples.append((timestamp, int(numeric)))
    return samples


def _distribution(samples):
    counts = Counter(position for _, position in samples)
    total = sum(counts.values())
    if not total:
        return []
    return [
        {
            "position": position,
            "samples": count,
            "sample_percentage": round(count * 100.0 / total, 1),
        }
        for position, count in sorted(counts.items())
    ]


def _dominant_position(samples):
    distribution = _distribution(samples)
    if not distribution:
        return None
    return max(
        distribution,
        key=lambda row: (row["samples"], -row["position"]),
    )["position"]


def derive_sram_coaching_metrics(activity):
    """Return compact, display-safe aggregates without raw telemetry."""
    if not activity:
        return None

    components = activity.get("components") or []
    drivetrain = next((
        row for row in components
        if row.get("ant_component_id") == 2
        or row.get("device_type") == 34
    ), {})
    gear = activity.get("gear_summary") or {}

    rear_samples = _position_samples(drivetrain, "rear_gears")
    front_samples = _position_samples(drivetrain, "front_gears")
    rear_distribution = _distribution(rear_samples)
    front_distribution = _distribution(front_samples)

    rear_shifts = _number(
        gear.get("rear_shift_count")
        if gear.get("rear_shift_count") is not None
        else activity.get("rear_shift_count"))
    front_shifts = _number(
        gear.get("front_shift_count")
        if gear.get("front_shift_count") is not None
        else activity.get("front_shift_count"))
    duration_s = _number(activity.get("duration_s"))
    total_shifts = (
        (rear_shifts or 0) + (front_shifts or 0)
        if rear_shifts is not None or front_shifts is not None
        else None
    )
    shifts_per_hour = (
        round(total_shifts / (duration_s / 3600.0), 1)
        if total_shifts is not None and duration_s and duration_s > 0
        else None
    )

    split = len(rear_samples) // 2
    first_dominant = _dominant_position(rear_samples[:split])
    second_dominant = _dominant_position(rear_samples[split:])
    dominant_rear = (
        _dominant_position(rear_samples)
        or gear.get("most_used_rear_index"))
    dominant_front = (
        _dominant_position(front_samples)
        or gear.get("most_used_front_index"))

    result = {
        "total_shifts": int(total_shifts) if total_shifts is not None else None,
        "shifts_per_hour": shifts_per_hour,
        "rear_shift_count": (
            int(rear_shifts) if rear_shifts is not None else None),
        "front_shift_count": (
            int(front_shifts) if front_shifts is not None else None),
        "dominant_rear_position": dominant_rear,
        "dominant_front_position": dominant_front,
        "rear_positions_used": len(rear_distribution) or None,
        "front_positions_used": len(front_distribution) or None,
        "rear_distribution": rear_distribution,
        "front_distribution": front_distribution,
        "first_half_dominant_rear_position": first_dominant,
        "second_half_dominant_rear_position": second_dominant,
        "rear_position_changed_late": (
            first_dominant is not None
            and second_dominant is not None
            and first_dominant != second_dominant
        ),
        "battery_status": drivetrain.get("battery_status"),
        "battery_voltage": drivetrain.get("voltage"),
        "distribution_basis": (
            "position samples" if rear_distribution or front_distribution
            else None),
        "insights": [],
    }

    if shifts_per_hour is not None:
        result["insights"].append(
            f"Drivetrain shifting averaged {shifts_per_hour:g} shifts per "
            "elapsed hour.")
    if result["rear_position_changed_late"]:
        result["insights"].append(
            "The dominant rear gear position changed from position "
            f"{first_dominant} in the first half to position "
            f"{second_dominant} in the second half.")
    if rear_distribution:
        dominant_row = max(
            rear_distribution, key=lambda row: row["samples"])
        result["insights"].append(
            f"Rear position {dominant_row['position']} represented "
            f"{dominant_row['sample_percentage']:g}% of recorded gear "
            "position samples.")
    return result


def _elapsed_seconds(timestamps, started_at=None):
    values = [_number(value) for value in (timestamps or [])]
    if not values or any(value is None for value in values):
        return []
    # AXS may return epoch milliseconds, epoch seconds, or elapsed samples.
    if values[0] > 1_000_000_000_000:
        values = [value / 1000.0 for value in values]
    if values[0] > 1_000_000_000:
        try:
            base = float(started_at.timestamp())
        except (AttributeError, TypeError, ValueError):
            base = values[0]
    else:
        base = values[0]
    elapsed = [max(0.0, value - base) for value in values]
    # A mismatched activity timestamp can produce a large offset. Relative AXS
    # sample time is still safe and useful for within-ride alignment.
    if elapsed and elapsed[0] > 300:
        origin = elapsed[0]
        elapsed = [max(0.0, value - origin) for value in elapsed]
    return elapsed


def _distance_at_time(seconds, stream_times, stream_distances):
    if not stream_times or not stream_distances:
        return None
    index = bisect_left(stream_times, seconds)
    if index <= 0:
        return float(stream_distances[0])
    if index >= len(stream_times):
        return float(stream_distances[-1])
    t0, t1 = float(stream_times[index - 1]), float(stream_times[index])
    d0, d1 = float(stream_distances[index - 1]), float(stream_distances[index])
    fraction = (seconds - t0) / (t1 - t0) if t1 > t0 else 0
    return d0 + fraction * (d1 - d0)


def _gear_samples(activity, streams, component, key):
    values = component.get(key) or []
    elapsed = _elapsed_seconds(
        component.get("timestamps"), activity.get("started_at"))
    stream_times = streams.get("time") or []
    stream_distances = streams.get("distance") or []
    if (min(len(values), len(elapsed)) < 2
            or min(len(stream_times), len(stream_distances)) < 2):
        return []
    samples = []
    for index in range(min(len(values), len(elapsed))):
        position = _number(values[index])
        distance_m = _distance_at_time(
            elapsed[index], stream_times, stream_distances)
        if position is None or position <= 0 or distance_m is None:
            continue
        samples.append((distance_m / 1609.344, int(position)))
    return samples


def _segment_gears(samples, start_mi, end_mi):
    positions = [
        position for distance, position in samples
        if start_mi <= distance <= end_mi]
    if not positions:
        return None
    changes = sum(
        1 for previous, current in zip(positions, positions[1:])
        if previous != current)
    counts = Counter(positions)
    dominant = max(counts, key=lambda value: (counts[value], -value))
    return {
        "start_position": positions[0],
        "end_position": positions[-1],
        "dominant_position": dominant,
        "positions_used": len(counts),
        "shift_count": changes,
    }


def derive_sram_segment_metrics(activity, streams, comparison_rows):
    """Align AXS position samples to Strava distance and summarize each leg."""
    if not activity or not streams or not comparison_rows:
        return {}
    component = next((
        row for row in (activity.get("components") or [])
        if row.get("ant_component_id") == 2
        or row.get("device_type") == 34
    ), None)
    if not component:
        return {}
    rear_samples = _gear_samples(activity, streams, component, "rear_gears")
    front_samples = _gear_samples(activity, streams, component, "front_gears")
    if not rear_samples and not front_samples:
        return {}

    result = {}
    previous_mi = 0.0
    planned_rows = sorted(
        (row for row in comparison_rows if not row.get("is_extra")),
        key=lambda row: float(row.get("distance_miles") or 0),
    )
    for row in planned_rows:
        end_mi = float(row.get("distance_miles") or 0)
        if end_mi <= previous_mi:
            previous_mi = end_mi
            continue
        rear = _segment_gears(rear_samples, previous_mi, end_mi)
        front = _segment_gears(front_samples, previous_mi, end_mi)
        if not rear and not front:
            previous_mi = end_mi
            continue
        distance_mi = end_mi - previous_mi
        shifts = (rear or {}).get("shift_count", 0) + (
            front or {}).get("shift_count", 0)
        cadence = _number(row.get("actual_avg_cadence"))
        climb_rate = _number(row.get("actual_climb_ft_per_mi"))
        advice = []
        if cadence is not None and cadence < 65 and (
                climb_rate is None or climb_rate >= 40):
            advice.append(
                f"Average cadence was {cadence:g} rpm while this segment used "
                "the recorded drivetrain positions. Review whether a lower "
                "gear range or easier pacing would reduce grinding here.")
        shift_rate = shifts / distance_mi if distance_mi > 0 else None
        if shifts >= 10 and shift_rate is not None and shift_rate >= 2:
            advice.append(
                f"{shifts} recorded position changes "
                f"({shift_rate:.1f}/mi) suggest frequent gear searching; "
                "shift earlier and settle into a sustainable cadence.")
        if not advice and cadence is not None and 70 <= cadence <= 95:
            advice.append(
                f"Cadence averaged {cadence:g} rpm with no clear drivetrain "
                "mismatch visible from the available positional data.")
        result[row.get("location") or str(end_mi)] = {
            "start_mi": round(previous_mi, 1),
            "end_mi": round(end_mi, 1),
            "rear": rear,
            "front": front,
            "total_shifts": shifts,
            "shifts_per_mile": (
                round(shift_rate, 1) if shift_rate is not None else None),
            "average_cadence": cadence,
            "advice": advice,
        }
        previous_mi = end_mi
    return result
