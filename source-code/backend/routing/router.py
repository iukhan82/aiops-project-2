"""P07.02: fastest-safe route and ETA alternatives.

Dijkstra over the real network graph (backend/routing/graph.py), cost per
segment built from live state (backend/routing/live_state.py): a closed
segment is never traversed at all (not merely penalized - a route must not
cross an active blockage/collision/flooding); a hazard segment is heavily
penalized but still passable, because an emergency vehicle sometimes has no
alternative and must be able to proceed deliberately through it rather than
being silently routed elsewhere. Segments with a recent live KPI use its
measured travel time; everything else uses posted free-flow speed.

Alternatives are produced by the standard penalize-and-resolve method: solve
for the fastest route, then solve again with that route's own segments made
expensive, up to `max_alternatives` times, discarding a candidate that
reuses too much of an already-kept route (Jaccard over segment sets) - a
"different" route, not the same path with one detour.

Uncertainty is grounded in what has actually been measured: a segment with a
buffer_index (P06.01's reliability metric, `(p95 - mean) / mean` over its own
trailing windows) contributes `travel_time_s * buffer_index`; a segment with
no live KPI window contributes `DEFAULT_UNCERTAINTY_RATIO * travel_time_s`,
the network's stated planning margin when nothing has actually been measured -
never a silently-invented precise number.
"""

from __future__ import annotations

import heapq
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.topology import Segment  # noqa: E402
from backend.routing.graph import EdgeState, RoadGraph  # noqa: E402

HAZARD_PENALTY = (
    4.0  # multiplies a hazardous segment's travel time in the cost function (not the reported ETA)
)
DEFAULT_UNCERTAINTY_RATIO = (
    0.15  # planning margin for a segment with no live reliability measurement
)
ALTERNATIVE_PENALTY = 6.0
MAX_JACCARD_OVERLAP = 0.6  # an alternative sharing more of its segments than this with a kept route is not "different"


@dataclass(frozen=True)
class RouteAlternative:
    route_id: str
    geometry_version: str
    distance_m: float
    eta_seconds: float
    eta_uncertainty_seconds: float
    edges: tuple[str, ...]
    constraints_applied: tuple[str, ...] = ()
    selected: bool = False

    def as_record(self) -> dict:
        return {
            "route_id": self.route_id,
            "geometry_version": self.geometry_version,
            "distance_m": round(self.distance_m, 2),
            "eta_seconds": round(self.eta_seconds, 1),
            "eta_uncertainty_seconds": round(self.eta_uncertainty_seconds, 1),
            "constraints_applied": list(self.constraints_applied),
            "selected": self.selected,
        }


def _segment_cost(
    seg: Segment, edge_state: EdgeState, hazard_penalty: float
) -> tuple[float, float, bool]:
    """(cost_for_search, real_travel_time_s, is_hazard). Cost inflates a hazard for ranking; the reported
    ETA always uses the real (un-inflated) travel time so a displayed number is never fictional."""
    travel_time = (
        edge_state.travel_time_s
        if edge_state.travel_time_s is not None
        else seg.length_m / seg.free_flow_speed_m_s
    )
    cost = travel_time * (hazard_penalty if edge_state.hazard else 1.0)
    return cost, travel_time, edge_state.hazard


@dataclass
class _SearchResult:
    edges: list[Segment]
    distance_m: float
    eta_seconds: float
    uncertainty_seconds: float
    constraints: list[str]


def _dijkstra(
    graph: RoadGraph,
    origin: str,
    destination: str,
    live_state: dict[str, EdgeState],
    extra_penalty: dict[str, float],
) -> _SearchResult | None:
    dist: dict[str, float] = {origin: 0.0}
    prev: dict[str, Segment] = {}
    visited: set[str] = set()
    heap = [(0.0, origin)]
    while heap:
        d, node = heapq.heappop(heap)
        if node in visited:
            continue
        visited.add(node)
        if node == destination:
            break
        for seg in graph.neighbors(node):
            state = live_state.get(seg.edge_id, EdgeState())
            if state.closed:
                continue
            cost, _tt, _hz = _segment_cost(seg, state, HAZARD_PENALTY)
            cost *= extra_penalty.get(seg.edge_id, 1.0)
            nd = d + cost
            if nd < dist.get(seg.to_node, float("inf")):
                dist[seg.to_node] = nd
                prev[seg.to_node] = seg
                heapq.heappush(heap, (nd, seg.to_node))
    if destination not in dist:
        return None
    path: list[Segment] = []
    node = destination
    while node != origin:
        seg = prev[node]
        path.append(seg)
        node = seg.from_node
    path.reverse()
    distance_m = sum(s.length_m for s in path)
    eta = uncertainty = 0.0
    constraints: list[str] = []
    for seg in path:
        state = live_state.get(seg.edge_id, EdgeState())
        _cost, tt, hazard = _segment_cost(seg, state, HAZARD_PENALTY)
        eta += tt
        uncertainty += tt * (
            state.buffer_index if state.buffer_index is not None else DEFAULT_UNCERTAINTY_RATIO
        )
        if hazard:
            constraints.append(f"through_hazard:{state.hazard_reason}")
    return _SearchResult(path, distance_m, eta, uncertainty, constraints)


def _edge_set(result: _SearchResult) -> set[str]:
    return {s.edge_id for s in result.edges}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a or b else 0.0


def fastest_safe_routes(
    graph: RoadGraph,
    origin: str,
    destination: str,
    live_state: dict[str, EdgeState],
    geometry_version: str,
    max_alternatives: int = 3,
    vehicle_class: str | None = None,
) -> list[RouteAlternative]:
    """Up to `max_alternatives` genuinely different routes, fastest first, with `selected=True` on the first."""
    if origin == destination:
        return []
    kept: list[_SearchResult] = []
    penalty: dict[str, float] = {}
    for _ in range(
        max_alternatives * 3
    ):  # generous retry budget: not every penalized search yields a *new* route
        if len(kept) >= max_alternatives:
            break
        result = _dijkstra(graph, origin, destination, live_state, penalty)
        if result is None:
            break
        edges = _edge_set(result)
        if any(_jaccard(edges, _edge_set(k)) > MAX_JACCARD_OVERLAP for k in kept):
            for seg in result.edges:
                penalty[seg.edge_id] = penalty.get(seg.edge_id, 1.0) * ALTERNATIVE_PENALTY
            continue
        kept.append(result)
        for seg in result.edges:
            penalty[seg.edge_id] = penalty.get(seg.edge_id, 1.0) * ALTERNATIVE_PENALTY
    out = []
    for i, r in enumerate(kept):
        constraints = list(dict.fromkeys(r.constraints))
        if vehicle_class:
            constraints.append(f"vehicle_class:{vehicle_class}")
        out.append(
            RouteAlternative(
                f"route-{uuid.uuid4()}",
                geometry_version,
                r.distance_m,
                r.eta_seconds,
                r.uncertainty_seconds,
                tuple(s.edge_id for s in r.edges),
                tuple(constraints),
                selected=(i == 0),
            )
        )
    return out


def closed_segments_on_route(
    route: RouteAlternative, live_state: dict[str, EdgeState]
) -> list[str]:
    """For re-verification after live state changes (P07.09's independent outcome check reuses this)."""
    return [e for e in route.edges if live_state.get(e, EdgeState()).closed]
