"""P06.01: traffic aggregates and corridor KPIs from loop-detector telemetry.

Pure functions over observation-envelope events (`traffic.loop_detector.count`)
and P06.01's segment topology - the same code runs on dataset files (training,
validation) and on rows read back from `observation_events` (serving), so KPI
definitions cannot drift between the two.

Per (corridor, direction, window) it reports volume, space-mean speed,
density, a queue indicator, travel time, delay, throughput and - over a
trailing hour of windows - travel-time reliability. Every KPI is a derived
estimate (truth label `inferred`) from `simulated` loop telemetry, on the one
instrumented general lane of each two-lane segment, scaled to the segment by
a per-segment lane share calibrated on the TRAIN split only
(calibration.py; default 1/2 = even lane split);
backend/analytics/truth_validation.py measures how far each estimate is from
SUMO's own edge-wide ground truth rather than assuming it is right.

Definitions (chosen up front, not tuned to the truth):
- speed: space-mean, `sum(n) / sum(n/v)` over intervals with vehicles.
- density: `occupancy * 1000 / 4.5 m` for the instrumented lane, / lane share.
- queue_fraction: share of 30 s intervals where a vehicle stood or crept over
  the detector (`occupancy >= 0.35` and speed < 3 m/s). A loop 10 m past the
  upstream stop line only sees a queue once it grows back to it, so this is an
  indicator of queue *reaching the loop*, not a queue length.
- travel_time: sum over segments of `length / speed`, free-flow speed where a
  segment reported no vehicles (and quality drops accordingly).
- reliability: over the trailing 12 windows (>= 6 required), travel-time index
  (mean/free-flow), planning-time index (P95/free-flow), buffer index
  ((P95 - mean)/mean).
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.analytics.topology import Segment, corridor_directions, edge_of_lane

WINDOW_SECONDS = 300
INTERVAL_SECONDS = 30
VEHICLE_LENGTH_M = 4.5
QUEUE_OCCUPANCY = 0.35
QUEUE_SPEED_M_S = 3.0
MIN_SPEED_M_S = 0.5
SPEED_CAP_FACTOR = 1.25
COVERAGE_VALID = 0.8
RELIABILITY_WINDOWS = 12
RELIABILITY_MIN_WINDOWS = 6
LOOP_EVENT_TYPE = "traffic.loop_detector.count"

KPI_UNITS = {
    "volume_veh_h": "veh_h-1",
    "speed_m_s": "m_s-1",
    "density_veh_km": "veh_km-1",
    "queue_fraction": "ratio",
    "travel_time_s": "s",
    "free_flow_travel_time_s": "s",
    "delay_s": "s",
    "throughput_veh_h": "veh_h-1",
    "travel_time_index": "ratio",
    "planning_time_index": "ratio",
    "buffer_index": "ratio",
}


@dataclass(frozen=True)
class LoopSample:
    edge_id: str
    interval_start: datetime
    count: int
    occupancy: float
    speed_m_s: float | None


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def window_floor(t: datetime, window_s: int) -> datetime:
    epoch = t.timestamp()
    return datetime.fromtimestamp(epoch - (epoch % window_s), tz=timezone.utc)


def samples_from_events(
    events: Iterable[dict], interval_s: int = INTERVAL_SECONDS
) -> list[LoopSample]:
    """One sample per (edge, interval); a repeated interval keeps its first
    event (duplicate delivery must not double-count vehicles)."""
    seen: set[tuple[str, datetime]] = set()
    out: list[LoopSample] = []
    for event in events:
        if event["event_type"] != LOOP_EVENT_TYPE:
            continue
        lane_id = event["location"].get("lane_id")
        if not lane_id:
            continue
        m = {x["name"]: x for x in event["measurements"]}
        count = m.get("vehicle_count")
        if count is None or count["quality"] == "invalid":
            continue
        start = parse_time(event["observation_time"]) - timedelta(seconds=interval_s)
        key = (edge_of_lane(lane_id), start)
        if key in seen:
            continue
        seen.add(key)
        speed = m.get("mean_speed")
        occ = m.get("occupancy")
        out.append(
            LoopSample(
                edge_id=key[0],
                interval_start=start,
                count=int(count["value"]),
                occupancy=float(occ["value"]) if occ and occ["quality"] != "invalid" else 0.0,
                speed_m_s=float(speed["value"])
                if speed and speed["quality"] != "invalid"
                else None,
            )
        )
    return out


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _segment_window(samples: list[LoopSample], seg: Segment, interval_s: int) -> dict:
    n = len(samples)
    if n == 0:
        return {"reporting": False, "coverage": 0.0}
    observed_s = n * interval_s
    moving = [s for s in samples if s.count > 0 and s.speed_m_s is not None]
    denom = sum(s.count / max(s.speed_m_s, MIN_SPEED_M_S) for s in moving)
    speed = sum(s.count for s in moving) / denom if denom > 0 else None
    occupancy = sum(s.occupancy for s in samples) / n
    queued = sum(
        1
        for s in samples
        if s.occupancy >= QUEUE_OCCUPANCY and (s.speed_m_s is None or s.speed_m_s < QUEUE_SPEED_M_S)
    )
    return {
        "reporting": True,
        "intervals": n,
        "coverage": 0.0,  # filled by caller (needs the window's expected count)
        "volume_veh_h": sum(s.count for s in samples) * 3600.0 / observed_s / seg.lane_share,
        "speed_m_s": speed,
        "density_veh_km": occupancy * 1000.0 / VEHICLE_LENGTH_M / seg.lane_share,
        "queue_fraction": queued / n,
    }


def _corridor_window(
    segs: list[Segment],
    window_start: datetime,
    by_edge_window: dict,
    window_s: int,
    interval_s: int,
) -> dict | None:
    expected = window_s // interval_s
    stats = []
    for seg in segs:
        s = _segment_window(by_edge_window.get((seg.edge_id, window_start), []), seg, interval_s)
        s["coverage"] = min(1.0, s.get("intervals", 0) / expected)
        stats.append(s)
    reporting = [s for s in stats if s["reporting"]]
    if not reporting:
        return None

    total_len = sum(seg.length_m for seg in segs)
    tt = ff_tt = 0.0
    speed_observed = 0
    for seg, s in zip(segs, stats, strict=True):
        ff = seg.free_flow_speed_m_s
        v = s.get("speed_m_s")
        if v is None:
            v = ff
        else:
            speed_observed += 1
        v = min(max(v, MIN_SPEED_M_S), ff * SPEED_CAP_FACTOR)
        tt += seg.length_m / v
        ff_tt += seg.length_m / ff

    def mean_of(key: str) -> float:
        vals = [s[key] for s in reporting]
        return sum(vals) / len(vals)

    density = sum(
        s["density_veh_km"] * seg.length_m
        for seg, s in zip(segs, stats, strict=True)
        if s["reporting"]
    )
    density /= sum(seg.length_m for seg, s in zip(segs, stats, strict=True) if s["reporting"])
    last = stats[-1]
    min_cov = min(s["coverage"] for s in stats)
    quality = (
        "valid"
        if len(reporting) == len(segs) and min_cov >= COVERAGE_VALID and speed_observed == len(segs)
        else ("suspect" if len(reporting) * 2 >= len(segs) else "invalid")
    )
    return {
        "volume_veh_h": round(mean_of("volume_veh_h"), 2),
        "speed_m_s": round(total_len / tt, 3),
        "density_veh_km": round(density, 2),
        "queue_fraction": round(mean_of("queue_fraction"), 4),
        "travel_time_s": round(tt, 2),
        "free_flow_travel_time_s": round(ff_tt, 2),
        "delay_s": round(max(0.0, tt - ff_tt), 2),
        "throughput_veh_h": round(last["volume_veh_h"], 2) if last["reporting"] else None,
        "_meta": {
            "segments_reporting": len(reporting),
            "segments_expected": len(segs),
            "coverage": round(min_cov, 3),
            "sample_count": sum(s.get("intervals", 0) for s in stats),
            "speed_observed_segments": speed_observed,
            "quality": quality,
        },
    }


def add_reliability(records: list[dict]) -> None:
    """Fills TTI/PTI/BI from each series' own trailing windows, in place."""
    series: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in records:
        series[(r["corridor_id"], r["direction"])].append(r)
    for rows in series.values():
        rows.sort(key=lambda r: r["window_start"])
        for i, r in enumerate(rows):
            trailing = [
                x["kpis"]["travel_time_s"]
                for x in rows[max(0, i + 1 - RELIABILITY_WINDOWS) : i + 1]
            ]
            ff = r["kpis"]["free_flow_travel_time_s"]
            if len(trailing) >= RELIABILITY_MIN_WINDOWS:
                mean = sum(trailing) / len(trailing)
                p95 = percentile(trailing, 0.95)
                r["kpis"]["travel_time_index"] = round(mean / ff, 4)
                r["kpis"]["planning_time_index"] = round(p95 / ff, 4)
                r["kpis"]["buffer_index"] = round((p95 - mean) / mean, 4)
            else:
                r["kpis"]["travel_time_index"] = r["kpis"]["planning_time_index"] = r["kpis"][
                    "buffer_index"
                ] = None


