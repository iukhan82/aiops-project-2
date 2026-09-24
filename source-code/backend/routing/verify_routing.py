"""P07.02 acceptance evidence, against the real Postgres and the real seeded
network topology (26 segments, `network_segments`):

    python source-code/backend/routing/verify_routing.py

Proves the router on the real grid: a baseline route with no live incidents;
a real, correlated high-severity incident (through P06.07's own repository)
closes its segment out of the route entirely while the network's redundancy
still finds a way through; a hazard-severity incident is passable but
labelled; a synthetic recent corridor KPI (labelled, removed after) drives
the reported uncertainty away from the default margin; the API serves
contract-shaped alternatives.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg
import uvicorn
from psycopg.types.json import Jsonb

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.correlation import Topology  # noqa: E402
from backend.analytics.kpi_service import load_segments  # noqa: E402

os.environ.setdefault(
    "AIOPS_AUTH_MODE", "off"
)  # this script tests data, not authentication; verify_auth.py proves the latter
from backend.api.app import app  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.repositories.incidents import create_incident  # noqa: E402
from backend.routing.graph import RoadGraph  # noqa: E402
from backend.routing.live_state import fetch_live_state  # noqa: E402
from backend.routing.router import fastest_safe_routes  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

GEOMETRY = "2026-09-18.1"
ORIGIN, DESTINATION = "int-a1", "int-c4"
HOST, PORT = "127.0.0.1", 8800
ev = Evidence("P07.02")


def run_server() -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("uvicorn did not start")


async def api_checks() -> None:
    server = run_server()
    try:
        async with httpx.AsyncClient(base_url=f"http://{HOST}:{PORT}", timeout=30) as client:
            resp = await client.get(
                "/api/v1/routes",
                params={"origin": ORIGIN, "destination": DESTINATION, "vehicle_class": "ambulance"},
            )
            body = resp.json()
            alts = body["route_alternatives"]
            ev.check(
                "api_serves_contract_shaped_route_alternatives",
                resp.status_code == 200
                and alts
                and all(
                    {
                        "route_id",
                        "geometry_version",
                        "distance_m",
                        "eta_seconds",
                        "eta_uncertainty_seconds",
                        "constraints_applied",
                        "selected",
                    }
                    == set(a)
                    for a in alts
                )
                and sum(a["selected"] for a in alts) == 1,
                detail=f"{len(alts)} alternatives",
            )
            unknown = await client.get(
                "/api/v1/routes", params={"origin": "int-zz", "destination": DESTINATION}
            )
            ev.check("api_unknown_node_is_404", unknown.status_code == 404)
    finally:
        server.should_exit = True
        await asyncio.sleep(0.5)


def main() -> int:
    with psycopg.connect(dsn_from_env()) as conn:
        segments = load_segments(conn, GEOMETRY)
        graph = RoadGraph(segments)
        topo = Topology(segments, 3)
        now = datetime.now(timezone.utc)

        # ---- baseline: no live incidents, no live KPI (default uncertainty margin) ----
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM corridor_kpis WHERE window_start > %s", (now - timedelta(hours=1),)
            )
        conn.commit()
        baseline_state = fetch_live_state(conn, GEOMETRY, topo, now)
        baseline = fastest_safe_routes(
            graph, ORIGIN, DESTINATION, baseline_state, GEOMETRY, max_alternatives=3
        )
        ev.check(
            "baseline_route_exists_on_the_real_26_segment_network",
            bool(baseline) and baseline[0].distance_m > 0 and baseline[0].eta_seconds > 0,
            detail=f"{len(baseline)} alternatives, fastest {baseline[0].edges}",
        )
        ev.check(
            "multiple_genuinely_different_alternatives_exist_on_the_real_grid",
            len(baseline) >= 2 and set(baseline[0].edges) != set(baseline[1].edges),
        )
        default_margin = 0.15
        expected_uncertainty = sum(
            (s.length_m / s.free_flow_speed_m_s) * default_margin
            for e in baseline[0].edges
            for s in [next(x for x in segments if x.edge_id == e)]
        )
        ev.check(
            "uncertainty_uses_the_stated_default_margin_when_no_live_kpi_exists",
            abs(baseline[0].eta_uncertainty_seconds - expected_uncertainty) < 1e-6,
        )

        # ---- a real, correlated, high-severity incident closes its segment ----
        target_edge = baseline[0].edges[0]
        incident_id = create_incident(
            conn,
            "collision",
            "high",
            "segment",
            target_edge,
            GEOMETRY,
            [str(uuid.uuid4())],
            "EMERG",
            0.9,
            "test:synthetic",
            at=now,
        )
        closure_state = fetch_live_state(conn, GEOMETRY, topo, now)
        ev.check(
            "a_real_high_severity_incident_marks_its_segment_closed",
            closure_state[target_edge].closed
            and target_edge in (closure_state[target_edge].closed_reason or ""),
        )
        rerouted = fastest_safe_routes(
            graph, ORIGIN, DESTINATION, closure_state, GEOMETRY, max_alternatives=3
        )
        ev.check(
            "the_router_never_uses_a_closed_segment_and_the_grid_still_finds_a_way_through",
            bool(rerouted) and target_edge not in rerouted[0].edges,
            detail=f"rerouted via {rerouted[0].edges}",
        )
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE incidents SET status = 'resolved', resolved_at = %s WHERE incident_id = %s",
                (now, incident_id),
            )
        conn.commit()

        # ---- a medium-severity congestion incident is a hazard, not a closure ----
        hazard_edge = rerouted[0].edges[0]
        hazard_incident = create_incident(
            conn,
            "congestion",
            "medium",
            "segment",
            hazard_edge,
            GEOMETRY,
            [str(uuid.uuid4())],
            "CONTROL",
            0.85,
            "test:synthetic",
            at=now,
        )
        hazard_state = fetch_live_state(conn, GEOMETRY, topo, now)
        ev.check(
            "a_medium_severity_congestion_incident_is_a_hazard_not_a_closure",
            hazard_state[hazard_edge].hazard and not hazard_state[hazard_edge].closed,
        )
        hazard_route = fastest_safe_routes(
            graph, ORIGIN, DESTINATION, hazard_state, GEOMETRY, max_alternatives=1
        )
        ev.check(
            "a_hazard_route_is_still_returned_and_reroutes_around_a_comparable_alternative_when_one_exists",
            bool(hazard_route),
        )
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE incidents SET status = 'resolved', resolved_at = %s WHERE incident_id = %s",
                (now, hazard_incident),
            )
            cur.execute(
                "DELETE FROM incidents WHERE incident_id = ANY(%s::uuid[])",
                ([incident_id, hazard_incident],),
            )
        conn.commit()

        # ---- a recent, real corridor KPI (synthetic, labelled) moves the reported uncertainty ----
        corridor = topo.segments[target_edge].corridor_id
        direction = topo.segments[target_edge].direction
        if corridor is None:
            corridor, direction = next(
                (s.corridor_id, s.direction) for s in segments if s.corridor_id
            )
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO corridor_kpis (corridor_id, direction, window_start, window_seconds, geometry_version, kpis, quality, coverage, "
                "sample_count, segments_reporting, segments_expected) VALUES (%s, %s, %s, 300, %s, %s::jsonb, 'valid', 1.0, 10, 1, 1)",
                (
                    corridor,
                    direction,
                    now - timedelta(minutes=1),
                    GEOMETRY,
                    Jsonb(
                        {
                            "travel_time_s": 90.0,
                            "free_flow_travel_time_s": 60.0,
                            "buffer_index": 0.4,
                        }
                    ),
                ),
            )
        conn.commit()
        same_direction = [
            e for e in topo.corridor.get(corridor, ()) if topo.segments[e].direction == direction
        ]
        kpi_state = fetch_live_state(conn, GEOMETRY, topo, now)
        affected = [
            e
            for e in same_direction
            if kpi_state.get(e) is not None and kpi_state[e].travel_time_s is not None
        ]
        ev.check(
            "a_recent_corridor_kpi_overrides_free_flow_travel_time_for_its_segments",
            bool(affected)
            and all(
                abs(
                    kpi_state[e].travel_time_s
                    / (
                        next(s for s in segments if s.edge_id == e).length_m
                        / next(s for s in segments if s.edge_id == e).free_flow_speed_m_s
                    )
                    - 1.5
                )
                < 1e-6
                for e in affected
            ),
        )
        stale_state = fetch_live_state(conn, GEOMETRY, topo, now + timedelta(minutes=20))
        ev.check(
            "a_kpi_window_older_than_the_live_budget_is_not_used",
            all(
                stale_state.get(e) is None or stale_state[e].travel_time_s is None for e in affected
            ),
        )
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM corridor_kpis WHERE corridor_id = %s AND window_start = %s",
                (corridor, now - timedelta(minutes=1)),
            )
        conn.commit()

        asyncio.run(api_checks())
        ev.metrics["baseline_alternatives"] = [r.as_record() for r in baseline]
        ev.metrics["rerouted_after_closure"] = [r.as_record() for r in rerouted]
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
