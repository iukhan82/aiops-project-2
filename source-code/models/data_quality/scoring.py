"""Score data-quality incidents against the injected truth, at incident level (acceptance target FA-02).

An incident is TRUE only if it is the first incident of the right class on the faulty device to open inside the fault window
[onset, end + GRACE_S]. Everything else counts against it, and the report says which kind:

    fragment      a second incident for a fault that already has one (an operator would see two tickets for one problem)
    wrong_class   opened on a device that was faulty at that moment, but for a different class
    spurious      opened on a device with no fault active in the window at all

The FA-02 headline is the STRICT share, (fragment + wrong_class + spurious) / incidents. The overlap share, spurious / incidents, is
reported beside it and is not the headline: it would call any alarm on a faulty device a success.
"""

from __future__ import annotations

import statistics

from models.data_quality.streams import CLASSES, Fault

GRACE_S = 300.0


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def score(
    incidents: list[dict], faults: list[Fault], device_hours: float, only: str | None = None
) -> dict:
    """`only` restricts the score to one class (tuning): other classes' faults and incidents are ignored."""
    faults = [f for f in faults if only in (None, f.fault_class)]
    incidents = [i for i in incidents if only in (None, i["fault_class"])]
    matched: dict[int, dict] = {}
    kinds = {"fragment": 0, "wrong_class": 0, "spurious": 0}
    delays: dict[str, list[float]] = {c: [] for c in CLASSES}
    for incident in sorted(incidents, key=lambda i: i["opened_at"]):
        window = [
            (k, f) for k, f in enumerate(faults) if f.device_id == incident["device_id"] and f.onset - 1.0 <= incident["opened_at"] <= f.end + GRACE_S
        ]  # fmt: skip
        same = [(k, f) for k, f in window if f.fault_class == incident["fault_class"]]
        if same:
            k, f = same[0]
            if k in matched:
                kinds["fragment"] += 1
            else:
                matched[k] = incident
                delays[f.fault_class].append(incident["opened_at"] - f.onset)
        elif window:
            kinds["wrong_class"] += 1
        else:
            kinds["spurious"] += 1
    total = len(incidents)
    per_class = {}
    for c in CLASSES:
        cls_faults = [k for k, f in enumerate(faults) if f.fault_class == c]
        detected = [k for k in cls_faults if k in matched]
        per_class[c] = {
            "faults": len(cls_faults), "detected": len(detected),
            "median_delay_s": statistics.median(delays[c]) if delays[c] else None,
            "p95_delay_s": _percentile(delays[c], 0.95),
        }  # fmt: skip
    false_count = sum(kinds.values())
    return {
        "incidents": total,
        "true": len(matched),
        **kinds,
        "strict_false_share": false_count / total if total else 0.0,
        "overlap_false_share": kinds["spurious"] / total if total else 0.0,
        "spurious_per_clean_device_hour": kinds["spurious"] / device_hours if device_hours else 0.0,
        "faults": len(faults),
        "recall": len(matched) / len(faults) if faults else None,
        "per_class": per_class,
        "matched_faults": sorted(matched),
    }


def clean_device_hours(
    faults: list[Fault], devices: int, steps: int, cadence_s: float, calibration_steps: int
) -> float:
    """Device-hours in which no fault (or its grace) is active: what the spurious count is a rate over."""
    total = devices * (steps - calibration_steps) * cadence_s
    busy = sum(f.end - f.onset + GRACE_S for f in faults)
    return max(total - busy, 0.0) / 3600.0
