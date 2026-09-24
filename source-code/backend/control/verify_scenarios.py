"""P07.10 acceptance evidence - the ambulance, fire and police scenarios end
to end, against the real Postgres, the real seeded network and the real pinned
SUMO image via TraCI:

    python source-code/backend/control/verify_scenarios.py

Each dispatch runs the whole platform path (`emergency_flow.run_dispatch`): a
call, a live-state-aware route and ETA (from corridor KPIs the simulator's own
pre-dispatch traffic produced), an assignment, a pre-emption command through
policy approval and real execution, and independent outcome verification -
plus a paired same-seed baseline with no pre-emption.

Measured, not assumed:
* ETA-01 (normal traffic) and ETA-02 (under a concurrent incident): predicted
  ETA vs the unit's simulated actual travel time, per scenario and pooled;
* ETA-03: paired pre-emption benefit, with the distribution (many pairs are
  a tie - the corridor is usually already green), a sign test, and the worst pair;
* SAFE-01: the independent signal-safety monitor over every step of every
  platform run - conflicting greens, yellow and pedestrian-clearance timing;
* traffic outcomes: cross-street and network-wide waiting, paired;
* failure limits: heavy congestion, no route, policy outage, adapter failure,
  stale evidence - each fail-closed, each with the measured consequence.

The evaluation seeds and departure times were fixed before these runs and
nothing was tuned on them: the router and the pre-emption mechanism were
finished (against other seeds) before this script ran.
"""

from __future__ import annotations

import math
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from unittest.mock import patch

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.control import preemption_service  # noqa: E402
from backend.control.emergency_flow import (  # noqa: E402
    DispatchResult,
    Scenario,
    cleanup_dispatches,
    run_dispatch,
)  # noqa: E402
from backend.emergency.cross_agency import (  # noqa: E402
    StagingStep,
    StagingViolation,
    advance_to_scene,
    plan_assignments,
    release_staged,
)  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.repositories import commands as cmd_repo  # noqa: E402
from backend.repositories import emergency as em_repo  # noqa: E402
from backend.repositories.incidents import create_incident  # noqa: E402
from backend.routing.route_service import route  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

GEOMETRY = "2026-09-18.1"
SCENARIOS = [
    Scenario(
        "ambulance",
        "ambulance",
        "cardiac_arrest",
        "critical",
        "ambulance-1",
        "ems-dispatch",
        ("als",),
        "int-a1",
        "int-a4",
        {"length": 6.5, "accel": 2.6, "decel": 4.5, "maxSpeed": 20.0},
    ),
    Scenario(
        "fire",
        "fire",
        "structure_fire",
        "critical",
        "engine-1",
        "fire-dispatch",
        ("suppression",),
        "int-c1",
        "int-b4",
        {"length": 11.0, "accel": 1.8, "decel": 4.0, "maxSpeed": 18.0},
    ),
    Scenario(
        "police",
        "police",
        "collision_blocking_lanes",
        "high",
        "patrol-1",
        "police-dispatch",
        ("traffic_control",),
        "int-a4",
        "int-c1",
        {"length": 4.8, "accel": 3.5, "decel": 5.0, "maxSpeed": 22.0},
    ),
]
TRIALS = [
    (201, 33.0),
    (202, 40.0),
    (203, 47.0),
    (204, 54.0),
    (205, 61.0),
]  # (seed, emergency-vehicle departure second)
ev = Evidence("P07.10")


def eta_stats(results: list[DispatchResult], actual_of=lambda r: r.actual_s) -> dict:
    errors = [abs(actual_of(r) - r.eta_s) / actual_of(r) for r in results]
    signed = [(actual_of(r) - r.eta_s) / actual_of(r) for r in results]
    covered = [abs(actual_of(r) - r.eta_s) <= r.eta_uncertainty_s for r in results]
    return {
        "n": len(results),
        "mae_pct": round(100 * mean(errors), 2),
        "worst_pct": round(100 * max(errors), 2),
        "mean_signed_error_pct": round(100 * mean(signed), 2),
        "within_stated_uncertainty": f"{sum(covered)}/{len(covered)}",
    }


