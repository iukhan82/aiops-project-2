"""P07.02: read the real, currently-live network state a route must respect -
active incidents (closures/hazards) and the latest corridor KPIs (real-time
travel time and reliability) - and turn it into per-segment `EdgeState`.

Nothing here is invented for routing: an incident is only a closure/hazard if
P06.07's own correlator has it open right now, and a segment's travel time
only overrides free-flow when P06.01's own KPI pipeline has actually measured
it recently. A segment with no live evidence routes on its posted free-flow
speed with the network's own baseline uncertainty - never silently on stale
data (a network-state consumer must never treat old evidence as fresh,
per docs/PROJECT_CONTEXT.md and SAFE-03's intent).
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.correlation import Topology  # noqa: E402
from backend.routing.graph import EdgeState  # noqa: E402

LIVE_INCIDENT_STATUSES = ("open", "acknowledged", "investigating", "escalated", "reopened")
# A closure needs real confidence: only a corroborated/high-precision incident blocks a route outright.
CLOSURE_KINDS = {"stalled_vehicle", "collision", "wrong_way", "flooding"}
CLOSURE_MIN_SEVERITY = {"high", "critical"}
HAZARD_KINDS = {
    "congestion",
    "spillback",
    "low_visibility",
    "pedestrian_conflict",
    "cyclist_conflict",
}
KPI_MAX_AGE_S = 900.0  # a KPI window older than this is not "live" for routing purposes


def fetch_live_state(
    conn: psycopg.Connection, geometry: str, topo: Topology, now: datetime
) -> dict[str, EdgeState]:
    state: dict[str, EdgeState] = {}

    def mark(segment_ids, closed=False, reason=None, hazard=False):
        for seg in segment_ids:
            cur = state.get(seg, EdgeState())
            if closed:
                state[seg] = EdgeState(
                    closed=True,
                    closed_reason=reason,
                    hazard=cur.hazard,
                    hazard_reason=cur.hazard_reason,
                    travel_time_s=cur.travel_time_s,
                    buffer_index=cur.buffer_index,
                )
            elif hazard and not cur.closed:
                state[seg] = EdgeState(
                    closed=cur.closed,
                    closed_reason=cur.closed_reason,
                    hazard=True,
                    hazard_reason=reason or cur.hazard_reason,
                    travel_time_s=cur.travel_time_s,
                    buffer_index=cur.buffer_index,
                )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT incident_type, severity, network_element_type, network_element_id
            FROM incidents WHERE status = ANY(%s) AND duplicate_of IS NULL AND geometry_version = %s
            """,
            (list(LIVE_INCIDENT_STATUSES), geometry),
        )
        for kind, severity, etype, eid in cur.fetchall():
            segs = topo.segments_of(etype, eid)
            if kind in CLOSURE_KINDS and severity in CLOSURE_MIN_SEVERITY:
                mark(segs, closed=True, reason=f"{kind} on {eid}")
            elif kind in HAZARD_KINDS or kind in CLOSURE_KINDS:
                mark(segs, hazard=True, reason=f"{kind} on {eid}")

        cur.execute(
            """
            SELECT DISTINCT ON (corridor_id, direction) corridor_id, direction, kpis, window_start
            FROM corridor_kpis WHERE geometry_version = %s AND window_start > %s
            ORDER BY corridor_id, direction, window_start DESC
            """,
            (geometry, now - timedelta(seconds=KPI_MAX_AGE_S)),
        )
        for corridor_id, direction, kpis, _window_start in cur.fetchall():
            travel_time, free_flow_time = (
                kpis.get("travel_time_s"),
                kpis.get("free_flow_travel_time_s"),
            )
            buffer_index = kpis.get("buffer_index")
            if travel_time is None or not free_flow_time:
                continue
            # the KPI is corridor-wide; a segment's share of it is proportional to its own free-flow time,
            # so the corridor's measured delay ratio is distributed uniformly - the finest granularity the
            # KPI pipeline actually measures (backend/analytics/kpis.py: travel_time_s is a corridor sum)
            delay_ratio = travel_time / free_flow_time
            for seg in topo.corridor.get(corridor_id, ()):
                s = topo.segments.get(seg)
                if s is not None and s.direction == direction:
                    cur_state = state.get(seg, EdgeState())
                    segment_free_flow = s.length_m / s.free_flow_speed_m_s
                    state[seg] = EdgeState(
                        closed=cur_state.closed,
                        closed_reason=cur_state.closed_reason,
                        hazard=cur_state.hazard,
                        hazard_reason=cur_state.hazard_reason,
                        travel_time_s=segment_free_flow * delay_ratio,
                        buffer_index=buffer_index,
                    )
    return state
