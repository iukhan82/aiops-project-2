"""P07.09 acceptance evidence, against the real Postgres, the real seeded
network and the real pinned SUMO image via TraCI:

    python source-code/backend/control/verify_outcomes.py

Proves independent outcome verification end to end on real infrastructure:

* thresholds are *calibrated* on separate no-action runs (the measured
  window-to-window noise), never picked to make a scenario pass;
* an unsafe action (closing a busy edge without rerouting) is measured by a
  real before/after window in one live SUMO session, classified `unsafe`,
  the command moves `executed -> rolled_back`, and the closure is physically
  undone - read back from TraCI, then watched recovering in a further window;
* an action with no real network-level effect is `ineffective` (no rollback);
* a paired same-seed transit-priority run is `effective` (real travel-time gain);
* a telemetry gap is `unknown` and escalated, never defaulted to effective;
* a rollback that cannot be confirmed is not claimed - the command stays
  `executed` and the outcome is escalated;
* the verifier must differ from requester, approver and executor;
* LAT-04 (approval -> adapter acknowledgement) is measured for a warm adapter
  session and, separately and honestly, for P07.06's cold one-container-per-
  action path.

The KPI-fixture rows used for the non-actuating and telemetry-gap cases are
labelled as fixtures in the evidence; every simulator-derived number comes from
TraCI.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg
import uvicorn
from jsonschema import Draft202012Validator
from psycopg.types.json import Jsonb

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

os.environ.setdefault(
    "AIOPS_AUTH_MODE", "off"
)  # this script tests data, not authentication; verify_auth.py proves the latter
from backend.api.app import app  # noqa: E402
from backend.control.command_service import request_command, review_command  # noqa: E402
from backend.control.live_session import LiveSession, execute_command_live  # noqa: E402
from backend.control.outcome_verification import Metric, mean_corridor_metric, verify_and_rollback  # noqa: E402
from backend.control.simulator_adapters import execute_command  # noqa: E402
from backend.control.transit_priority_service import (  # noqa: E402
    execute_transit_priority,
    request_transit_priority,
)  # noqa: E402
from backend.control.verify_simulator_adapters import fresh_signal_evidence  # noqa: E402
from backend.control.verify_transit_priority import ROUTE as TRANSIT_ROUTE  # noqa: E402
from backend.control.verify_transit_priority import SCENARIO as TRANSIT_SCENARIO  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.repositories import commands as cmd_repo  # noqa: E402
from backend.repositories import outcomes as outcome_repo  # noqa: E402
from backend.roles import EXECUTOR, OUTCOME_VERIFIER, NotPermitted  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

GEOMETRY = "2026-09-18.1"
REQUESTER, APPROVER, VERIFIER = "operator:alice", "supervisor:bob", OUTCOME_VERIFIER
DEMAND = {"vehicles": 650, "span_s": 900}
WARMUP_S, WINDOW_S = 120, 120
CONTROLLED = [f"int-{c}{i}" for c in "abc" for i in range(1, 5)]
SCHEMA = json.loads(
    (SOURCE_ROOT / "contracts" / "outcome" / "v1" / "schema.json").read_text(encoding="utf-8")
)
HOST, PORT = "127.0.0.1", 8810
STAMP = int(time.time() * 1000)
ev = Evidence("P07.09")


def utc() -> datetime:
    return datetime.now(timezone.utc)


FIXTURE_KPI_ROWS: list[tuple[str, str, datetime]] = []


def kpi_row(
    conn: psycopg.Connection, corridor: str, direction: str, at: datetime, delay_s: float
) -> None:
    FIXTURE_KPI_ROWS.append((corridor, direction, at))
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO corridor_kpis (corridor_id, direction, window_start, window_seconds, geometry_version, kpis, quality, coverage, "
            "sample_count, segments_reporting, segments_expected) VALUES (%s, %s, %s, 300, %s, %s::jsonb, 'valid', 1.0, 10, 1, 1) ON CONFLICT DO NOTHING",
            (
                corridor,
                direction,
                at,
                GEOMETRY,
                Jsonb(
                    {
                        "delay_s": delay_s,
                        "queue_fraction": 0.2,
                        "travel_time_s": 60.0,
                        "free_flow_travel_time_s": 50.0,
                    }
                ),
            ),
        )
    conn.commit()


def approved_command(
    conn: psycopg.Connection,
    tag: str,
    action_type: str,
    adapter: str,
    entity: str,
    role: str,
    at: datetime | None = None,
) -> str:
    """`at` places the request and its review on a chosen timeline (used by the replay-timeline cases, where
    the KPI rows the verifier reads live in a coherent past); the default is the real present."""
    cid, _ = request_command(
        conn,
        f"verify-p0709-{tag}-{STAMP}",
        action_type,
        adapter,
        entity,
        REQUESTER,
        at or utc(),
        "operator",
    )
    status = review_command(
        conn, cid, APPROVER, role, (at + timedelta(seconds=5)) if at else utc(), GEOMETRY
    )
    if status != "approved":
        raise RuntimeError(
            f"{tag}: policy did not approve ({status}): {cmd_repo.get_command(conn, cid)}"
        )
    return cid


def stub_executed(
    conn: psycopg.Connection, command_id: str, at: datetime | None = None, actor: str = EXECUTOR
) -> None:
    """Moves an approved command to `executed` through the real state machine without a simulator run - used
    only where the case under test is the verifier's own decision logic, not the actuation."""
    cmd_repo.transition_command(
        conn,
        command_id,
        "executing",
        actor,
        "stubbed execution (decision-logic test)",
        at=at or utc(),
    )
    cmd_repo.transition_command(
        conn,
        command_id,
        "executed",
        actor,
        "stubbed execution (decision-logic test)",
        at=at or utc(),
        acknowledged=True,
    )


