"""P07.08 acceptance evidence, against the real Postgres, the real seeded
network and the real pinned SUMO image via TraCI:

    python source-code/backend/control/verify_transit_priority.py

Proves the full pipeline: a real `transit_priority` command, approved by
P07.05's policy, executed against a real running SUMO instance. A paired,
same-seed comparison (baseline vs. priority, identical deterministic
starting signal state) measures **both** sides honestly - the transit
benefit and the cross-street traffic's own waiting time - and reports
whatever the real simulation shows, not what would be convenient to claim.
`int-b1` is used because it is a genuine multi-approach intersection with
real vehicular cross traffic (confirmed via TraCI's own `getControlledLinks`
during development - a plain through-intersection's minor phase in this
network is pedestrian-only, not a competing vehicle movement).
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.control.command_service import review_command  # noqa: E402
from backend.control.transit_priority_service import (  # noqa: E402
    execute_transit_priority,
    request_transit_priority,
)  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.repositories import commands as cmd_repo  # noqa: E402
from backend.roles import EXECUTOR  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

GEOMETRY = "2026-09-18.1"
ROUTE = ("int-a1_int-b1", "int-b1_int-b2", "int-b2_int-b3")
SCENARIO = {
    "seed": 5,
    "background_vehicles": 20,
    "transit_vehicle_depart_s": 10,
    "initial_phase": {"int-b1": 3},
    "initial_phase_duration_s": 60,
    "harm_edges": ["int-c1_int-b1"],
    "harm_window_s": 150,
    "cross_routes": [["int-c1_int-b1", "int-b1_int-a1"]],
}
ev = Evidence("P07.08")


def main() -> int:
    with psycopg.connect(dsn_from_env()) as conn:
        now = datetime.now(timezone.utc)
        with (
            conn.cursor() as cur
        ):  # SAFE-03 freshness gate needs a recent KPI window for corridor-b
            cur.execute(
                "INSERT INTO corridor_kpis (corridor_id, direction, window_start, window_seconds, geometry_version, kpis, quality, coverage, "
                "sample_count, segments_reporting, segments_expected) VALUES ('corridor-b', 'east', %s, 300, %s, %s::jsonb, 'valid', 1.0, 10, 1, 1) "
                "ON CONFLICT DO NOTHING",
                (
                    now - timedelta(minutes=1),
                    GEOMETRY,
                    Jsonb(
                        {
                            "delay_s": 10.0,
                            "queue_fraction": 0.2,
                            "travel_time_s": 60.0,
                            "free_flow_travel_time_s": 50.0,
                        }
                    ),
                ),
            )
        conn.commit()

        key = f"verify-p0708-priority-{now.timestamp()}"
        command_id, steps = request_transit_priority(
            conn, ROUTE, GEOMETRY, "operator:alice", now, key
        )
        ev.check(
            "a_real_route_yields_real_corridor_steps_at_a_genuine_multi_approach_intersection",
            [s.tl_id for s in steps] == ["int-b1", "int-b2"],
            detail=str([s.tl_id for s in steps]),
        )
        status = review_command(
            conn, command_id, "supervisor:bob", "supervisor", now + timedelta(seconds=2), GEOMETRY
        )
        ev.check(
            "the_transit_priority_command_is_approved_by_real_policy",
            status == "approved",
            detail=str(cmd_repo.get_command(conn, command_id)),
        )

        baseline_key = f"verify-p0708-baseline-{now.timestamp()}"
        baseline_cmd, baseline_steps = request_transit_priority(
            conn, ROUTE, GEOMETRY, "operator:alice", now, baseline_key
        )
        review_command(
            conn, baseline_cmd, "supervisor:bob", "supervisor", now + timedelta(seconds=2), GEOMETRY
        )
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE commands SET status='approved' WHERE command_id=%s", (baseline_cmd,)
            )
        conn.commit()
        baseline_result = execute_transit_priority(
            conn,
            baseline_cmd,
            baseline_steps,
            "bus-baseline",
            ROUTE,
            EXECUTOR,
            now + timedelta(seconds=3),
            extra_request={**SCENARIO, "mode": "baseline"},
        )
        priority_result = execute_transit_priority(
            conn,
            command_id,
            steps,
            "bus-priority",
            ROUTE,
            EXECUTOR,
            now + timedelta(seconds=3),
            extra_request=SCENARIO,
        )

        ev.check(
            "both_paired_runs_completed",
            baseline_result["transit_completed"] and priority_result["transit_completed"],
            detail=f"baseline={baseline_result.get('transit_travel_time_s')}s priority={priority_result.get('transit_travel_time_s')}s",
        )
        ev.check(
            "transit_priority_measurably_reduces_the_transit_vehicles_travel_time",
            priority_result["transit_travel_time_s"] < baseline_result["transit_travel_time_s"],
            detail=f"baseline {baseline_result['transit_travel_time_s']}s -> priority {priority_result['transit_travel_time_s']}s "
            f"({round(100 * (1 - priority_result['transit_travel_time_s'] / baseline_result['transit_travel_time_s']))}% faster)",
        )
        ev.check(
            "the_command_status_reflects_the_real_completed_corridor",
            cmd_repo.get_command(conn, command_id)["status"] == "executed",
        )

        # ---- the harm side: measured and reported honestly, whatever it shows ----
        ev.check(
            "cross_street_traffic_harm_was_actually_measured_not_assumed_zero",
            baseline_result["cross_traffic_samples"] > 0
            and priority_result["cross_traffic_samples"] > 0,
            detail=f"baseline mean wait {baseline_result['cross_traffic_mean_waiting_time_s']}s ({baseline_result['cross_traffic_samples']} samples), "
            f"priority mean wait {priority_result['cross_traffic_mean_waiting_time_s']}s ({priority_result['cross_traffic_samples']} samples)",
        )
        ev.check(
            "both_runs_sampled_the_same_fixed_window_so_the_harm_comparison_is_like_for_like",
            baseline_result["cross_traffic_samples"] == priority_result["cross_traffic_samples"],
            detail=f"{baseline_result['cross_traffic_samples']} vs {priority_result['cross_traffic_samples']} one-second samples",
        )
        ev.metrics["balanced_measurement"] = {
            "transit_benefit": {
                "baseline_travel_time_s": baseline_result["transit_travel_time_s"],
                "priority_travel_time_s": priority_result["transit_travel_time_s"],
            },
            "cross_traffic_harm": {
                "baseline_mean_wait_s": baseline_result["cross_traffic_mean_waiting_time_s"],
                "priority_mean_wait_s": priority_result["cross_traffic_mean_waiting_time_s"],
                "window_s": SCENARIO["harm_window_s"],
                "note": "the same 150 s window after the bus departs in both runs",
            },
        }

        # ---- safety: the independent monitor on the raw signal state at every step; movement served ----
        root = ET.parse(
            SOURCE_ROOT / "simulator" / "network" / "output" / "district.net.xml"
        ).getroot()

        def own_next(tl_id: str, phase_index: int) -> set[int]:
            logic = next(tl for tl in root.findall("tlLogic") if tl.get("id") == tl_id)
            phases = logic.findall("phase")
            nxt = phases[phase_index].get("next")
            return {int(x) for x in nxt.split()} if nxt else {(phase_index + 1) % len(phases)}

        def movement_green(step) -> bool:
            links = [
                int(c.get("linkIndex"))
                for c in root.findall("connection")
                if c.get("tl") == step.tl_id
                and c.get("from") == step.entry_edge
                and c.get("to") == step.exit_edge
            ]
            state = (
                next(tl for tl in root.findall("tlLogic") if tl.get("id") == step.tl_id)
                .findall("phase")[priority_result["target_phases"][step.tl_id]]
                .get("state")
            )
            return bool(links) and all(state[i] in "Gg" for i in links)

        ev.check(
            "the_target_phase_gives_the_buss_actual_movement_a_green",
            all(movement_green(s) for s in steps),
            detail=str(priority_result["target_phases"]),
        )
        ev.check(
            "signal_safety_monitor_reports_zero_violations_in_the_baseline_and_priority_runs",
            baseline_result["signal_safety"]["violation_count"] == 0
            and priority_result["signal_safety"]["violation_count"] == 0,
            detail=f"baseline {baseline_result['signal_safety']['violation_count']}, priority {priority_result['signal_safety']['violation_count']}",
        )
        violations = [
            f"{tl}: {b}->{a} not in {own_next(tl, b)}"
            for tl, transitions in priority_result["phase_transitions"].items()
            for b, a in transitions
            if a not in own_next(tl, b)
        ]
        ev.check(
            "every_phase_transition_during_priority_follows_the_programs_own_defined_sequence",
            not violations and any(priority_result["phase_transitions"].values()),
            detail=str(violations) or str(priority_result["phase_transitions"]),
        )
        ev.check(
            "no_safety_violation_was_reported_by_the_adapter_itself",
            priority_result["safety_violations"] == [],
        )

        with conn.cursor() as cur:
            cur.execute("DELETE FROM commands WHERE idempotency_key LIKE 'verify-p0708-%'")
            cur.execute(
                "DELETE FROM corridor_kpis WHERE corridor_id = 'corridor-b' AND direction = 'east' AND window_start = %s",
                (now - timedelta(minutes=1),),
            )
        conn.commit()
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