def compute_corridor_kpis(
    events: Iterable[dict],
    segments: list[Segment],
    geometry_version: str,
    window_s: int = WINDOW_SECONDS,
    interval_s: int = INTERVAL_SECONDS,
) -> list[dict]:
    by_edge_window: dict[tuple[str, datetime], list[LoopSample]] = defaultdict(list)
    for s in samples_from_events(events, interval_s):
        by_edge_window[(s.edge_id, window_floor(s.interval_start, window_s))].append(s)
    windows = sorted({w for _, w in by_edge_window})
    out: list[dict] = []
    for (corridor, direction), segs in corridor_directions(segments).items():
        for w in windows:
            rec = _corridor_window(segs, w, by_edge_window, window_s, interval_s)
            if rec is None:
                continue
            meta = rec.pop("_meta")
            out.append(
                {
                    "corridor_id": corridor,
                    "direction": direction,
                    "window_start": iso_z(w),
                    "window_seconds": window_s,
                    "geometry_version": geometry_version,
                    "kpis": rec,
                    **meta,
                }
            )
    add_reliability(out)
    return out


def to_network_state_record(
    kpi: dict, as_of: datetime, max_staleness_s: float | None = None
) -> dict:
    """A stored KPI row as a `contracts/network-state/v1` corridor record.
    Freshness is computed against `as_of` at read time, never stored: a KPI
    row does not get more fresh by being re-served (SAFE-03)."""
    max_staleness_s = max_staleness_s or kpi["window_seconds"] * 2
    end = parse_time(kpi["window_start"]) + timedelta(seconds=kpi["window_seconds"])
    quality = kpi["quality"]
    confidence = round(min(1.0, kpi["coverage"]) * (1.0 if quality == "valid" else 0.6), 3)
    age = (as_of - end).total_seconds()
    return {
        "schema_version": "1.0.0",
        "record_id": str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"kpi:{kpi['corridor_id']}:{kpi['direction']}:{kpi['window_start']}:{kpi['window_seconds']}:{kpi['geometry_version']}",
            )
        ),
        "network_element_type": "corridor",
        "network_element_id": f"{kpi['corridor_id']}/{kpi['direction']}",
        "geometry_version": kpi["geometry_version"],
        "window_seconds": float(kpi["window_seconds"]),
        "observation_time": iso_z(end),
        "ingest_time": iso_z(max(as_of, end)),
        "measurements": [
            {
                "name": name,
                "value": value,
                "unit": KPI_UNITS[name],
                "quality": quality,
                "confidence": confidence,
                "sample_count": kpi["sample_count"],
            }
            for name, value in kpi["kpis"].items()
            if value is not None
        ],
        "truth_label": "inferred",
        "freshness_status": "fresh" if age <= max_staleness_s else "stale",
        "max_staleness_seconds": float(max_staleness_s),
    }
