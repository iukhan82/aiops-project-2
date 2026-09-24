"""P06.04: congestion and spillback detection from loop telemetry, and the
SUMO-truth episodes/matching that judge it.

Detector (rule, transparent; ADR-0005 baseline before ML): a segment is
congested while its loop shows occupancy >= `occ_on` with speed <= `speed_on`
(or standing traffic with no speed) for `n_on` consecutive 30 s intervals; the
episode clears after `n_off` consecutive intervals of clearly free flow.
Spillback is queue propagating upstream across a junction: an upstream
segment's episode starting while its immediate downstream neighbour's is
active. Every episode carries the ids of the events that triggered it
(evidence), a location (segment/corridor/direction) and onset/clear times.

Truth is SUMO's own edge-wide measurement, never seen by the detector: a
segment is congested while its standing queue (waiting vehicles x 3.5 m per
lane) is at least 30 m; truth spillback is the same propagation rule applied to
truth episodes. Physical limit, by design: a loop 10 m past the upstream stop
line only sees a queue once it has grown back to it.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from backend.analytics.kpis import INTERVAL_SECONDS, LOOP_EVENT_TYPE, parse_time
from backend.analytics.topology import Segment, corridor_directions, edge_of_lane

SOURCE = "rule:congestion/1"
SEVERITIES = ("low", "medium", "high", "critical")
GAP_S = 90
MAX_LAG_S = 180
MATCH_TOLERANCE_S = 60
TRUTH_QUEUE_M = 30.0
TRUTH_MIN_INTERVALS = 2
TRUTH_MERGE_GAP_S = 60
QUEUE_M_PER_WAITING_VEHICLE_PER_LANE = 3.5  # 7 m spacing / 2 lanes


@dataclass(frozen=True)
class Params:
    occ_on: float = 0.30
    speed_on: float = 8.0
    n_on: int = 2
    n_off: int = 2
    occ_off_frac: float = 0.6


@dataclass(frozen=True)
class SpillParams:
    """Inferred spillback: a congestion episode that persists this long at this
    occupancy has, physically, outgrown its own segment - its queue must now be
    crossing the upstream junction, where no loop can see it."""

    min_duration_s: float = 180.0
    min_peak_occupancy: float = 0.6


@dataclass
class Episode:
    segment: str
    corridor_id: str
    direction: str
    order: int
    onset: datetime
    detected_at: datetime
    clear: datetime | None
    peak_occupancy: float
    min_speed: float | None
    evidence: list[str] = field(default_factory=list)
    last_seen: datetime | None = None  # end of the latest interval that still showed congestion

    @property
    def end(self) -> datetime | None:
        return self.clear

    @property
    def duration_s(self) -> float:
        last = self.clear or self.last_seen or self.detected_at
        return (last - self.onset).total_seconds()

    @property
    def severity(self) -> str:
        if self.peak_occupancy >= 0.75 and self.duration_s >= 180:
            return "critical"
        if self.peak_occupancy >= 0.60 and self.duration_s >= 120:
            return "high"
        if self.peak_occupancy >= 0.45 or self.duration_s >= 120:
            return "medium"
        return "low"


@dataclass
class Spillback:
    origin_segment: str
    upstream_segment: str
    corridor_id: str
    direction: str
    onset: datetime
    detected_at: datetime
    clear: datetime | None
    severity: str
    evidence: list[str]
    inferred: bool = False


def _samples(events: Iterable[dict], seg_by_edge: dict[str, Segment]):
    by_edge: dict[str, list[tuple]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for e in events:
        if e["event_type"] != LOOP_EVENT_TYPE or not e["location"].get("lane_id"):
            continue
        edge = edge_of_lane(e["location"]["lane_id"])
        if edge not in seg_by_edge:
            continue
        m = {x["name"]: x for x in e["measurements"]}
        if "occupancy" not in m or m["occupancy"]["quality"] == "invalid":
            continue
        if (edge, e["observation_time"]) in seen:  # a repeated interval keeps its first event
            continue
        seen.add((edge, e["observation_time"]))
        end = parse_time(e["observation_time"])
        speed = m.get("mean_speed")
        by_edge[edge].append(
            (
                end - timedelta(seconds=INTERVAL_SECONDS),
                end,
                float(m["occupancy"]["value"]),
                float(speed["value"])
                if speed and speed["quality"] != "invalid" and m["vehicle_count"]["value"] > 0
                else None,
                e["event_id"],
            )
        )
    return by_edge


def detect_congestion(
    events: Iterable[dict], segments: list[Segment], params: Params = Params()
) -> list[Episode]:
    seg_by_edge = {s.edge_id: s for s in segments if s.is_corridor}
    episodes: list[Episode] = []
    for edge, rows in _samples(events, seg_by_edge).items():
        seg = seg_by_edge[edge]
        rows.sort(key=lambda r: r[0])
        pending: list[tuple] = []
        open_ep: Episode | None = None
        last_true_end: datetime | None = None
        off_run = 0
        prev_end: datetime | None = None

        def close(ep: Episode, at: datetime | None) -> None:
            ep.clear = at
            episodes.append(ep)

        for start, end, occ, speed, event_id in rows:
            if prev_end is not None and (start - prev_end).total_seconds() > GAP_S:
                if open_ep is not None:
                    close(open_ep, last_true_end)
                    open_ep, off_run = None, 0
                pending = []
            prev_end = end
            on = occ >= params.occ_on and (speed is None or speed <= params.speed_on)
            off = occ < params.occ_on * params.occ_off_frac or (
                speed is not None and speed > params.speed_on * 1.5
            )
            if open_ep is None:
                pending = pending + [(start, end, occ, speed, event_id)] if on else []
                if len(pending) >= params.n_on:
                    speeds = [p[3] for p in pending if p[3] is not None]
                    open_ep = Episode(
                        edge,
                        seg.corridor_id,
                        seg.direction,
                        seg.order,
                        pending[0][0],
                        end,
                        None,
                        max(p[2] for p in pending),
                        min(speeds) if speeds else None,
                        [p[4] for p in pending],
                        end,
                    )
                    last_true_end, off_run, pending = end, 0, []
            else:
                open_ep.evidence.append(event_id)
                open_ep.peak_occupancy = max(open_ep.peak_occupancy, occ)
                if speed is not None:
                    open_ep.min_speed = (
                        speed if open_ep.min_speed is None else min(open_ep.min_speed, speed)
                    )
                if on:
                    last_true_end, off_run = end, 0
                    open_ep.last_seen = (
                        end  # detected_at stays the moment the episode was first confirmed
                    )
                elif off:
                    off_run += 1
                    if off_run >= params.n_off:
                        close(open_ep, last_true_end)
                        open_ep, off_run = None, 0
                else:
                    off_run = 0
        if open_ep is not None:
            episodes.append(open_ep)
    return sorted(episodes, key=lambda e: (e.onset, e.segment))


def pair_spillbacks(
    spans: dict[str, list[tuple]], segments: list[Segment], max_lag_s: float = MAX_LAG_S
) -> list[tuple]:
    """(origin_edge, upstream_edge, upstream_span, origin_span) wherever an
    upstream segment's congestion starts while its downstream neighbour's is
    active and started no later than the upstream one (queue moving upstream).
    `spans[edge]` = [(onset, end_or_None, payload)]."""
    out = []
    for (_corridor, _direction), segs in corridor_directions(segments).items():
        for k in range(1, len(segs)):
            up, down = segs[k - 1].edge_id, segs[k].edge_id
            for u in spans.get(up, []):
                for d in spans.get(down, []):
                    d_end = d[1] or datetime.max.replace(tzinfo=d[0].tzinfo)
                    u_end = u[1] or datetime.max.replace(tzinfo=u[0].tzinfo)
                    started_after = 0 <= (u[0] - d[0]).total_seconds() <= max_lag_s
                    overlapping = u[0] <= d_end and d[0] <= u_end
                    if started_after and overlapping:
                        out.append((down, up, u, d))
    return out


def detect_spillback(
    episodes: list[Episode], segments: list[Segment], spill: SpillParams | None = None
) -> list[Spillback]:
    """Observed propagation (both segments' loops show it) plus, when `spill`
    is given, inferred spillback from persistent high-occupancy episodes."""
    spans = defaultdict(list)
    for e in episodes:
        spans[e.segment].append((e.onset, e.clear, e))
    out = []
    seen: set[tuple[str, datetime]] = set()
    for origin, upstream, u, d in pair_spillbacks(spans, segments):
        eu, ed = u[2], d[2]
        level = min(
            len(SEVERITIES) - 1,
            max(SEVERITIES.index(eu.severity), SEVERITIES.index(ed.severity)) + 1,
        )
        seen.add((origin, ed.onset))
        out.append(
            Spillback(
                origin,
                upstream,
                ed.corridor_id,
                ed.direction,
                eu.onset,
                max(eu.detected_at, ed.detected_at),
                eu.clear if eu.clear and ed.clear else None,
                SEVERITIES[level],
                ed.evidence + eu.evidence,
            )
        )
    if spill is not None:
        upstream_of = {
            segs[k].edge_id: segs[k - 1].edge_id
            for segs in corridor_directions(segments).values()
            for k in range(1, len(segs))
        }
        for e in episodes:
            if e.segment not in upstream_of or (e.segment, e.onset) in seen:
                continue
            if (
                e.duration_s >= spill.min_duration_s
                and e.peak_occupancy >= spill.min_peak_occupancy
            ):
                level = min(len(SEVERITIES) - 1, SEVERITIES.index(e.severity) + 1)
                out.append(
                    Spillback(
                        e.segment,
                        upstream_of[e.segment],
                        e.corridor_id,
                        e.direction,
                        e.onset,
                        e.onset + timedelta(seconds=spill.min_duration_s),
                        e.clear,
                        SEVERITIES[level],
                        list(e.evidence),
                        inferred=True,
                    )
                )
    return sorted(out, key=lambda s: (s.onset, s.origin_segment))


# ---------------------------------------------------------------- truth ----


@dataclass
class TruthEpisode:
    segment: str
    onset: datetime
    end: datetime
    peak_queue_m: float

    @property
    def severity(self) -> str:
        return (
            "low"
            if self.peak_queue_m < 60
            else "medium"
            if self.peak_queue_m < 120
            else "high"
            if self.peak_queue_m < 200
            else "critical"
        )


def truth_episodes(
    rows: list[dict], segments: list[Segment], anchor: datetime
) -> list[TruthEpisode]:
    seg_by_edge = {s.edge_id: s for s in segments if s.is_corridor}
    by_edge: dict[str, dict[float, float]] = defaultdict(dict)
    for r in rows:
        if r["edge_id"] in seg_by_edge:
            by_edge[r["edge_id"]][r["begin_s"]] = (
                (r["waiting_s"] or 0.0) / INTERVAL_SECONDS * QUEUE_M_PER_WAITING_VEHICLE_PER_LANE
            )
    out: list[TruthEpisode] = []
    for edge, series in by_edge.items():
        begins = sorted(b for b, q in series.items() if q >= TRUTH_QUEUE_M)
        groups: list[list[float]] = []
        for b in begins:
            if groups and b - groups[-1][-1] - INTERVAL_SECONDS <= TRUTH_MERGE_GAP_S:
                groups[-1].append(b)
            else:
                groups.append([b])
        for g in groups:
            if len(g) >= TRUTH_MIN_INTERVALS:
                out.append(
                    TruthEpisode(
                        edge,
                        anchor + timedelta(seconds=g[0]),
                        anchor + timedelta(seconds=g[-1] + INTERVAL_SECONDS),
                        max(series[b] for b in g),
                    )
                )
    return sorted(out, key=lambda t: (t.onset, t.segment))


def truth_spillbacks(truth: list[TruthEpisode], segments: list[Segment]) -> list[tuple]:
    spans = defaultdict(list)
    for t in truth:
        spans[t.segment].append((t.onset, t.end, t))
    return pair_spillbacks(spans, segments)


# -------------------------------------------------------------- matching ----


def match_spans(
    detected: list[tuple], truth: list[tuple], tol_s: float = MATCH_TOLERANCE_S
) -> list[tuple[int, int]]:
    """One-to-one greedy matching of (segment, onset, end_or_None) spans by
    earliest onset; a detection matches a truth span on the same key when the
    truth span, widened by `tol_s`, overlaps it."""
    used: set[int] = set()
    pairs = []
    for i, (key, onset, end) in sorted(enumerate(detected), key=lambda x: x[1][1]):
        end = end or datetime.max.replace(tzinfo=onset.tzinfo)
        for j, (tkey, t_on, t_end) in enumerate(truth):
            if j in used or tkey != key:
                continue
            tol = timedelta(seconds=tol_s)
            if onset <= t_end + tol and t_on - tol <= end:
                pairs.append((i, j))
                used.add(j)
                break
    return pairs


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def episode_id(kind: str, element: str, onset: datetime) -> str:
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"candidate:{kind}:{element}:{onset.isoformat()}:{SOURCE}")
    )