def timed_advance(
    session: LiveSession, seconds: int
) -> tuple[Metric, tuple[datetime, datetime], dict]:
    start = utc()
    resp = session.call("advance", seconds=seconds)
    return (
        Metric("total_waiting_time", resp["mean_total_waiting_s"], "vehicle_s"),
        (start, utc()),
        resp,
    )


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def calibrate_noise() -> dict:
    """Window-to-window change of the metric with NO action, on seeds the scenarios never use."""
    deltas: list[float] = []
    for seed in (3, 4, 5):
        with LiveSession(seed=seed, **DEMAND) as session:
            session.call("advance", seconds=WARMUP_S)
            values = [
                session.call("advance", seconds=WINDOW_S)["mean_total_waiting_s"] for _ in range(4)
            ]
        deltas += [b - a for a, b in zip(values, values[1:], strict=False)]
    band = max(abs(d) for d in deltas)
    return {
        "deltas": [round(d, 2) for d in deltas],
        "noise_band": round(band, 2),
        "threshold": math.ceil(2 * band),
    }


def transit_pair(conn: psycopg.Connection) -> tuple[str, dict, dict, tuple, tuple]:
    now = utc()
    kpi_row(
        conn, "corridor-b", "east", now - timedelta(minutes=2), 10.0
    )  # SAFE-03 freshness for the corridor (removed at the end)
    cmd, steps = request_transit_priority(
        conn, TRANSIT_ROUTE, GEOMETRY, REQUESTER, now, f"verify-p0709-transit-priority-{STAMP}"
    )
    review_command(conn, cmd, APPROVER, "supervisor", utc(), GEOMETRY)
    base_cmd, base_steps = request_transit_priority(
        conn, TRANSIT_ROUTE, GEOMETRY, REQUESTER, now, f"verify-p0709-transit-baseline-{STAMP}"
    )
    review_command(conn, base_cmd, APPROVER, "supervisor", utc(), GEOMETRY)
    with (
        conn.cursor() as cur
    ):  # the baseline run is a counterfactual harness, not a real command to be verified
        cur.execute("UPDATE commands SET status='approved' WHERE command_id=%s", (base_cmd,))
    conn.commit()
    base_window_start = utc()
    baseline = execute_transit_priority(
        conn,
        base_cmd,
        base_steps,
        "bus-baseline",
        TRANSIT_ROUTE,
        EXECUTOR,
        extra_request={**TRANSIT_SCENARIO, "mode": "baseline"},
    )
    base_window = (base_window_start, utc())
    prio_window_start = utc()
    priority = execute_transit_priority(
        conn, cmd, steps, "bus-priority", TRANSIT_ROUTE, EXECUTOR, extra_request=TRANSIT_SCENARIO
    )
    return cmd, baseline, priority, base_window, (prio_window_start, utc())


