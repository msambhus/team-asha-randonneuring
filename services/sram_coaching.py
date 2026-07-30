"""Conservative coaching metrics derived from private SRAM AXS telemetry.

AXS reports positional gear indexes, not cassette tooth counts. The helpers in
this module intentionally describe positions and sample distributions without
claiming gear ratios, elapsed time in gear, or drivetrain efficiency.
"""
from collections import Counter


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
