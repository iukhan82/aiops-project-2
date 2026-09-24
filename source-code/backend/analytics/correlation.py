"""P06.07: correlate detection candidates into incidents (pure logic, no I/O).

A candidate is one detector's claim; an incident is what an operator works.
Several candidates describe the same physical event (a stalled vehicle, the
queue behind it, the congestion detector's own episode, a collision flag), so
they are grouped, and the group - not each claim - is what gets an owner.

Grouping rules (temporal overlap with slack AND spatial relation on the real
network graph):
- same kind on the same element                      -> duplicate_source
- collision <-> stalled_vehicle, congestion <-> spillback, co-located
                                                       -> duplicate_source
- a cause (collision, wrong_way, stalled_vehicle, flooding, low_visibility)
  and a congestion/spillback on the same segment or up to `max_hops` segments
  upstream (queues grow against the flow)              -> consequence
- collision/stall near an intersection with a pedestrian_conflict, or two
  different causes on one segment                      -> related
Connected components become incidents; the highest-precedence kind is the
incident's type.

Confidence is *calibrated*, not the detector's own score: each kind carries the
validation precision measured in evaluation (backend/analytics/artifacts/
incident_policy.json), and only *independent sensor modalities* add up - a
stalled-vehicle candidate and a congestion episode both come from the same loop
detectors, so together they are one modality (max), while a road-condition
sensor is a second (noisy-OR across modalities).

Hypotheses are ranked, plainly labelled as hypotheses, and never written as a
verified cause.
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from backend.analytics.topology import Segment

POLICY_PATH = Path(__file__).resolve().parent / "artifacts" / "incident_policy.json"

SEVERITIES = ["low", "medium", "high", "critical"]
KIND_PRECEDENCE = [
    "collision",
    "wrong_way",
    "stalled_vehicle",
    "flooding",
    "low_visibility",
    "pedestrian_conflict",
    "spillback",
    "congestion",
]
CAUSE_KINDS = {"collision", "wrong_way", "stalled_vehicle", "flooding", "low_visibility"}
CONSEQUENCE_KINDS = {"congestion", "spillback"}
DUPLICATE_PAIRS = {
    frozenset({"collision", "stalled_vehicle"}),
    frozenset({"congestion", "spillback"}),
}
OWNER_ROLE = {
    "collision": "EMERG",
    "wrong_way": "EMERG",
    "stalled_vehicle": "OPS",
    "flooding": "OPS",
    "low_visibility": "OPS",
    "pedestrian_conflict": "OPS",
    "spillback": "CONTROL",
    "congestion": "CONTROL",
}
DEVICE_MODALITY = {
    "inductive_loop": "loop_detector",
    "edge_camera": "camera",
    "road_condition_sensor": "road_condition_sensor",
    "weather_station": "weather_station",
    "crossing_detector": "crossing_detector",
}
NAMESPACE = uuid.UUID("6f6f6f6f-0607-4a4a-8a8a-202609200007")


@dataclass(frozen=True)
class Policy:
    calibrated_precision: dict
    open_confidence: float = 0.5
    slack_s: float = 300.0
    max_hops: int = 3
    resolve_hysteresis_s: float = 120.0
    escalate_unacknowledged_after_s: float = 600.0
    escalate_corroborated_confidence: float = 0.85

    def precision(self, kind: str, corroborated: bool) -> float:
        p = self.calibrated_precision[kind]
        if isinstance(p, dict):
            return p["corroborated"] if corroborated else p["single_source"]
        return p


def load_policy(path: Path = POLICY_PATH) -> Policy:
    raw = json.loads(path.read_text(encoding="utf-8"))
    keys = (
        "open_confidence",
        "slack_s",
        "max_hops",
        "resolve_hysteresis_s",
        "escalate_unacknowledged_after_s",
        "escalate_corroborated_confidence",
    )
    return Policy(raw["calibrated_precision"], **{k: raw[k] for k in keys if k in raw})


@dataclass(frozen=True)
class Cand:
    candidate_id: str
    kind: str
    element_type: str
    element_id: str
    onset: datetime
    end: datetime | None  # clear_time; None = still active
    detected_at: datetime
    severity: str
    confidence: float
    source: str
    modality: str
    corroborated: bool
    evidence: tuple[str, ...]
    attributes: dict = field(default_factory=dict, compare=False, hash=False)
    last_seen: datetime | None = (
        None  # latest evidence time; an "active" candidate is not assumed alive forever
    )


def view_at(c: Cand, now: datetime) -> Cand | None:
    """What was knowable at `now`: nothing before detection, and a clear time only once it has passed."""
    if c.detected_at > now:
        return None
    fields = dict(c.__dict__)
    if c.end is not None and c.end > now:
        fields["end"] = None
    if c.last_seen is not None and c.last_seen > now:
        fields["last_seen"] = now
    return Cand(**fields) if fields != c.__dict__ else c


class Topology:
    def __init__(self, segments: list[Segment], max_hops: int = 3) -> None:
        self.segments = {s.edge_id: s for s in segments}
        feeders: dict[str, set[str]] = defaultdict(
            set
        )  # segment -> segments whose to_node is its from_node
        for s in segments:
            for other in segments:
                if (
                    other.to_node == s.from_node
                    and other.edge_id != s.edge_id
                    and not (other.from_node == s.to_node)
                ):
                    feeders[s.edge_id].add(other.edge_id)
        self.upstream: dict[str, set[str]] = {}
        for s in segments:
            seen: set[str] = set()
            frontier = {s.edge_id}
            for _ in range(max_hops):
                frontier = {f for x in frontier for f in feeders[x]} - seen - {s.edge_id}
                seen |= frontier
            self.upstream[s.edge_id] = seen
        self.corridor: dict[str, set[str]] = defaultdict(set)
        self.junction: dict[str, set[str]] = defaultdict(set)
        for s in segments:
            if s.corridor_id:
                self.corridor[s.corridor_id].add(s.edge_id)
            self.junction[s.from_node].add(s.edge_id)
            self.junction[s.to_node].add(s.edge_id)

    def segments_of(self, element_type: str, element_id: str) -> set[str]:
        if element_type == "segment":
            return {element_id} if element_id in self.segments else set()
        if element_type == "corridor":
            return set(self.corridor.get(element_id, ()))
        if element_type == "intersection":
            return set(self.junction.get(element_id, ()))
        return set()

    def upstream_segments(self, segs: set[str]) -> set[str]:
        return {u for s in segs for u in self.upstream.get(s, ())}


def _end(c: Cand, now: datetime, slack: timedelta) -> datetime:
    """A cleared candidate ends when it cleared. One with no clear time is still ongoing - but only as far as
    its evidence goes: a flag last seen hours ago must not absorb whatever happens nearby now."""
    if c.end is not None:
        return c.end
    return min(now, c.last_seen + slack) if c.last_seen is not None else now


def temporally_related(a: Cand, b: Cand, now: datetime, slack: timedelta) -> bool:
    return a.onset - slack <= _end(b, now, slack) and b.onset - slack <= _end(a, now, slack)


def _precedence(c: Cand) -> tuple[int, datetime]:
    return KIND_PRECEDENCE.index(c.kind), c.onset


def link(a: Cand, b: Cand, topo: Topology, policy: Policy, now: datetime) -> str | None:
    """Relation of the lower-precedence candidate to the higher-precedence one, or None."""
    if _precedence(b) < _precedence(a):
        a, b = b, a
    if not temporally_related(a, b, now, timedelta(seconds=policy.slack_s)):
        return None
    sa, sb = (
        topo.segments_of(a.element_type, a.element_id),
        topo.segments_of(b.element_type, b.element_id),
    )
    colocated = bool(sa & sb)
    if a.kind == b.kind:
        return (
            "duplicate_source"
            if (a.element_id == b.element_id or (colocated and a.kind in CONSEQUENCE_KINDS))
            else None
        )
    if frozenset({a.kind, b.kind}) in DUPLICATE_PAIRS:
        return "duplicate_source" if colocated else None
    if a.kind in CAUSE_KINDS and b.kind in CONSEQUENCE_KINDS:
        upstream = bool(topo.upstream_segments(sa) & sb)
        return (
            "consequence"
            if (colocated or upstream) and b.onset >= a.onset - timedelta(seconds=policy.slack_s)
            else None
        )
    if a.kind == "collision" and b.kind == "pedestrian_conflict":
        return "related" if colocated else None
    if a.kind in CAUSE_KINDS and b.kind in CAUSE_KINDS:
        return "related" if colocated else None
    return None


@dataclass
class Group:
    members: list[Cand]
    relations: dict[str, str]  # candidate_id -> relation to the group's primary

    @property
    def primary(self) -> Cand:
        return min(self.members, key=_precedence)


def correlate(candidates: list[Cand], topo: Topology, policy: Policy, now: datetime) -> list[Group]:
    """Connected components of the link graph. `candidates` should already be `view_at(now)`."""
    parent = {c.candidate_id: c.candidate_id for c in candidates}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(candidates):
        for b in candidates[i + 1 :]:
            if link(a, b, topo, policy, now):
                parent[find(a.candidate_id)] = find(b.candidate_id)
    comps: dict[str, list[Cand]] = defaultdict(list)
    for c in candidates:
        comps[find(c.candidate_id)].append(c)
    groups = []
    for members in comps.values():
        members.sort(key=lambda c: (_precedence(c), c.candidate_id))
        primary = members[0]
        rel = {primary.candidate_id: "primary"}
        for m in members[1:]:
            rel[m.candidate_id] = link(primary, m, topo, policy, now) or _indirect_relation(
                primary, m
            )
        groups.append(Group(members, rel))
    return sorted(groups, key=lambda g: (g.primary.onset, g.primary.candidate_id))


def _indirect_relation(primary: Cand, member: Cand) -> str:
    if member.kind in CONSEQUENCE_KINDS and primary.kind in CAUSE_KINDS:
        return "consequence"
    return "duplicate_source" if member.kind == primary.kind else "related"


def group_confidence(group: Group, policy: Policy) -> float:
    by_modality: dict[str, float] = {}
    for m in group.members:
        p = policy.precision(m.kind, m.corroborated)
        by_modality[m.modality] = max(by_modality.get(m.modality, 0.0), p)
    miss = 1.0
    for p in by_modality.values():
        miss *= 1.0 - p
    return round(1.0 - miss, 4)


def group_severity(group: Group) -> str:
    return max((m.severity for m in group.members), key=SEVERITIES.index)


def hypotheses(group: Group, policy: Policy, topo: Topology) -> list[dict]:
    """Ranked, explicitly unverified. `likelihood` is each alternative's share of the calibrated evidence among
    the hypotheses considered - a ranking aid, not a probability that the hypothesis is true."""
    primary = group.primary
    kinds = {m.kind for m in group.members}
    where = primary.element_id
    reach = sorted({m.element_id for m in group.members if m.kind in CONSEQUENCE_KINDS})
    queue = f"; queue/congestion observed on {', '.join(reach)}" if reach else ""
    raw: list[tuple[str, float, list[str]]] = []

    def support(*wanted: str) -> list[str]:
        return [m.candidate_id for m in group.members if m.kind in wanted]

    if "collision" in kinds:
        raw.append(
            (
                f"Collision or crash on {where}{queue}",
                policy.precision("collision", False),
                support("collision", "stalled_vehicle"),
            )
        )
    if "stalled_vehicle" in kinds:
        cand = next(m for m in group.members if m.kind == "stalled_vehicle")
        raw.append(
            (
                f"Stalled or disabled vehicle blocking a lane on {cand.element_id}{queue}",
                policy.precision("stalled_vehicle", cand.corroborated),
                support("stalled_vehicle", "congestion", "spillback"),
            )
        )
    if "wrong_way" in kinds:
        raw.append(
            (
                f"Wrong-way vehicle on {where}",
                policy.precision("wrong_way", False),
                support("wrong_way"),
            )
        )
    if "flooding" in kinds:
        raw.append(
            (
                f"Flooded road surface reducing capacity on {where}{queue}",
                policy.precision("flooding", False),
                support("flooding", "congestion", "spillback"),
            )
        )
    if "low_visibility" in kinds:
        raw.append(
            (
                f"Low visibility on {where}{queue}",
                policy.precision("low_visibility", False),
                support("low_visibility", "congestion", "spillback"),
            )
        )
    if "pedestrian_conflict" in kinds:
        cand = next(m for m in group.members if m.kind == "pedestrian_conflict")
        raw.append(
            (
                f"Elevated vehicle-pedestrian conflict at {cand.element_id}",
                policy.precision("pedestrian_conflict", False),
                support("pedestrian_conflict"),
            )
        )
    if not (kinds & CAUSE_KINDS) and kinds & CONSEQUENCE_KINDS:
        base = max(policy.precision(k, False) for k in kinds & CONSEQUENCE_KINDS)
        raw.append(
            (
                f"Demand-driven congestion on {where}{queue}",
                base * 0.6,
                support("congestion", "spillback"),
            )
        )
        raw.append(
            (
                f"Unobserved blockage or bottleneck downstream of {where}",
                base * 0.4,
                support("congestion", "spillback"),
            )
        )
    total = sum(p for _, p, _ in raw) or 1.0
    ranked = sorted(raw, key=lambda r: -r[1])
    return [
        {
            "rank": i + 1,
            "hypothesis": h + " (hypothesis, not verified)",
            "likelihood": round(p / total, 4),
            "supporting_candidate_ids": ids,
        }
        for i, (h, p, ids) in enumerate(ranked)
    ]


def incident_key(group: Group) -> str:
    """Stable id for a *new* incident from its primary candidate."""
    return str(uuid.uuid5(NAMESPACE, group.primary.candidate_id))


@dataclass
class SimIncident:
    """What `incident_service.sync` would have opened, computed without a database (evaluation)."""

    members: set[str]
    opened_at: datetime
    primary: Cand
    confidence: float
    opening_evidence: tuple[
        str, ...
    ] = ()  # evidence of the group that triggered creation - not later merges/consequences
    merged_into: int | None = None


def simulate_openings(
    candidates: list[Cand], topo: Topology, policy: Policy, ticks: list[datetime] | None = None
) -> list[SimIncident]:
    """Replays the opening/merging half of the lifecycle. With `ticks=None` the correlator runs at every candidate
    detection time (event-driven); pass a schedule to mirror a periodic `sync`."""
    times = ticks or sorted({c.detected_at for c in candidates})
    incidents: list[SimIncident] = []
    owner: dict[str, int] = {}
    for now in times:
        visible = [v for c in candidates if (v := view_at(c, now)) is not None]
        for group in correlate(visible, topo, policy, now):
            ids = {m.candidate_id for m in group.members}
            confidence = group_confidence(group, policy)
            live = sorted(
                {owner[i] for i in ids if i in owner and incidents[owner[i]].merged_into is None}
            )
            if not live:
                if confidence < policy.open_confidence:
                    continue
                opening_evidence = tuple(
                    dict.fromkeys(e for m in group.members for e in m.evidence)
                )
                incidents.append(
                    SimIncident(set(ids), now, group.primary, confidence, opening_evidence)
                )
                index = len(incidents) - 1
            else:
                index = min(live, key=lambda i: (incidents[i].opened_at, i))
                for other in live:
                    if other != index:
                        incidents[index].members |= incidents[other].members
                        incidents[other].merged_into = index
                incidents[index].members |= ids
                incidents[index].primary = group.primary
                incidents[index].confidence = confidence
            for i in incidents[index].members:
                owner[i] = index
    return [i for i in incidents if i.merged_into is None]