def sign_test_p(improved: int, worse: int) -> float | None:
    """One-sided exact sign test on the untied pairs: P(at least `improved` wins out of n untied | no effect)."""
    n = improved + worse
    return None if n == 0 else round(sum(math.comb(n, k) for k in range(improved, n + 1)) / 2**n, 4)


def delete_incidents(conn: psycopg.Connection, incident_ids: list[str]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM incident_transitions WHERE incident_id = ANY(%s::uuid[])", (incident_ids,)
        )
        cur.execute("DELETE FROM incidents WHERE incident_id = ANY(%s::uuid[])", (incident_ids,))
    conn.commit()


def main() -> int:  # noqa: PLR0915 - one linear acceptance narrative
    kpi_keys: list = []
    call_ids: list[str] = []
    with psycopg.connect(dsn_from_env()) as conn:
        cleanup_dispatches(conn, "", [])
        now0 = datetime.now(timezone.utc)

        # ---- stale evidence first, while no live corridor KPI exists (SAFE-02/03 gate) ----
        stale = run_dispatch(conn, SCENARIOS[0], GEOMETRY, "stale-0", 401, 40.0, live_kpi=False)
        call_ids.append(stale.call_id)
        stale_cmd = cmd_repo.get_command(conn, stale.command_id)
        ev.check(
            "stale_evidence_denies_the_preemption_and_the_unit_drives_on_unassisted",
            stale.command_status == "denied"
            and stale_cmd["error_code"] == "stale_evidence"
            and not stale.preemption_applied
            and stale.actual_s == stale.baseline_s,
            detail=f"command {stale.command_status}/{stale_cmd['error_code']}, actual {stale.actual_s}s = baseline {stale.baseline_s}s",
        )

        # ---- normal traffic: three scenarios x five paired trials, the platform's own flow ----
        normal: dict[str, list[DispatchResult]] = {s.name: [] for s in SCENARIOS}
        for scenario in SCENARIOS:
            for i, (seed, depart) in enumerate(TRIALS):
                r = run_dispatch(
                    conn,
                    scenario,
                    GEOMETRY,
                    f"{scenario.name}-n{i}",
                    seed,
                    depart,
                    kpi_keys=kpi_keys,
                )
                if r.call_id:
                    call_ids.append(r.call_id)
                if r.status != "completed":
                    raise RuntimeError(f"{scenario.name} trial {i} {r.status}: {r.notes}")
                normal[scenario.name].append(r)
                print(
                    f"  {scenario.name} n{i}: eta {r.eta_s:.1f}s actual {r.actual_s}s baseline {r.baseline_s}s preempt {r.preempt_s}s cmd {r.command_status} "
                    f"outcome {r.outcome_classification} violations {r.safety_violations}",
                    flush=True,
                )
        all_normal = [r for rs in normal.values() for r in rs]
        stats_normal = {name: eta_stats(rs) for name, rs in normal.items()}
        pooled_normal = eta_stats(all_normal)
        ev.metrics["eta_01_normal_traffic"] = {
            "pooled": pooled_normal,
            "per_scenario": stats_normal,
            "against_the_unassisted_baseline_instead": {
                name: eta_stats(rs, lambda r: r.baseline_s) for name, rs in normal.items()
            },
            "note": "actual = the unit's simulated travel time on the platform's own flow (pre-emption executed); the second block scores the same ETAs against the paired no-pre-emption run",
        }
        ev.check(
            "eta_01_pooled_mae_within_15_percent_in_normal_traffic",
            pooled_normal["mae_pct"] <= 15.0,
            detail=str(pooled_normal),
        )
        ev.check(
            "eta_01_every_scenario_within_15_percent_individually",
            all(s["mae_pct"] <= 15.0 for s in stats_normal.values()),
            detail=str({k: v["mae_pct"] for k, v in stats_normal.items()}),
        )

        # ---- under a concurrent incident: the router must learn of it from the platform's own incident record ----
        incident_results: dict[str, list[DispatchResult]] = {s.name: [] for s in SCENARIOS}
        detours: dict[str, dict] = {}
        for scenario in SCENARIOS:
            normal_route = normal[scenario.name][0].route_edges
            blocked = normal_route[len(normal_route) // 2]
            incident_id = create_incident(
                conn,
                "collision",
                "high",
                "segment",
                blocked,
                GEOMETRY,
                [str(uuid.uuid4())],
                "EMERG",
                0.9,
                "test:synthetic",
                at=now0,
            )
            for i, (seed, depart) in enumerate(TRIALS):
                r = run_dispatch(
                    conn,
                    scenario,
                    GEOMETRY,
                    f"{scenario.name}-i{i}",
                    seed,
                    depart,
                    closed_edges=[blocked],
                    kpi_keys=kpi_keys,
                )
                if r.call_id:
                    call_ids.append(r.call_id)
                if r.status != "completed":
                    raise RuntimeError(f"{scenario.name} incident trial {i} {r.status}: {r.notes}")
                incident_results[scenario.name].append(r)
                print(
                    f"  {scenario.name} i{i}: eta {r.eta_s:.1f}s actual {r.actual_s}s baseline {r.baseline_s}s cmd {r.command_status} outcome {r.outcome_classification} "
                    f"violations {r.safety_violations}",
                    flush=True,
                )
            delete_incidents(conn, [incident_id])
            first = incident_results[scenario.name][0]
            detours[scenario.name] = {
                "blocked_edge": blocked,
                "normal_route": list(normal_route),
                "detour_route": list(first.route_edges),
                "normal_eta_s": round(normal[scenario.name][0].eta_s, 1),
                "detour_eta_s": round(first.eta_s, 1),
            }
        all_incident = [r for rs in incident_results.values() for r in rs]
        stats_incident = {name: eta_stats(rs) for name, rs in incident_results.items()}
        pooled_incident = eta_stats(all_incident)
        ev.metrics["eta_02_with_incident"] = {
            "pooled": pooled_incident,
            "per_scenario": stats_incident,
            "detours": detours,
        }
        ev.check(
            "every_incident_reroute_avoids_the_blocked_segment_it_learned_from_the_incident_record",
            all(
                d["blocked_edge"] not in d["detour_route"]
                and d["detour_route"] != d["normal_route"]
                for d in detours.values()
            ),
            detail=str(detours),
        )
        ev.check(
            "eta_02_pooled_mae_within_25_percent_under_an_active_incident",
            pooled_incident["mae_pct"] <= 25.0,
            detail=str(pooled_incident),
        )
        ev.check(
            "eta_02_every_scenario_within_25_percent_individually",
            all(s["mae_pct"] <= 25.0 for s in stats_incident.values()),
            detail=str({k: v["mae_pct"] for k, v in stats_incident.items()}),
        )

        # ---- ETA-03: paired benefit ----
        paired = [r for r in all_normal + all_incident if r.preemption_applied]
        reductions = [r.baseline_s - r.preempt_s for r in paired]
        improved, tied, worse = (
            sum(1 for x in reductions if x > 0),
            sum(1 for x in reductions if x == 0),
            sum(1 for x in reductions if x < 0),
        )
        by_scenario = {
            s.name: [r.baseline_s - r.preempt_s for r in paired if r.scenario == s.name]
            for s in SCENARIOS
        }
        ev.metrics["eta_03_paired_preemption_benefit"] = {
            "pairs": len(paired),
            "improved": improved,
            "tied": tied,
            "worse": worse,
            "mean_reduction_s": round(mean(reductions), 2),
            "median_reduction_s": median(reductions),
            "best_reduction_s": max(reductions),
            "worst_reduction_s": min(reductions),
            "mean_reduction_pct": round(
                100 * mean(x / r.baseline_s for x, r in zip(reductions, paired, strict=True)), 2
            ),
            "sign_test_one_sided_p": sign_test_p(improved, worse),
            "per_scenario_mean_reduction_s": {k: round(mean(v), 2) for k, v in by_scenario.items()},
            "note": "the corridor is usually already green when the vehicle arrives, so most natural-traffic pairs tie; the adverse-phase case (a cross-street green in progress) is in P07.07/P07.08's evidence",
        }
        ev.check(
            "eta_03_pre_emption_reduces_travel_time_on_average_and_never_worsens_a_pair_beyond_the_outcome_tolerance",
            mean(reductions) > 0 and min(reductions) >= -5.0,
            detail=str(ev.metrics["eta_03_paired_preemption_benefit"]),
        )
        ev.check(
            "eta_03_the_benefit_is_not_explained_by_chance_sign_test",
            sign_test_p(improved, worse) is not None and sign_test_p(improved, worse) < 0.05,
            detail=f"{improved} improved / {tied} tied / {worse} worse, one-sided p={sign_test_p(improved, worse)}",
        )

        # ---- independent outcome verification of every executed pre-emption ----
        classes: dict[str, int] = {}
        for r in paired:
            classes[r.outcome_classification] = classes.get(r.outcome_classification, 0) + 1
        ev.metrics["outcome_verification"] = classes
        ev.check(
            "every_executed_preemption_was_independently_verified_and_none_was_left_unknown",
            sum(classes.values()) == len(paired) and "unknown" not in classes,
            detail=str(classes),
        )

        # ---- SAFE-01 ----
        observations = sum(r.safety_observations for r in all_normal + all_incident + [stale])
        violations = sum(r.safety_violations for r in all_normal + all_incident + [stale])
        ev.metrics["safe_01"] = {
            "platform_runs": len(all_normal + all_incident) * 3 + 1,
            "intersection_state_observations": observations,
            "violations": violations,
            "rules": [
                "conflicting_green",
                "no_yellow_before_red",
                "short_yellow",
                "short_pedestrian_green",
                "short_pedestrian_clearance",
            ],
        }
        ev.check(
            "safe_01_zero_signal_safety_violations_over_every_step_of_every_platform_run",
            violations == 0 and observations > 100000,
            detail=f"{violations} violations over {observations} intersection-state observations",
        )

        # ---- traffic outcomes, paired ----
        with_traffic = [
            r
            for r in paired
            if r.baseline_traffic.get("network_mean_waiting_s") is not None
            and r.preempt_traffic.get("network_mean_waiting_s") is not None
        ]
        harm_pairs = [
            r
            for r in with_traffic
            if r.baseline_traffic.get("harm_edges_mean_waiting_s") is not None
        ]
        net_delta = [
            r.preempt_traffic["network_mean_waiting_s"]
            - r.baseline_traffic["network_mean_waiting_s"]
            for r in with_traffic
        ]
        harm_delta = [
            r.preempt_traffic["harm_edges_mean_waiting_s"]
            - r.baseline_traffic["harm_edges_mean_waiting_s"]
            for r in harm_pairs
        ]
        ev.metrics["traffic_outcomes"] = {
            "pairs": len(with_traffic),
            "window_s": 150,
            "network_mean_waiting_change_s": {
                "mean": round(mean(net_delta), 3),
                "worst_increase": round(max(net_delta), 3),
                "best_decrease": round(min(net_delta), 3),
            },
            "cross_street_mean_waiting_change_s": (
                {
                    "pairs": len(harm_pairs),
                    "mean": round(mean(harm_delta), 3),
                    "worst_increase": round(max(harm_delta), 3),
                    "best_decrease": round(min(harm_delta), 3),
                }
                if harm_pairs
                else None
            ),
            "note": "paired, same seed, the same 150 s window after the vehicle departs; positive = pre-emption made general traffic wait longer",
        }
        ev.check(
            "general_traffic_effect_was_measured_paired_and_is_reported",
            len(with_traffic) >= len(paired) * 0.9,
            detail=str(ev.metrics["traffic_outcomes"]),
        )

        # ---- failure limits ----
        limits: dict = {}
        heavy = []
        for seed, depart in ((301, 33.0), (302, 47.0), (303, 61.0)):
            r = run_dispatch(
                conn,
                SCENARIOS[0],
                GEOMETRY,
                f"heavy-{seed}",
                seed,
                depart,
                background_rate_per_s=1.1,
                kpi_keys=kpi_keys,
            )
            if r.call_id:
                call_ids.append(r.call_id)
            heavy.append(r)
        done = [r for r in heavy if r.status == "completed"]
        limits["heavy_congestion"] = {
            "background_rate_per_s": 1.1,
            "normal_rate_per_s": 0.6,
            "completed": f"{len(done)}/{len(heavy)}",
            "eta_error": eta_stats(done) if done else None,
            "unit_actual_s": [r.actual_s for r in done],
            "etas_s": [round(r.eta_s, 1) for r in done],
            "violations": sum(r.safety_violations for r in heavy),
        }
        ev.check(
            "heavy_congestion_is_run_and_its_eta_error_is_reported_not_hidden",
            len(done) >= 1,
            detail=str(limits["heavy_congestion"]),
        )

        no_route_incidents = []
        for edge in ("int-a3_int-a4", "int-b4_int-a4"):
            no_route_incidents.append(
                create_incident(
                    conn,
                    "collision",
                    "critical",
                    "segment",
                    edge,
                    GEOMETRY,
                    [str(uuid.uuid4())],
                    "EMERG",
                    0.9,
                    "test:synthetic",
                    at=now0,
                )
            )
        no_route = run_dispatch(
            conn, SCENARIOS[0], GEOMETRY, "noroute-0", 501, 40.0, live_kpi=False
        )
        delete_incidents(conn, no_route_incidents)
        if no_route.call_id:
            call_ids.append(no_route.call_id)
        call = em_repo.get_call(conn, no_route.call_id)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM commands WHERE idempotency_key = %s",
                ("verify-p0710-noroute-0",),
            )
            commands_for_noroute = cur.fetchone()[0]
        limits["no_route"] = {
            "status": no_route.status,
            "call_status": call["status"],
            "commands_created": commands_for_noroute,
        }
        ev.check(
            "no_open_route_is_reported_not_guessed_and_nothing_is_pre_empted",
            no_route.status == "no_route"
            and call["status"] == "dispatched"
            and commands_for_noroute == 0,
            detail=str(limits["no_route"]),
        )

        with patch(
            "backend.control.policy.build_context",
            side_effect=RuntimeError("policy backend unavailable"),
        ):
            outage = run_dispatch(
                conn, SCENARIOS[1], GEOMETRY, "outage-0", 601, 40.0, kpi_keys=kpi_keys
            )
        call_ids.append(outage.call_id)
        outage_cmd = cmd_repo.get_command(conn, outage.command_id)
        cmd_repo.expire_stale(conn, datetime.now(timezone.utc) + timedelta(minutes=10))
        conn.commit()
        outage_after = cmd_repo.get_command(conn, outage.command_id)
        limits["policy_outage"] = {
            "command_status": outage.command_status,
            "policy_decision": outage_cmd["policy_decision"],
            "later_status": outage_after["status"],
            "unit_actual_s": outage.actual_s,
            "eta_s": round(outage.eta_s, 1),
            "eta_error_pct": round(100 * abs(outage.actual_s - outage.eta_s) / outage.actual_s, 2),
        }
        ev.check(
            "policy_outage_fails_closed_the_command_never_executes_and_later_expires",
            outage.command_status == "requested"
            and outage_cmd["policy_decision"] == "policy_unavailable"
            and not outage.preemption_applied
            and outage_after["status"] == "expired",
            detail=str(limits["policy_outage"]),
        )

        with patch.object(
            preemption_service, "_run_corridor_scenario", side_effect=lambda request: None
        ):
            adapter_down = run_dispatch(
                conn, SCENARIOS[2], GEOMETRY, "adapter-0", 701, 40.0, kpi_keys=kpi_keys
            )
        call_ids.append(adapter_down.call_id)
        adapter_cmd = cmd_repo.get_command(conn, adapter_down.command_id)
        limits["adapter_failure"] = {
            "command_status": adapter_down.command_status,
            "error_code": adapter_cmd["error_code"],
            "retryable": adapter_cmd["error_retryable"],
            "unit_actual_s": adapter_down.actual_s,
            "baseline_s": adapter_down.baseline_s,
        }
        ev.check(
            "adapter_failure_fails_the_command_retryably_and_the_unit_continues_unassisted",
            adapter_down.command_status == "failed"
            and adapter_cmd["error_code"] == "adapter_unreachable"
            and adapter_cmd["error_retryable"] is True
            and not adapter_down.preemption_applied
            and adapter_down.actual_s == adapter_down.baseline_s,
            detail=str(limits["adapter_failure"]),
        )
        ev.metrics["failure_limits"] = limits | {
            "stale_evidence": {
                "command_status": stale.command_status,
                "error_code": stale_cmd["error_code"],
                "unit_actual_s": stale.actual_s,
            }
        }
        ev.check(
            "failure_runs_also_had_zero_signal_safety_violations",
            sum(r.safety_violations for r in heavy + [outage, adapter_down, no_route]) == 0,
        )

        # ---- cross-agency staged response on the fire call (P07.03): police, then fire, then EMS ----
        fire_call = em_repo.create_call(
            conn,
            "fire",
            "structure_fire",
            "critical",
            {"latitude": 31.52, "longitude": 74.36},
            GEOMETRY,
            "verified_dispatch",
            "simulated",
            "test:synthetic",
            at=now0,
        )
        call_ids.append(fire_call)
        em_repo.transition_call(conn, fire_call, "dispatched", "test:synthetic", at=now0)
        origins = {
            "patrol-2": ("police-dispatch", "int-a4", ["perimeter"], None),
            "engine-1": ("fire-dispatch", "int-c1", ["suppression"], "patrol-2"),
            "ambulance-2": ("ems-dispatch", "int-a1", ["als"], "engine-1"),
        }
        steps = [
            StagingStep(
                unit,
                agency,
                cap,
                route(conn, origin, "int-b4", GEOMETRY, now0)[0].as_record(),
                after,
            )
            for unit, (agency, origin, cap, after) in origins.items()
        ]
        assignments = plan_assignments(conn, fire_call, steps, "simulated", now0)
        arrived: dict[str, bool] = {}
        early = False
        try:  # EMS tries to go on scene before fire has arrived
            advance_to_scene(
                conn,
                assignments["ambulance-2"],
                "ambulance-2",
                "engine-1",
                arrived,
                now0 + timedelta(seconds=60),
            )
        except StagingViolation:
            early = True
        advance_to_scene(
            conn, assignments["patrol-2"], "patrol-2", None, arrived, now0 + timedelta(seconds=60)
        )
        advance_to_scene(
            conn,
            assignments["engine-1"],
            "engine-1",
            "patrol-2",
            arrived,
            now0 + timedelta(seconds=120),
        )
        release_staged(
            conn, assignments["ambulance-2"], "ambulance-2", now0 + timedelta(seconds=200), arrived
        )
        statuses = {
            a["unit_id"]: a["status"] for a in em_repo.assignments_for_call(conn, fire_call)
        }
        ev.check(
            "staged_response_holds_ems_back_until_fire_has_arrived_then_releases_it",
            early
            and statuses
            == {"patrol-2": "on_scene", "engine-1": "on_scene", "ambulance-2": "on_scene"},
            detail=str(statuses),
        )

        # ---- the recorded emergency timeline matches the simulated one ----
        sample = normal["ambulance"][0]
        assignment = em_repo.assignments_for_call(conn, sample.call_id)[0]
        recorded_s = (
            assignment["arrived_at"]
            - [
                h
                for h in em_repo.assignment_transition_history(
                    conn, str(assignment["assignment_id"])
                )
                if h["to_status"] == "en_route"
            ][0]["changed_at"]
        ).total_seconds()
        stored_eta = next(a for a in assignment["route_alternatives"] if a["selected"])[
            "eta_seconds"
        ]
        ev.check(
            "the_recorded_assignment_timeline_equals_the_simulated_travel_and_carries_the_predicted_eta",
            abs(recorded_s - sample.actual_s) < 1.0
            and abs(stored_eta - sample.eta_s) < 0.1
            and em_repo.get_call(conn, sample.call_id)["status"] == "cleared",
            detail=f"recorded {recorded_s:.1f}s vs simulated {sample.actual_s}s; stored ETA {stored_eta}s vs {sample.eta_s:.1f}s",
        )

        # ---- cleanup ----
        cleanup_dispatches(conn, "", [c for c in call_ids if c])
        with conn.cursor() as cur:
            for corridor, direction, at in kpi_keys:
                cur.execute(
                    "DELETE FROM corridor_kpis WHERE corridor_id = %s AND direction = %s AND window_start = %s",
                    (corridor, direction, at),
                )
        conn.commit()
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
