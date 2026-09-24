"""P06.01: measure how far loop-derived corridor KPIs are from SUMO's own
edge-wide ground truth (edgeData), per KPI, instead of assuming they agree.

Truth never feeds KPI computation: it is read only here, after the KPIs
exist. Definitions mirror kpis.py so the comparison is like for like:
volume = vehicles entering or being inserted onto the edge per hour; speed/travel time from the
edge's mean speed (weighted by SUMO's sampled seconds); density = mean
vehicles per km over the window's 30 s intervals (an interval SUMO omitted
because the edge was empty counts as density 0, free-flow speed).
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from backend.analytics.kpis import INTERVAL_SECONDS, WINDOW_SECONDS, iso_z, parse_time
from backend.analytics.topology import Segment, corridor_directions

COMPARED = (
    "volume_veh_h",
    "speed_m_s",
    "density_veh_km",
    "travel_time_s",
    "delay_s",
    "throughput_veh_h",
)
CONGESTED_SPEED_RATIO = 0.7


def load_truth(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def truth_windows(
    rows: list[dict],
    segments: list[Segment],
    anchor: str,
    window_s: int = WINDOW_SECONDS,
    interval_s: int = INTERVAL_SECONDS,
) -> dict[tuple[str, str, str], dict]:
    anchor_dt = parse_time(anchor)
    by_edge: dict[str, dict[float, dict]] = defaultdict(dict)
    for r in rows:
        by_edge[r["edge_id"]][r["begin_s"]] = r
    horizon = max((r["end_s"] for r in rows), default=0.0)
    out: dict[tuple[str, str, str], dict] = {}
    for (corridor, direction), segs in corridor_directions(segments).items():
        for w in range(int(horizon // window_s) + 1):
            w0 = w * window_s
            per_seg = []
            for seg in segs:
                entered = waiting = sampled = speed_x_sampled = density_sum = 0.0
                for k in range(window_s // interval_s):
                    row = by_edge[seg.edge_id].get(float(w0 + k * interval_s))
                    if row is None:
                        continue
                    entered += row["entered"] + row["departed"]
                    waiting += row["waiting_s"] or 0.0
                    density_sum += row["density_veh_km"] or 0.0
                    if row["speed_m_s"] is not None and row["sampled_seconds"]:
                        sampled += row["sampled_seconds"]
                        speed_x_sampled += row["speed_m_s"] * row["sampled_seconds"]
                speed = speed_x_sampled / sampled if sampled > 0 else seg.free_flow_speed_m_s
                per_seg.append(
                    {
                        "volume": entered * 3600.0 / window_s,
                        "speed": speed,
                        "density": density_sum / (window_s // interval_s),
                        "queued_vehicles": waiting / window_s,
                        "seg": seg,
                    }
                )
            total_len = sum(p["seg"].length_m for p in per_seg)
            tt = sum(p["seg"].length_m / max(p["speed"], 0.5) for p in per_seg)
            ff_tt = sum(p["seg"].length_m / p["seg"].free_flow_speed_m_s for p in per_seg)
            out[(corridor, direction, iso_z(anchor_dt + timedelta(seconds=w0)))] = {
                "volume_veh_h": sum(p["volume"] for p in per_seg) / len(per_seg),
                "speed_m_s": total_len / tt,
                "density_veh_km": sum(p["density"] * p["seg"].length_m for p in per_seg)
                / total_len,
                "travel_time_s": tt,
                "delay_s": max(0.0, tt - ff_tt),
                "throughput_veh_h": per_seg[-1]["volume"],
                "queued_vehicles": sum(p["queued_vehicles"] for p in per_seg) / len(per_seg),
                "speed_ratio": (total_len / tt)
                / (
                    sum(p["seg"].free_flow_speed_m_s * p["seg"].length_m for p in per_seg)
                    / total_len
                ),
            }
    return out


def _pearson(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    return round(statistics.correlation(x, y), 4)


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0
        i = j + 1
    return ranks


def compare(kpis: list[dict], truth: dict[tuple[str, str, str], dict]) -> dict:
    pairs = [
        (k, truth[(k["corridor_id"], k["direction"], k["window_start"])])
        for k in kpis
        if (k["corridor_id"], k["direction"], k["window_start"]) in truth
    ]
    report: dict = {
        "windows_compared": len(pairs),
        "per_kpi": {},
        "queue_indicator": {},
        "by_regime": {},
    }

    def stats(name: str, subset: list[tuple[dict, dict]]) -> dict:
        est = [k["kpis"][name] for k, _ in subset if k["kpis"].get(name) is not None]
        tru = [t[name] for k, t in subset if k["kpis"].get(name) is not None]
        if not est:
            return {"n": 0}
        err = [e - t for e, t in zip(est, tru, strict=True)]
        rel = [abs(e - t) / abs(t) for e, t in zip(est, tru, strict=True) if abs(t) > 1e-6]
        return {
            "n": len(est),
            "mae": round(sum(abs(x) for x in err) / len(err), 3),
            "bias": round(sum(err) / len(err), 3),
            "mape": round(sum(rel) / len(rel), 4) if rel else None,
            "pearson_r": _pearson(est, tru),
        }

    for name in COMPARED:
        report["per_kpi"][name] = stats(name, pairs)
    for regime, keep in (
        ("free_flow", lambda t: t["speed_ratio"] >= CONGESTED_SPEED_RATIO),
        ("congested", lambda t: t["speed_ratio"] < CONGESTED_SPEED_RATIO),
    ):
        subset = [(k, t) for k, t in pairs if keep(t)]
        report["by_regime"][regime] = {
            "windows": len(subset),
            **{n: stats(n, subset) for n in ("speed_m_s", "travel_time_s", "volume_veh_h")},
        }

    q_est = [k["kpis"]["queue_fraction"] for k, _ in pairs]
    q_tru = [t["queued_vehicles"] for _, t in pairs]
    if len(q_est) >= 3 and len(set(q_est)) > 1 and len(set(q_tru)) > 1:
        report["queue_indicator"] = {
            "spearman_r_vs_truth_waiting_vehicles": round(
                statistics.correlation(_ranks(q_est), _ranks(q_tru)), 4
            ),
            "windows_with_queue_at_loop": sum(1 for q in q_est if q > 0),
        }
    report["lane_scaling"] = "per-segment lane share (calibrated on TRAIN) or default 1/2"
    return report


def anchor_from_events(events: list[dict]) -> datetime | None:
    return min((parse_time(e["observation_time"]) for e in events), default=None)


def compare_runs(runs: dict[str, tuple[list[dict], dict[tuple[str, str, str], dict]]]) -> dict:
    """Pooled comparison over several runs (same window timestamps repeat in
    every run, so each run's keys are namespaced before pooling)."""
    kpis: list[dict] = []
    truth: dict[tuple[str, str, str], dict] = {}
    for name, (k_list, t_map) in runs.items():
        kpis += [{**k, "window_start": f"{name}|{k['window_start']}"} for k in k_list]
        truth.update({(c, d, f"{name}|{w}"): v for (c, d, w), v in t_map.items()})
    report = compare(kpis, truth)
    for name in COMPARED:
        vals = [
            truth[(k["corridor_id"], k["direction"], k["window_start"])][name]
            for k in kpis
            if (k["corridor_id"], k["direction"], k["window_start"]) in truth
            and k["kpis"].get(name) is not None
        ]
        report["per_kpi"][name]["truth_mean"] = round(sum(vals) / len(vals), 3) if vals else None
    return report