async def api_checks(unsafe_outcome: str, unknown_outcome: str, unsafe_command: str) -> None:
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        if server.started:
            break
        await asyncio.sleep(0.1)
    try:
        validator = Draft202012Validator(SCHEMA)
        async with httpx.AsyncClient(base_url=f"http://{HOST}:{PORT}", timeout=30) as client:
            page = (await client.get("/api/v1/outcomes", params={"limit": 500})).json()
            errors = [e.message for item in page["items"] for e in validator.iter_errors(item)]
            ev.check(
                "api_outcomes_are_contract_valid",
                bool(page["items"]) and not errors,
                detail=f"{len(page['items'])} records; {errors[:1]}",
            )
            by_class = (
                await client.get(
                    "/api/v1/outcomes", params={"classification": "unsafe", "limit": 500}
                )
            ).json()
            ev.check(
                "api_filters_by_classification",
                by_class["items"]
                and all(o["classification"] == "unsafe" for o in by_class["items"]),
            )
            by_cmd = (
                await client.get("/api/v1/outcomes", params={"command_id": unsafe_command})
            ).json()
            ev.check(
                "api_filters_by_command",
                len(by_cmd["items"]) == 1 and by_cmd["items"][0]["outcome_id"] == unsafe_outcome,
            )
            first = (await client.get("/api/v1/outcomes", params={"limit": 1})).json()
            second = (
                await client.get(
                    "/api/v1/outcomes", params={"limit": 1, "cursor": first["next_cursor"]}
                )
            ).json()
            ev.check(
                "api_paginates_with_a_cursor",
                first["next_cursor"]
                and second["items"]
                and second["items"][0]["outcome_id"] != first["items"][0]["outcome_id"],
            )
            detail = (await client.get(f"/api/v1/outcomes/{unsafe_outcome}")).json()
            ev.check(
                "api_detail_exposes_thresholds_and_the_physical_undo_evidence",
                detail["detail"]["undo"]["kind"] == "simulator_undo"
                and detail["detail"]["undo"]["restored"] is True
                and "thresholds" in detail["detail"],
                detail=str(detail["detail"].get("undo"))[:200],
            )
            unknown = (await client.get(f"/api/v1/outcomes/{unknown_outcome}")).json()
            ev.check(
                "api_unknown_outcome_carries_its_escalation_reason",
                unknown["outcome"]["classification"] == "unknown"
                and unknown["outcome"]["escalation_reason"],
            )
            ev.check(
                "api_unknown_id_is_404",
                (
                    await client.get(f"/api/v1/outcomes/{'0' * 8}-0000-0000-0000-{'0' * 12}")
                ).status_code
                == 404,
            )
            ev.check(
                "api_bad_command_id_is_400",
                (await client.get("/api/v1/outcomes", params={"command_id": "nope"})).status_code
                == 400,
            )
    finally:
        server.should_exit = True
        await asyncio.sleep(0.5)


