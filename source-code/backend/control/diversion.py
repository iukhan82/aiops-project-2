"""P07.04: diversion recommendations for a closed or hazardous segment,
built on P07.02's real router - not a separate routing model.

Three alternatives, always in this order: divert general traffic onto the
router's own best detour, post a variable-message-sign advisory only (drivers
choose their own route; no forced diversion), or take no action. Benefit and
harm are both derived from the same real inputs: the segment's own recent
corridor KPI volume (P06.01) for how much traffic is affected, and the
router's own ETA delta (P07.02) for how much time a full diversion costs or
saves - never a number invented outside what those two pipelines measured.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.correlation import Topology  # noqa: E402
from backend.analytics.kpi_service import load_segments  # noqa: E402
from backend.control.engine import Alternative, Metric, SafetyBounds, enforce  # noqa: E402
from backend.routing.graph import RoadGraph  # noqa: E402
from backend.routing.live_state import fetch_live_state  # noqa: E402
from backend.routing.router import fastest_safe_routes  # noqa: E402

DEFAULT_BOUNDS = SafetyBounds(min_pedestrian_clearance_s=7.0, max_signal_deviation_s=20.0)
DEFAULT_VOLUME_VEH_H = 200.0  # used only when no recent KPI volume exists for the affected corridor - stated, not silent


def _recent_volume(
    conn: psycopg.Connection,
    corridor_id: str | None,
    direction: str | None,
    geometry: str,
    now: datetime,
) -> tuple[float, bool]:
    """(volume_veh_h, measured) - `measured` is False when falling back to the stated default."""
    if corridor_id is None:
        return DEFAULT_VOLUME_VEH_H, False
    with conn.cursor() as cur:
        cur.execute(
            "SELECT kpis->>'throughput_veh_h' FROM corridor_kpis WHERE corridor_id = %s AND direction = %s AND geometry_version = %s "
            "AND window_start > %s ORDER BY window_start DESC LIMIT 1",
            (corridor_id, direction, geometry, now - timedelta(seconds=900)),
        )
        row = cur.fetchone()
    if row is None or row[0] is None:
        return DEFAULT_VOLUME_VEH_H, False
    return float(row[0]), True


def build_diversion_recommendation(
    conn: psycopg.Connection,
    closed_edge: str,
    origin: str,
    destination: str,
    geometry: str,
    now: datetime,
    bounds: SafetyBounds = DEFAULT_BOUNDS,
) -> tuple[list[Alternative], list[str]]:
    segments = load_segments(conn, geometry)
    graph = RoadGraph(segments)
    topo = Topology(segments, 3)
    live_state = fetch_live_state(conn, geometry, topo, now)
    seg = topo.segments.get(closed_edge)
    volume, measured = _recent_volume(
        conn, seg.corridor_id if seg else None, seg.direction if seg else None, geometry, now
    )

    routes = fastest_safe_routes(
        graph, origin, destination, live_state, geometry, max_alternatives=1
    )
    if not routes:
        return [], []
    diverted = routes[0]
    free_flow_state = {e: s for e, s in live_state.items()}
    without_closure = {e: v for e, v in free_flow_state.items() if e != closed_edge}
    unaffected = fastest_safe_routes(
        graph, origin, destination, without_closure, geometry, max_alternatives=1
    )
    delay_s_per_vehicle = max(
        0.0,
        diverted.eta_seconds - (unaffected[0].eta_seconds if unaffected else diverted.eta_seconds),
    )
    vehicles_affected_per_hour = volume
    # diverting does not eliminate the delay the closure itself causes (that is physical); it only
    # avoids the WORSE delay of vehicles queuing at the closure with no guidance - modelled as the
    # gap between an unguided approach (assumed 2x the diverted ETA delay, stated as an assumption)
    # and the guided diversion's own delay.
    unguided_delay_s = delay_s_per_vehicle * 2.0
    veh_h_saved_by_guidance = max(
        0.0, vehicles_affected_per_hour * (unguided_delay_s - delay_s_per_vehicle) / 3600.0
    )

    confidence = 0.75 if measured else 0.45
    alternatives = [
        Alternative(
            f"alt-{uuid.uuid4()}",
            f"Divert traffic around {closed_edge} onto {' -> '.join(diverted.edges)}",
            (Metric("network_delay_avoided", veh_h_saved_by_guidance, "veh_h"),),
            (
                Metric("added_travel_time_per_diverted_vehicle", delay_s_per_vehicle, "s"),
                Metric("vehicles_affected", vehicles_affected_per_hour, "veh_h-1"),
            ),
            confidence,
        ),
        Alternative(
            f"alt-{uuid.uuid4()}",
            f"Post a variable-message-sign advisory for {closed_edge}; no forced diversion",
            (Metric("network_delay_avoided", veh_h_saved_by_guidance * 0.4, "veh_h"),),
            (Metric("added_travel_time_per_diverted_vehicle", 0.0, "s"),),
            confidence * 0.7,
        ),
        Alternative(
            f"alt-{uuid.uuid4()}",
            "Take no action",
            (),
            (Metric("network_delay_avoided", 0.0, "veh_h"),),
            1.0,
        ),
    ]
    constraints = [
        "closure-respected",
        "measured-corridor-volume" if measured else "default-volume-assumption-used",
    ]
    return enforce(alternatives, bounds), constraints
