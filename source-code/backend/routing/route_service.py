"""P07.02: wires the router to the real network graph and real live state
(incidents + corridor KPIs), the way P07.01's CAD adapter and P07.07's
green-corridor logic call it.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.correlation import Topology  # noqa: E402
from backend.analytics.kpi_service import load_segments  # noqa: E402
from backend.routing.graph import RoadGraph  # noqa: E402
from backend.routing.live_state import fetch_live_state  # noqa: E402
from backend.routing.router import RouteAlternative, fastest_safe_routes  # noqa: E402

MAX_HOPS = 3


def route(
    conn: psycopg.Connection,
    origin: str,
    destination: str,
    geometry: str,
    now: datetime,
    vehicle_class: str | None = None,
    max_alternatives: int = 3,
) -> list[RouteAlternative]:
    segments = load_segments(conn, geometry)
    graph = RoadGraph(segments)
    topo = Topology(segments, MAX_HOPS)
    live_state = fetch_live_state(conn, geometry, topo, now)
    return fastest_safe_routes(
        graph, origin, destination, live_state, geometry, max_alternatives, vehicle_class
    )
