"""Shared evaluation for edge blockage detectors (baselines and models).

Three lenses, because row-level scores alone mislead on rare-event data:

- row level: precision/recall/F1/FPR over (device, 30 s interval) rows;
- episode level: an *incident* is detected if any alarm fires during its
  ground-truth window (with detection delay from the measured onset); a
  *false-alarm episode* is a contiguous run of alarmed rows that never
  overlaps a ground-truth positive row;
- condition slices: demand scale and blockage distance, always with sample
  sizes, so nobody reads an aggregate number as universal performance.

Confidence intervals are cluster bootstraps over SUMO runs (rows within a
run are strongly correlated, so resampling rows would understate variance).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

INTERVAL_S = 30.0
# Operating constraint shared by every detector we compare: at most 10% of raised
# alarm episodes may be false (edge-level proxy for ACCEPTANCE_TARGETS FA-01).
MAX_FALSE_ALARM_EPISODE_SHARE = 0.10
ANCHOR = datetime(2026, 9, 18, 9, 0, 0, tzinfo=timezone.utc)


def _seconds(interval_end: str) -> float:
    return (datetime.fromisoformat(interval_end.replace("Z", "+00:00")) - ANCHOR).total_seconds()


def confusion(y: np.ndarray, pred: np.ndarray) -> dict[str, int]:
    y = y.astype(bool)
    pred = pred.astype(bool)
    return {
        "tp": int((y & pred).sum()),
        "fp": int((~y & pred).sum()),
        "fn": int((y & ~pred).sum()),
        "tn": int((~y & ~pred).sum()),
    }


def prf(c: dict[str, int]) -> dict[str, float]:
    tp, fp, fn, tn = c["tp"], c["fp"], c["fn"], c["tn"]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "fpr": fpr}


def row_metrics(y: np.ndarray, pred: np.ndarray) -> dict:
    c = confusion(y, pred)
    return {**c, **prf(c), "n": int(len(y)), "positives": int(y.sum())}


def streams_of(meta: list[dict]) -> dict[tuple[str, str], list[int]]:
    """Public alias so callers can precompute streams once for repeated scoring."""
    return _streams(meta)


def _streams(meta: list[dict]) -> dict[tuple[str, str], list[int]]:
    streams: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, m in enumerate(meta):
        streams[(m["run_id"], m["device_id"])].append(index)
    for indices in streams.values():
        indices.sort(key=lambda i: meta[i]["interval_end"])
    return streams


def episode_metrics(
    meta: list[dict],
    y: np.ndarray,
    pred: np.ndarray,
    incidents: dict[str, dict],
    streams: dict[tuple[str, str], list[int]] | None = None,
) -> dict:
    """Incident detection rate/delay and false-alarm episodes per device-hour."""
    positives_by_incident: dict[str, list[int]] = defaultdict(list)
    for index, m in enumerate(meta):
        if y[index] == 1 and m["incident_id"]:
            positives_by_incident[m["incident_id"]].append(index)

    detected, delays, missed = 0, [], []
    for incident_id, indices in positives_by_incident.items():
        alarmed = [i for i in indices if pred[i]]
        if alarmed:
            detected += 1
            first = min(alarmed, key=lambda i: meta[i]["interval_end"])
            delays.append(_seconds(meta[first]["interval_end"]) - incidents[incident_id]["onset_s"])
        else:
            missed.append(incident_id)

    false_episodes = []
    true_episodes = 0
    negative_rows = 0
    for (run_id, device_id), indices in (streams or _streams(meta)).items():
        current: list[int] = []
        for i in indices + [None]:
            if i is not None and pred[i]:
                current.append(i)
                continue
            if current:
                if any(y[j] for j in current):
                    true_episodes += 1
                else:
                    false_episodes.append((run_id, device_id, current))
                current = []
        negative_rows += sum(1 for i in indices if not y[i])

    hours = negative_rows * INTERVAL_S / 3600.0
    return {
        "incidents": len(positives_by_incident),
        "incidents_detected": detected,
        "incident_recall": detected / len(positives_by_incident) if positives_by_incident else 0.0,
        "median_detection_delay_s": float(np.median(delays)) if delays else None,
        "p90_detection_delay_s": float(np.percentile(delays, 90)) if delays else None,
        "false_alarm_episodes": len(false_episodes),
        "true_alarm_episodes": true_episodes,
        "false_alarm_episode_share": (
            len(false_episodes) / (len(false_episodes) + true_episodes)
            if false_episodes or true_episodes
            else 0.0
        ),
        "false_alarm_episodes_per_device_hour": len(false_episodes) / hours if hours else 0.0,
        "missed_incident_ids": sorted(missed),
        "_false_episode_rows": false_episodes,
    }


def cluster_bootstrap(
    meta: list[dict], y: np.ndarray, pred: np.ndarray, samples: int = 400, seed: int = 20260918
) -> dict[str, list[float]]:
    """95% percentile intervals for precision/recall/F1 by resampling runs."""
    per_run: dict[str, np.ndarray] = {}
    for run_id in sorted({m["run_id"] for m in meta}):
        idx = np.array([i for i, m in enumerate(meta) if m["run_id"] == run_id])
        c = confusion(y[idx], pred[idx])
        per_run[run_id] = np.array([c["tp"], c["fp"], c["fn"], c["tn"]])
    runs = list(per_run)
    rng = np.random.default_rng(seed)
    results = {"precision": [], "recall": [], "f1": []}
    for _ in range(samples):
        chosen = rng.integers(0, len(runs), len(runs))
        tp, fp, fn, tn = sum(per_run[runs[k]] for k in chosen)
        m = prf({"tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn)})
        for key in results:
            results[key].append(m[key])
    return {
        key: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))]
        for key, v in results.items()
    }


def sliced(
    meta: list[dict], y: np.ndarray, pred: np.ndarray, key: str, positives_only: bool = False
) -> list[dict]:
    """Metrics per value of a meta field (e.g. demand_scale), with sample sizes."""
    values = sorted({m[key] for m in meta if m[key] is not None})
    rows = []
    for value in values:
        idx = np.array([i for i, m in enumerate(meta) if m[key] == value])
        if positives_only:
            idx = np.array([i for i in idx if y[i] == 1])
        if not len(idx):
            continue
        entry = {"value": value, **row_metrics(y[idx], pred[idx])}
        rows.append(entry)
    return rows


def full_report(
    meta: list[dict], y: np.ndarray, pred: np.ndarray, incidents: dict[str, dict]
) -> dict:
    episodes = episode_metrics(meta, y, pred, incidents)
    episodes.pop("_false_episode_rows")
    return {
        "rows": row_metrics(y, pred),
        "rows_ci95": cluster_bootstrap(meta, y, pred),
        "episodes": episodes,
        "by_demand_scale": sliced(meta, y, pred, "demand_scale"),
        "recall_by_stall_distance_m": sliced(meta, y, pred, "stall_distance_from_loop_m"),
    }


def failure_cases(
    meta: list[dict],
    X: np.ndarray,
    names: tuple[str, ...],
    y: np.ndarray,
    pred: np.ndarray,
    incidents: dict[str, dict],
    limit: int = 8,
) -> dict:
    """Concrete misses and false alarms, worst first, with the evidence."""
    ix = {n: i for i, n in enumerate(names)}
    episodes = episode_metrics(meta, y, pred, incidents)
    missed = []
    for incident_id in episodes["missed_incident_ids"]:
        rows = [i for i, m in enumerate(meta) if m["incident_id"] == incident_id and y[i]]
        first = meta[rows[0]]
        missed.append(
            {
                "incident_id": incident_id,
                "run_spec": first["run_spec"],
                "demand_scale": first["demand_scale"],
                "stall_distance_from_loop_m": first["stall_distance_from_loop_m"],
                "positive_intervals": len(rows),
                "max_occ_during_incident": float(max(X[i, ix["occ_last"]] for i in rows)),
                "min_speed_during_incident": float(min(X[i, ix["speed_min"]] for i in rows)),
            }
        )
    missed.sort(key=lambda r: (-r["positive_intervals"], r["incident_id"]))

    false_alarms = []
    for run_id, device_id, rows in episodes["_false_episode_rows"]:
        first = meta[rows[0]]
        false_alarms.append(
            {
                "run_id": run_id,
                "device_id": device_id,
                "run_spec": first["run_spec"],
                "demand_scale": first["demand_scale"],
                "start": first["interval_end"],
                "intervals": len(rows),
                "max_occ": float(max(X[i, ix["occ_last"]] for i in rows)),
                "min_speed": float(min(X[i, ix["speed_min"]] for i in rows)),
            }
        )
    false_alarms.sort(key=lambda r: (-r["intervals"], -r["max_occ"], r["run_id"]))
    return {
        "missed_incidents_total": len(missed),
        "missed_incidents_worst": missed[:limit],
        "false_alarm_episodes_total": len(false_alarms),
        "false_alarm_episodes_worst": false_alarms[:limit],
    }