def main() -> int:  # noqa: PLR0915 - one linear acceptance narrative
    with psycopg.connect(dsn_from_env()) as conn:
        now = utc()
        for intersection in CONTROLLED:
            fresh_signal_evidence(conn, f"signal-{intersection}", intersection, now)
        kpi_row(conn, "corridor-b", "east", now - timedelta(minutes=1), 10.0)
        kpi_row(conn, "corridor-c", "east", now - timedelta(minutes=1), 10.0)

        # ---- 1. calibrate the noise band on separate no-action runs ----
        noise = calibrate_noise()
        threshold = noise["threshold"]
        ev.metrics["noise_calibration"] = {
            **noise,
            "note": "seeds 3-5, no action, four consecutive 120 s windows each; threshold = ceil(2 x max |delta|)",
        }
        ev.check(
            "thresholds_are_derived_from_measured_no_action_noise",
            threshold > 0,
            detail=f"noise band {noise['noise_band']} -> threshold {threshold}",
        )

        # ---- 2. unsafe: a real closure, measured, rolled back, physically undone ----
        edge = "int-b2_int-b3"
        cmd_unsafe = approved_command(
            conn, "diversion-unsafe", "diversion", "diversion_adapter", edge, "supervisor"
        )
        with LiveSession(seed=1, **DEMAND) as session:
            session.call("advance", seconds=WARMUP_S)
            pre, pre_window, pre_raw = timed_advance(session, WINDOW_S)
            result = execute_command_live(conn, cmd_unsafe, session, {}, EXECUTOR)
            ev.check(
                "the_closure_was_really_applied_in_the_live_simulator",
                result["acknowledged"]
                and all(
                    "passenger" in v for v in result["observation"]["observed_disallowed"].values()
                ),
                detail=str(result["observation"].get("lanes_closed")),
            )
            post, post_window, post_raw = timed_advance(session, WINDOW_S)
            before_undo = session.call("state", edge_id=edge)
            outcome_unsafe, verdict = verify_and_rollback(
                conn,
                cmd_unsafe,
                pre,
                post,
                pre_window,
                post_window,
                VERIFIER,
                utc(),
                threshold,
                threshold,
                undo=lambda cmd: session.call("undo", idempotency_key=cmd["idempotency_key"]),
                extra_detail={
                    "pre_sim_window_s": [pre_raw["sim_time"] - WINDOW_S, pre_raw["sim_time"]],
                    "post_sim_window_s": [post_raw["sim_time"] - WINDOW_S, post_raw["sim_time"]],
                },
            )
            after_undo = session.call("state", edge_id=edge)
            recovery, _, recovery_raw = timed_advance(session, WINDOW_S)
        stored = cmd_repo.get_command(conn, cmd_unsafe)
        history = cmd_repo.transition_history(conn, cmd_unsafe)
        detail = outcome_repo.get_outcome_detail(conn, outcome_unsafe)
        ev.check(
            "real_closure_worsens_waiting_far_beyond_the_noise_band",
            verdict.classification == "unsafe" and post.value - pre.value > 10 * threshold,
            detail=f"pre {pre.value} -> post {post.value} vehicle_s (threshold {threshold})",
        )
        ev.check(
            "unsafe_command_moved_executed_to_rolled_back_by_the_verifier",
            stored["status"] == "rolled_back"
            and stored["rolled_back_at"] is not None
            and history[-1]["from_status"] == "executed"
            and history[-1]["to_status"] == "rolled_back"
            and history[-1]["changed_by"] == VERIFIER,
        )
        ev.check(
            "the_lanes_were_physically_closed_before_and_reopened_after_the_undo_read_back_from_traci",
            all("passenger" in v for v in before_undo["lanes"].values())
            and after_undo["lanes"] == detail["undo"]["evidence"]["original_disallowed"]
            and not any("passenger" in v for v in after_undo["lanes"].values()),
            detail=f"before {before_undo['lanes']} after {after_undo['lanes']}",
        )
        ev.check(
            "waiting_measurably_recovers_after_the_rollback",
            recovery.value < post.value / 2,
            detail=f"post {post.value} -> recovery {recovery.value} vehicle_s (baseline {pre.value})",
        )
        ev.check(
            "outcome_records_rollback_and_the_undo_evidence",
            outcome_repo.get_outcome(conn, outcome_unsafe)["rollback_triggered"] is True
            and detail["undo"]["kind"] == "simulator_undo"
            and detail["undo"]["restored"] is True,
        )
        ev.metrics["unsafe_scenario"] = {
            "pre_waiting": pre.value,
            "post_waiting": post.value,
            "recovery_waiting_after_rollback": recovery.value,
            "vehicles_in_network": {
                "pre": pre_raw["vehicles_in_network"],
                "post": post_raw["vehicles_in_network"],
                "recovery": recovery_raw["vehicles_in_network"],
            },
        }

        # ---- 3. a second session: a real ineffective action, then two cases read from corridor_kpis on replay timelines ----
        # Fixture timelines: request, review, execution, windows and verification all sit on one coherent past
        # timeline (T), so the "post" window really follows the execution. The KPI rows are fixtures; the verifier
        # reads them back through the same query it would use on live data.
        t_vms, t_gap = now - timedelta(hours=3), now - timedelta(hours=6)
        for base in (t_vms, t_gap):
            kpi_row(conn, "corridor-c", "east", base - timedelta(minutes=8), 20.0)
            kpi_row(
                conn, "corridor-c", "east", base - timedelta(minutes=1), 20.0
            )  # also the freshness row the policy needs at review time
        kpi_row(
            conn, "corridor-c", "east", t_vms + timedelta(minutes=3), 90.0
        )  # a post-window row for the VMS case only: the gap case has none

        def replay(
            base: datetime,
        ) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime], datetime]:
            return (
                (base - timedelta(minutes=10), base),
                (base + timedelta(minutes=1), base + timedelta(minutes=11)),
                base + timedelta(minutes=12),
            )

        # By this point in the script real SUMO sessions (the unsafe-action scenario, noise calibration) have run for several
        # minutes, so int-a2's evidence seeded near the top may already be past FRESH_EVIDENCE_MAX_AGE_S; refresh it right
        # before this request rather than relying on how long everything before it happened to take.
        fresh_signal_evidence(conn, "signal-int-a2", "int-a2", utc())
        cmd_ineff = approved_command(
            conn,
            "signal-ineffective",
            "signal_plan_change",
            "signal_controller_adapter",
            "int-a2",
            "supervisor",
        )
        cmd_gap = approved_command(
            conn,
            "vms-gap",
            "variable_message_sign",
            "vms_adapter",
            "int-c2_int-c3",
            "supervisor",
            at=t_gap,
        )
        cmd_vms = approved_command(
            conn,
            "vms-fixture-unsafe",
            "variable_message_sign",
            "vms_adapter",
            "int-c2_int-c3",
            "supervisor",
            at=t_vms,
        )
        with LiveSession(seed=2, **DEMAND) as session:
            session.call("advance", seconds=WARMUP_S)
            pre, pre_window, _ = timed_advance(session, WINDOW_S)
            execute_command_live(
                conn, cmd_ineff, session, {"deviation_s": 15.0, "max_deviation_s": 20.0}, EXECUTOR
            )
            post, post_window, _ = timed_advance(session, WINDOW_S)
            outcome_ineff, verdict_ineff = verify_and_rollback(
                conn,
                cmd_ineff,
                pre,
                post,
                pre_window,
                post_window,
                VERIFIER,
                utc(),
                threshold,
                threshold,
                undo=lambda cmd: session.call("undo", idempotency_key=cmd["idempotency_key"]),
            )
            execute_command_live(
                conn,
                cmd_gap,
                session,
                {"message": "gap"},
                EXECUTOR,
                now=t_gap + timedelta(seconds=10),
            )
            execute_command_live(
                conn,
                cmd_vms,
                session,
                {"message": "Expect delays"},
                EXECUTOR,
                now=t_vms + timedelta(seconds=10),
            )
        ineff = cmd_repo.get_command(conn, cmd_ineff)
        ev.check(
            "a_real_signal_extension_within_the_noise_band_is_ineffective_and_not_rolled_back",
            verdict_ineff.classification == "ineffective"
            and ineff["status"] == "executed"
            and ineff["rolled_back_at"] is None,
            detail=f"pre {pre.value} -> post {post.value} vehicle_s (threshold {threshold})",
        )
        with LiveSession(
            seed=2, **DEMAND
        ) as control:  # same seed, no action: what the post window looks like without the command
            control.call("advance", seconds=WARMUP_S)
            control_pre, _, _ = timed_advance(control, WINDOW_S)
            control_post, _, _ = timed_advance(control, WINDOW_S)
        ev.check(
            "the_ineffective_verdict_matches_a_same_seed_no_action_control",
            abs(post.value - control_post.value) <= noise["noise_band"],
            detail=f"treated post {post.value} vs control post {control_post.value} vehicle_s (control drift {control_pre.value} -> {control_post.value})",
        )
        ev.metrics["ineffective_scenario"] = {
            "pre_waiting": pre.value,
            "post_waiting": post.value,
            "control_no_action": {
                "pre_waiting": control_pre.value,
                "post_waiting": control_post.value,
            },
            "note": "the before/after difference is time drift in the demand, not the action: the same-seed control drifts identically",
        }

        gap_pre_w, gap_post_w, gap_verified = replay(t_gap)
        pre_g = mean_corridor_metric(conn, "corridor-c", "delay_s", "s", gap_pre_w, "east")
        post_g = mean_corridor_metric(conn, "corridor-c", "delay_s", "s", gap_post_w, "east")
        outcome_gap, verdict_gap = verify_and_rollback(
            conn, cmd_gap, pre_g, post_g, gap_pre_w, gap_post_w, VERIFIER, gap_verified, 15.0, 15.0
        )
        gap_row = outcome_repo.get_outcome(conn, outcome_gap)
        ev.check(
            "a_post_action_telemetry_gap_is_unknown_and_escalated_never_defaulted_to_effective",
            pre_g.value == 20.0
            and post_g.value is None
            and verdict_gap.classification == "unknown"
            and gap_row["escalation_reason"]
            and not gap_row["rollback_triggered"]
            and cmd_repo.get_command(conn, cmd_gap)["status"] == "executed",
            detail=str(gap_row.get("escalation_reason")),
        )
        ev.check(
            "the_missing_window_is_recorded_as_a_zero_sample_count_and_is_still_contract_valid",
            gap_row["post_window"]["measurements"][0]["value"] == 0
            and gap_row["post_window"]["measurements"][0]["name"].endswith("_sample_count")
            and not list(Draft202012Validator(SCHEMA).iter_errors(gap_row)),
        )

        vms_pre_w, vms_post_w, vms_verified = replay(t_vms)
        pre_v = mean_corridor_metric(conn, "corridor-c", "delay_s", "s", vms_pre_w, "east")
        post_v = mean_corridor_metric(conn, "corridor-c", "delay_s", "s", vms_post_w, "east")
        outcome_vms, verdict_vms = verify_and_rollback(
            conn, cmd_vms, pre_v, post_v, vms_pre_w, vms_post_w, VERIFIER, vms_verified, 15.0, 15.0
        )
        vms_detail = outcome_repo.get_outcome_detail(conn, outcome_vms)
        ev.check(
            "kpi_windows_are_read_from_the_platforms_own_corridor_kpis",
            pre_v.value == 20.0 and post_v.value == 90.0,
            detail=f"{pre_v.value} -> {post_v.value} s (fixture rows)",
        )
        ev.check(
            "an_unsafe_outcome_on_a_non_actuating_adapter_is_rolled_back_with_the_no_physical_undo_stated",
            verdict_vms.classification == "unsafe"
            and cmd_repo.get_command(conn, cmd_vms)["status"] == "rolled_back"
            and vms_detail["undo"]["kind"] == "none",
        )

        # ---- 4. effective: a real paired same-seed transit-priority run ----
        cmd_eff, baseline, priority, base_window, prio_window = transit_pair(conn)
        pre_t = Metric("transit_travel_time", baseline["transit_travel_time_s"], "s")
        post_t = Metric("transit_travel_time", priority["transit_travel_time_s"], "s")
        outcome_eff, verdict_eff = verify_and_rollback(
            conn,
            cmd_eff,
            pre_t,
            post_t,
            base_window,
            prio_window,
            VERIFIER,
            utc(),
            5.0,
            5.0,
            extra_detail={
                "design": "paired same-seed counterfactual: pre = baseline run, post = priority run"
            },
        )
        ev.check(
            "a_paired_transit_priority_run_is_effective_and_not_rolled_back",
            verdict_eff.classification == "effective"
            and cmd_repo.get_command(conn, cmd_eff)["status"] == "executed",
            detail=f"baseline {pre_t.value}s -> priority {post_t.value}s",
        )

        # ---- 5. an unconfirmed rollback is never claimed (replay timeline, stubbed execution) ----
        t_dec = now - timedelta(hours=1, minutes=30)
        kpi_row(
            conn, "corridor-b", "east", t_dec - timedelta(minutes=1), 10.0
        )  # freshness row for the policy at review time
        w1, w2, dec_verified = (
            (t_dec - timedelta(minutes=10), t_dec),
            (t_dec + timedelta(minutes=1), t_dec + timedelta(minutes=3)),
            t_dec + timedelta(minutes=5),
        )
        cmd_no_undo = approved_command(
            conn,
            "unconfirmed-no-undo",
            "diversion",
            "diversion_adapter",
            edge,
            "supervisor",
            at=t_dec,
        )
        cmd_bad_undo = approved_command(
            conn,
            "unconfirmed-bad-undo",
            "diversion",
            "diversion_adapter",
            edge,
            "supervisor",
            at=t_dec,
        )
        stub_executed(conn, cmd_no_undo, t_dec + timedelta(seconds=10))
        stub_executed(conn, cmd_bad_undo, t_dec + timedelta(seconds=10))
        o1, _ = verify_and_rollback(
            conn,
            cmd_no_undo,
            Metric("m", 10.0, "s"),
            Metric("m", 400.0, "s"),
            w1,
            w2,
            VERIFIER,
            dec_verified,
            20.0,
            20.0,
        )
        o2, _ = verify_and_rollback(
            conn,
            cmd_bad_undo,
            Metric("m", 10.0, "s"),
            Metric("m", 400.0, "s"),
            w1,
            w2,
            VERIFIER,
            dec_verified,
            20.0,
            20.0,
            undo=lambda cmd: {"restored": False, "error": "lanes still closed"},
        )
        row1, row2 = outcome_repo.get_outcome(conn, o1), outcome_repo.get_outcome(conn, o2)
        ev.check(
            "no_undo_available_for_a_reversible_adapter_leaves_the_command_executed_and_escalates",
            cmd_repo.get_command(conn, cmd_no_undo)["status"] == "executed"
            and row1["classification"] == "unsafe"
            and not row1["rollback_triggered"]
            and row1["escalation_reason"],
        )
        ev.check(
            "an_undo_that_does_not_restore_leaves_the_command_executed_and_escalates",
            cmd_repo.get_command(conn, cmd_bad_undo)["status"] == "executed"
            and not row2["rollback_triggered"]
            and "could not be confirmed" in row2["escalation_reason"],
        )

        # ---- 6. independence and precondition guards ----
        cmd_guard = approved_command(
            conn, "guard", "diversion", "diversion_adapter", edge, "supervisor", at=t_dec
        )
        refused_not_executed = False
        try:
            verify_and_rollback(
                conn,
                cmd_guard,
                Metric("m", 1.0, "s"),
                Metric("m", 1.0, "s"),
                w1,
                w2,
                VERIFIER,
                dec_verified,
                1.0,
                1.0,
            )
        except ValueError:
            refused_not_executed = True
        stub_executed(conn, cmd_guard, t_dec + timedelta(seconds=10))
        identities = (("requester", REQUESTER), ("approver", APPROVER), ("executor", EXECUTOR))
        service_refusals, repo_refusals = {}, {}
        for role, who in identities:
            try:  # the service records outcomes only as system:outcome-verifier
                verify_and_rollback(
                    conn,
                    cmd_guard,
                    Metric("m", 1.0, "s"),
                    Metric("m", 1.0, "s"),
                    w1,
                    w2,
                    who,
                    dec_verified,
                    1.0,
                    1.0,
                )
                service_refusals[role] = False
            except NotPermitted:
                service_refusals[role] = True
            try:  # and the repository independently refuses the command's own requester, approver and executor
                outcome_repo.record_outcome(
                    conn,
                    cmd_guard,
                    w1,
                    w2,
                    [Metric("m", 1.0, "s")],
                    [Metric("m", 1.0, "s")],
                    "ineffective",
                    who,
                    dec_verified,
                    False,
                )
                repo_refusals[role] = False
            except outcome_repo.OutcomeError:
                repo_refusals[role] = True
        cmd_misconfigured = approved_command(
            conn, "misconfigured", "diversion", "diversion_adapter", edge, "supervisor", at=t_dec
        )
        stub_executed(
            conn, cmd_misconfigured, t_dec + timedelta(seconds=10), actor=VERIFIER
        )  # an executor mis-provisioned as the verifier
        self_verify_refused = False
        try:
            verify_and_rollback(
                conn,
                cmd_misconfigured,
                Metric("m", 1.0, "s"),
                Metric("m", 1.0, "s"),
                w1,
                w2,
                VERIFIER,
                dec_verified,
                1.0,
                1.0,
            )
        except ValueError:
            self_verify_refused = True
        ev.check("a_command_that_is_not_yet_executed_cannot_be_verified", refused_not_executed)
        ev.check(
            "only_system_outcome_verifier_records_outcomes_never_the_requester_approver_or_executor",
            all(service_refusals.values()),
            detail=str(service_refusals),
        )
        ev.check(
            "the_repository_independently_refuses_the_commands_own_requester_approver_and_executor",
            all(repo_refusals.values()),
            detail=str(repo_refusals),
        )
        ev.check(
            "even_the_verifier_identity_is_refused_for_a_command_it_executed_itself",
            self_verify_refused,
        )
        ev.check(
            "a_refused_verification_leaves_no_outcome_and_no_state_change",
            not outcome_repo.outcomes_for_command(conn, cmd_guard)
            and cmd_repo.get_command(conn, cmd_guard)["status"] == "executed",
        )
        refusals_window = {}
        for label, pre_w, post_w, verified in (
            ("windows_out_of_order", w2, w1, dec_verified),
            ("verified_before_the_post_window_closed", w1, w2, t_dec + timedelta(minutes=2)),
        ):
            try:
                outcome_repo.record_outcome(
                    conn,
                    cmd_guard,
                    pre_w,
                    post_w,
                    [Metric("m", 1.0, "s")],
                    [Metric("m", 1.0, "s")],
                    "ineffective",
                    VERIFIER,
                    verified,
                    False,
                )
                refusals_window[label] = False
            except outcome_repo.OutcomeError:
                refusals_window[label] = True
        ev.check(
            "impossible_window_ordering_is_refused",
            all(refusals_window.values()),
            detail=str(refusals_window),
        )

        # ---- 7. API ----
        asyncio.run(api_checks(outcome_unsafe, outcome_gap, cmd_unsafe))

        # ---- 8. LAT-04: approval -> adapter acknowledgement ----
        warm: list[float] = []
        with LiveSession(seed=7, vehicles=200, span_s=900) as session:
            session.call("advance", seconds=30)
            for i in range(24):
                cid = approved_command(
                    conn,
                    f"lat-warm-{i}",
                    "signal_plan_change",
                    "signal_controller_adapter",
                    CONTROLLED[i % len(CONTROLLED)],
                    "supervisor",
                )
                res = execute_command_live(
                    conn, cid, session, {"deviation_s": 5.0, "max_deviation_s": 20.0}, EXECUTOR
                )
                if not res["acknowledged"]:
                    raise RuntimeError(f"warm command {i} was not acknowledged: {res}")
                warm.append(res["latency_s"])
                session.call("advance", seconds=5)
        cold: list[float] = []
        for i in range(5):
            cid = approved_command(
                conn,
                f"lat-cold-{i}",
                "signal_plan_change",
                "signal_controller_adapter",
                CONTROLLED[i],
                "supervisor",
            )
            started = time.perf_counter()
            res = execute_command(
                conn, cid, {"deviation_s": 5.0, "max_deviation_s": 20.0}, EXECUTOR
            )
            cold.append(time.perf_counter() - started)
            if not res["acknowledged"]:
                raise RuntimeError(f"cold command {i} was not acknowledged: {res}")
        ev.metrics["lat_04"] = {
            "warm_session": {
                "n": len(warm),
                "p50_s": round(sorted(warm)[len(warm) // 2], 4),
                "p95_s": round(p95(warm), 4),
                "max_s": round(max(warm), 4),
            },
            "cold_container_per_action": {
                "n": len(cold),
                "p50_s": round(sorted(cold)[len(cold) // 2], 3),
                "p95_s": round(p95(cold), 3),
                "max_s": round(max(cold), 3),
            },
            "target_p95_s": 3.0,
        }
        ev.check(
            "lat_04_warm_adapter_session_p95_is_under_3_s",
            p95(warm) < 3.0,
            detail=f"p95 {p95(warm):.3f}s over {len(warm)} commands",
        )
        ev.check(
            "lat_04_cold_start_path_is_measured_and_reported_whatever_it_shows",
            len(cold) == 5,
            detail=f"cold p95 {p95(cold):.2f}s (target 3s: {'met' if p95(cold) < 3.0 else 'NOT met'})",
        )

        # ---- cleanup ----
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM commands WHERE idempotency_key LIKE %s", (f"verify-p0709-%-{STAMP}",)
            )
            cur.execute(
                "DELETE FROM observation_events WHERE device_id LIKE %s AND provenance->>'producer' = 'verify-p07.06'",
                ("signal-int-%",),
            )
            for corridor, direction, at in FIXTURE_KPI_ROWS:
                cur.execute(
                    "DELETE FROM corridor_kpis WHERE corridor_id = %s AND direction = %s AND window_start = %s",
                    (corridor, direction, at),
                )
        conn.commit()
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
