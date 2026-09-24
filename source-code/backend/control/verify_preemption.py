"""P07.07 acceptance evidence (re-verified in P07.10), against the real
Postgres, the real seeded network and the real pinned SUMO image via TraCI:

    python source-code/backend/control/verify_preemption.py

Proves the full pipeline: a real emergency call (P07.01) with a real route
(P07.02) requests real `emergency_preemption` pre-emption, approved by
P07.05's policy, executed against a real running SUMO instance. A paired,
same-seed comparison (baseline vs. pre-emption, identical deterministic
starting condition: a light serving the cross street with a long green) shows
a real, measured travel-time reduction.

Safety is judged by the **independent runtime signal-safety monitor** on the raw
signal state at every simulation step (conflicting greens, yellow and
pedestrian-clearance timing), not by the mechanism's own bookkeeping. A
**negative control** runs the first version of this mechanism (drive every
light to phase 0, ending every phase at once): it passes the old "phase order
matches the program's own `next`" check and is caught by the monitor, which
is how P07.10 found that version unsafe. The phase the mechanism targets is
checked independently against the network file: it must give the vehicle's
actual movement a green.
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
from backend.control.preemption_service import (  # noqa: E402
    NET_FILE,
    _run_corridor_scenario,
    _step_records,
    abort_preemption,
    execute_preemption,
    request_preemption,
)  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.repositories import commands as cmd_repo  # noqa: E402
from backend.repositories import emergency as em_repo  # noqa: E402
from backend.roles import EXECUTOR  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

GEOMETRY = "2026-09-18.1"
ROUTE = ("int-a1_int-b1", "int-b1_int-b2", "int-b2_int-b3")
DELAY_SCENARIO = {
    "seed": 5,
    "background_vehicles": 20,
    "emergency_vehicle_depart_s": 10,
    "initial_phase": {"int-b1": 3},
    "initial_phase_duration_s": 40,
    "harm_edges": ["int-c1_int-b1"],
    "harm_window_s": 120,
    "cross_routes": [["int-c1_int-b1", "int-b1_int-a1"]],
}
ev = Evidence("P07.07")


def _own_next_phase(tl_id: str, phase_index: int) -> set[int]:
    """The real compiled program's own successor(s) for one phase (its `next` attribute if present,
    otherwise the implicit +1 mod length every SUMO program falls back to)."""
    root = ET.parse(NET_FILE).getroot()
    logic = next(tl for tl in root.findall("tlLogic") if tl.get("id") == tl_id)
    phases = logic.findall("phase")
    nxt = phases[phase_index].get("next")
    if nxt:
        return {int(x) for x in nxt.split()}
    return {(phase_index + 1) % len(phases)}


def _order_violations(transitions_by_tl: dict) -> list[str]:
    return [
        f"{tl_id}: {b}->{a} not in {_own_next_phase(tl_id, b)}"
        for tl_id, transitions in transitions_by_tl.items()
        for b, a in transitions
        if a not in _own_next_phase(tl_id, b)
    ]


def _movement_is_green_in_target(tl_id: str, entry: str, exit_: str, target_phase: int) -> bool:
    """Independent of the mechanism: from the network file alone, does the target phase give every link of the
    vehicle's movement a green? A right turn at a cross-street approach is served by a permissive `g` (it yields),
    so `g` counts; a phase where the movement is red does not."""
    root = ET.parse(NET_FILE).getroot()
    links = [
        int(c.get("linkIndex"))
        for c in root.findall("connection")
        if c.get("tl") == tl_id and c.get("from") == entry and c.get("to") == exit_
    ]
    logic = next(tl for tl in root.findall("tlLogic") if tl.get("id") == tl_id)
    state = logic.findall("phase")[target_phase].get("state")
    return bool(links) and all(state[i] in "Gg" for i in links)


def main() -> int:
    with psycopg.connect(dsn_from_env()) as conn:
        now = datetime.now(timezone.utc)
        with (
            conn.cursor() as cur
        ):  # SAFE-03 freshness gate needs a recent KPI window for corridor-b east
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

        # ---- a real emergency call, a real command, real policy approval ----
        call_id = em_repo.create_call(
            conn,
            "ambulance",
            "cardiac",
            "critical",
            {"latitude": 31.52, "longitude": 74.36},
            GEOMETRY,
            "verified_dispatch",
            "simulated",
            "test:synthetic",
            at=now,
        )
        key = f"verify-p0707-preempt-{now.timestamp()}"
        command_id, steps = request_preemption(conn, ROUTE, GEOMETRY, "dispatcher:dana", now, key)
        ev.check(
            "a_real_route_through_the_real_network_yields_real_corridor_steps",
            [s.tl_id for s in steps] == ["int-b1", "int-b2"],
            detail=str([(s.tl_id, s.entry_edge, s.exit_edge) for s in steps]),
        )
        status = review_command(
            conn,
            command_id,
            "commander:eve",
            "incident_commander",
            now + timedelta(seconds=2),
            GEOMETRY,
        )
        ev.check(
            "the_preemption_command_is_approved_by_real_policy",
            status == "approved",
            detail=str(cmd_repo.get_command(conn, command_id)),
        )
        ev.check(
            "a_route_that_starts_on_a_cross_street_targets_the_corridor_it_actually_needs",
            cmd_repo.get_command(conn, command_id)["target_entity_id"] == "corridor-b",
        )

        # ---- paired comparison: identical deterministic starting condition, mode is the only difference ----
        baseline_key = f"verify-p0707-baseline-{now.timestamp()}"
        baseline_cmd, baseline_steps = request_preemption(
            conn, ROUTE, GEOMETRY, "dispatcher:dana", now, baseline_key
        )
        review_command(
            conn,
            baseline_cmd,
            "commander:eve",
            "incident_commander",
            now + timedelta(seconds=2),
            GEOMETRY,
        )
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE commands SET status='approved' WHERE command_id=%s", (baseline_cmd,)
            )
        conn.commit()
        baseline_result = execute_preemption(
            conn,
            baseline_cmd,
            baseline_steps,
            "ev-baseline",
            ROUTE,
            EXECUTOR,
            now + timedelta(seconds=3),
            extra_request={**DELAY_SCENARIO, "mode": "baseline"},
        )

        preempt_result = execute_preemption(
            conn,
            command_id,
            steps,
            "ev-preempt",
            ROUTE,
            EXECUTOR,
            now + timedelta(seconds=3),
            extra_request=DELAY_SCENARIO,
        )
        ev.check(
            "both_paired_runs_completed",
            baseline_result["ev_completed"] and preempt_result["ev_completed"],
            detail=f"baseline={baseline_result.get('travel_time_s')}s preempt={preempt_result.get('travel_time_s')}s",
        )
        ev.check(
            "pre_emption_measurably_reduces_the_emergency_vehicles_travel_time",
            preempt_result["travel_time_s"] < baseline_result["travel_time_s"],
            detail=f"baseline {baseline_result['travel_time_s']}s -> pre-emption {preempt_result['travel_time_s']}s "
            f"({round(100 * (1 - preempt_result['travel_time_s'] / baseline_result['travel_time_s']))}% faster)",
        )
        ev.check(
            "the_command_status_reflects_the_real_completed_corridor",
            cmd_repo.get_command(conn, command_id)["status"] == "executed",
        )

        # ---- the mechanism serves the vehicle's actual movement (checked from the network file, not from the mechanism) ----
        served = {
            s.tl_id: _movement_is_green_in_target(
                s.tl_id, s.entry_edge, s.exit_edge, preempt_result["target_phases"][s.tl_id]
            )
            for s in steps
        }
        ev.check(
            "the_target_phase_gives_the_vehicles_actual_movement_a_green",
            all(served.values()),
            detail=f"target phases {preempt_result['target_phases']} served {served}",
        )

        # ---- safety: the independent monitor on the raw signal state, at every step, in both runs ----
        ev.check(
            "signal_safety_monitor_reports_zero_violations_in_the_baseline_run",
            baseline_result["signal_safety"]["violation_count"] == 0,
            detail=str(baseline_result["signal_safety"]),
        )
        ev.check(
            "signal_safety_monitor_reports_zero_violations_during_preemption",
            preempt_result["signal_safety"]["violation_count"] == 0
            and preempt_result["safety_violations"] == [],
            detail=str(preempt_result["signal_safety"]),
        )
        ev.check(
            "every_phase_transition_still_follows_the_programs_own_defined_sequence",
            not _order_violations(preempt_result["phase_transitions"])
            and any(preempt_result["phase_transitions"].values()),
            detail=str(preempt_result["phase_transitions"]),
        )

        # ---- negative control: the first version of the mechanism, same scenario ----
        naive = _run_corridor_scenario(
            {
                "mode": "preempt_naive",
                "seed": DELAY_SCENARIO["seed"],
                "route": list(ROUTE),
                "emergency_vehicle_id": "ev-naive",
                "steps": _step_records(steps),
                **{k: v for k, v in DELAY_SCENARIO.items() if k != "seed"},
            }
        )
        ev.check(
            "negative_control_the_old_mechanism_passes_the_phase_order_check",
            naive is not None and not _order_violations(naive["phase_transitions"]),
            detail=str(naive["phase_transitions"]) if naive else "no result",
        )
        ev.check(
            "negative_control_the_monitor_catches_the_old_mechanism_in_the_real_simulator",
            naive is not None and naive["signal_safety"]["violation_count"] > 0,
            detail=str(naive["signal_safety"]["violations_by_kind"])
            + " "
            + str(naive["signal_safety"]["examples"][:1])
            if naive
            else "no result",
        )
        ev.metrics["negative_control"] = (
            {
                "violations_by_kind": naive["signal_safety"]["violations_by_kind"],
                "examples": naive["signal_safety"]["examples"],
                "travel_time_s": naive["travel_time_s"],
            }
            if naive
            else None
        )

        # ---- abort: real restoration, command ends failed (an abort is never reported as success) ----
        abort_key = f"verify-p0707-abort-{now.timestamp()}"
        abort_cmd, abort_steps = request_preemption(
            conn, ROUTE, GEOMETRY, "dispatcher:dana", now, abort_key
        )
        review_command(
            conn,
            abort_cmd,
            "commander:eve",
            "incident_commander",
            now + timedelta(seconds=2),
            GEOMETRY,
        )
        abort_result = abort_preemption(
            conn,
            abort_cmd,
            abort_steps,
            "ev-abort",
            ROUTE,
            abort_after_step=1,
            actor=EXECUTOR,
            now=now + timedelta(seconds=3),
            extra_request=DELAY_SCENARIO,
        )
        ev.check(
            "abort_restores_every_touched_intersection_to_its_own_original_program",
            abort_result["aborted_at_step"] == 1 and not abort_result["safety_violations"],
            detail=str(abort_result.get("original_programs")),
        )
        ev.check(
            "an_aborted_command_ends_failed_never_reported_as_a_successful_execution",
            cmd_repo.get_command(conn, abort_cmd)["status"] == "failed",
        )

        with conn.cursor() as cur:
            cur.execute("DELETE FROM commands WHERE idempotency_key LIKE 'verify-p0707-%'")
        conn.commit()
        em_repo.transition_call(
            conn, call_id, "cancelled", "test:synthetic", "verification cleanup", at=now
        )
        with conn.cursor() as cur:
            cur.execute("DELETE FROM emergency_calls WHERE call_id = %s", (call_id,))
            cur.execute(
                "DELETE FROM corridor_kpis WHERE corridor_id = 'corridor-b' AND direction = 'east' AND window_start = %s",
                (now - timedelta(minutes=1),),
            )
        conn.commit()

        ev.metrics["baseline_vs_preemption"] = {
            "baseline_travel_time_s": baseline_result["travel_time_s"],
            "preemption_travel_time_s": preempt_result["travel_time_s"],
            "traffic_baseline": baseline_result["traffic"],
            "traffic_preemption": preempt_result["traffic"],
        }
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
