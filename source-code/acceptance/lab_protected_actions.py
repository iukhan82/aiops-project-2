#!/usr/bin/env python3
"""P12.02 (SAFE-02): a protected action that lacks current evidence, a valid policy decision or fresh state is denied and NEVER executed.

    python source-code/acceptance/lab_protected_actions.py        # needs the platform stack (PostgreSQL, the policy engine) up; about 3 minutes

The acceptance target reads: "100% of protected actions that lack current evidence, valid policy decision, or fresh state are denied, never executed". Earlier verifiers
proved single cases; this drives EVERY protected adapter of the platform (signal controller, diversion, variable message sign, transit priority, emergency pre-emption)
through EVERY way the target names, on the real database and the real policy engine, through the platform's own request -> review -> executor path:

  evidence     no fresh evidence when the command is reviewed; evidence that goes stale AFTER approval, before the executor drives the adapter
  decision     never approved; approved by the requester; approved by a role with no authority; requested by a role with no authority; expired; an unknown target;
               a linked recommendation of another action type; a superseded recommendation; the policy engine unreachable at approval; the policy engine
               unreachable at execution (the real engine container is stopped)
  state        the adapter unreachable when it is finally driven (the command fails, retryably, and is never `executed`)

The adapter is a counting stand-in for the SUMO container (the simulator's own runs are P07.06-P07.10): what is measured is whether the boundary that drives an adapter was
crossed. A control per adapter - a valid command - must be executed exactly once, so a lab that cannot execute anything cannot pass. Time is moved forward (the clock the
policy sees) rather than waiting, exactly as the P07.05 verifier does.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import psycopg
from psycopg.types.json import Jsonb

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from backend import pdp  # noqa: E402
from backend.control import executor_worker, simulator_adapters  # noqa: E402
from backend.control.command_service import RequestNotPermitted, request_command, review_command  # noqa: E402
from backend.control.policy import PolicyResult, execution_gate  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.loader.platform_loader import load_events, register_devices  # noqa: E402
from backend.repositories import commands as command_repo  # noqa: E402
from backend.repositories.recommendations import create_recommendation, supersede  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

GEOMETRY = "2026-09-18.1"
STAMP = uuid.uuid4().hex[:8]
ev = Evidence(
    "P12.02", "p12_02_safe02_protected_actions", docs_name="p12_02_safe02_protected_actions"
)

# adapter, action type, a real target, its safety class
SPECS = (
    ("signal_controller_adapter", "signal_plan_change", "int-a2", "SC-1"),
    ("diversion_adapter", "diversion", "int-a2_int-a3", "SC-1"),
    ("vms_adapter", "variable_message_sign", "int-a2_int-a3", "SC-0"),
    ("transit_priority_adapter", "transit_priority", "corridor-a", "SC-1"),
    ("emergency_preemption_adapter", "emergency_preemption", "corridor-a", "SC-2"),
    # the eight cross streets have no corridor: their evidence is the telemetry of the junctions at their ends (found by this lab, P12.02: it used to need none)
    ("diversion_adapter", "diversion", "int-a1_int-b1", "SC-1"),
    ("vms_adapter", "variable_message_sign", "int-a1_int-b1", "SC-0"),
)
REQUESTER, APPROVER = "operator:alice", "supervisor:bob"
LONG = 6 * 3600.0  # commands live long enough that "stale" and "expired" are never confused

calls: dict[str, int] = {}  # idempotency key -> times the adapter boundary was crossed
attempts: list[dict] = []
mode = {"adapter": "acknowledge"}  # what the stand-in does: acknowledge, or be unreachable


def stand_in(_command: str, _timeout: float) -> subprocess.CompletedProcess:
    """The simulator adapter, counted. Reads the action the real code just wrote, so the count is per command."""
    action = json.loads((simulator_adapters.OUTPUT_DIR / "action.json").read_text(encoding="utf-8"))
    calls[action["idempotency_key"]] = calls.get(action["idempotency_key"], 0) + 1
    if mode["adapter"] == "unreachable":
        raise subprocess.TimeoutExpired("adapter", 1)
    (simulator_adapters.OUTPUT_DIR / "result.json").write_text(
        json.dumps({"acknowledged": True, "error": None}), encoding="utf-8"
    )
    return subprocess.CompletedProcess([], 0, "", "")


def wsl(command: str, timeout: float = 90) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["wsl.exe", "-e", "bash", "-lc", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def engine(up: bool) -> None:
    wsl("docker start aiops-opa" if up else "docker stop aiops-opa", 60)
    for _ in range(60):
        try:
            answering = pdp.loaded_version() is not None
        except Exception:  # noqa: BLE001 - the point is that it may not answer
            answering = False
        if answering == up:
            return
        time.sleep(1)
    raise RuntimeError(f"the policy engine did not become {'available' if up else 'unavailable'}")


def seed_evidence(conn: psycopg.Connection, now: datetime) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM corridor_kpis WHERE window_start > %s", (now - timedelta(hours=1),)
        )
        for direction in ("east", "west"):
            cur.execute(
                "INSERT INTO corridor_kpis (corridor_id, direction, window_start, window_seconds, geometry_version, kpis, quality, coverage, sample_count, segments_reporting, segments_expected) "
                "VALUES (%s, %s, %s, 300, %s, %s::jsonb, 'valid', 1.0, 10, 1, 1) ON CONFLICT DO NOTHING",
                (
                    "corridor-a",
                    direction,
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
    for device_id, junction in (("signal-int-a2", "int-a2"), ("signal-int-a1", "int-a1")):
        signal_reading(conn, device_id, junction, now)


def signal_reading(conn: psycopg.Connection, device_id: str, junction: str, now: datetime) -> None:
    """A real signal-controller envelope through the real ingestion path: the evidence a signal target, and a cross street at that junction, needs."""
    register_devices(
        conn,
        [
            {
                "device_id": device_id, "device_type": "signal_controller", "deployment_type": "simulated", "agency_scope": "city-traffic-ops",
                "location": {"geometry_version": GEOMETRY, "longitude": 74.36, "latitude": 31.52, "intersection_id": junction},
                "capabilities": ["signal_state"], "status": "active", "registered_at": now, "privacy_classification": "none", "retention_class": "standard",
            }
        ],
    )  # fmt: skip
    load_events(
        conn,
        [
            {
                "schema_version": "1.0.0", "event_id": str(uuid.uuid4()), "event_type": "signal.controller.spat", "device_id": device_id, "agency_scope": "city-traffic-ops",
                "observation_time": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "ingest_time": now.strftime("%Y-%m-%dT%H:%M:%S.250Z"), "sequence_number": 0, "clock_quality": "synced",
                "geometry_version": GEOMETRY,
                "location": {"coordinate_reference": "EPSG:4326", "latitude": 31.52, "longitude": 74.36, "intersection_id": junction},
                "measurements": [
                    {"name": "active_phase", "value": 0, "unit": "index", "quality": "valid", "confidence": 1.0},
                    {"name": "signal_state", "value": "GGGGGGr", "unit": "category", "quality": "valid", "confidence": 1.0},
                ],
                "truth_label": "simulated", "privacy_classification": "none", "retention_class": "standard",
                "provenance": {"producer": "p12-safe02-lab", "pipeline_version": "lab-1"},
            }
        ],
    )  # fmt: skip


def proposal(conn: psycopg.Connection, action_type: str, now: datetime) -> str:
    return create_recommendation(
        conn,
        action_type,
        [
            {
                "alternative_id": "a1",
                "description": "lab",
                "predicted_benefit": [],
                "predicted_harm": [],
                "confidence": 0.5,
            }
        ],
        {"min_pedestrian_clearance_s": 7.0, "max_signal_deviation_s": 20.0},
        [],
        now,
        now + timedelta(seconds=3600),
    )


def request(
    conn: psycopg.Connection, case: str, spec: tuple, now: datetime, **kw
) -> tuple[str, str]:
    adapter, action, entity, _ = spec
    key = f"p12-safe02-{STAMP}-{case}-{adapter}-{entity}"
    command_id, _ = request_command(
        conn,
        key,
        action,
        adapter,
        kw.pop("entity", entity),
        kw.pop("requester", REQUESTER),
        now,
        kw.pop("requester_role", "operator"),
        ttl_s=kw.pop("ttl_s", LONG),
        **kw,
    )
    return command_id, key


def drain(conn: psycopg.Connection, limit: int = 12) -> None:
    """Let the real executor loop act on everything approved (it acts on one command per call)."""
    for _ in range(limit):
        if executor_worker.run_once(conn) is None:
            return
    conn.commit()


def record(
    conn: psycopg.Connection,
    case: str,
    spec: tuple,
    command_id: str | None,
    key: str | None,
    expect: tuple[str, ...],
) -> dict:
    row = command_repo.get_command(conn, command_id) if command_id else None
    status = row["status"] if row else "no command was created"
    executed_calls = calls.get(key, 0) if key else 0
    entry = {
        "case": case,
        "adapter": spec[0],
        "status": status,
        "error_code": (row or {}).get("error_code"),
        "adapter_calls": executed_calls,
        "expected": list(expect),
        "ok": status in expect and (executed_calls == 0 or status == "executed"),
    }
    attempts.append(entry)
    return entry


def main() -> int:  # noqa: PLR0915
    with (
        patch.object(simulator_adapters, "_run_in_wsl", stand_in),
        psycopg.connect(dsn_from_env()) as conn,
    ):
        now = datetime.now(UTC)
        stale_time = now + timedelta(hours=2)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM commands WHERE status IN ('approved', 'executing')")
            waiting = cur.fetchone()[0]
        ev.check(
            "precondition_no_command_is_waiting_for_the_executor_so_every_call_counted_here_is_this_lab_s",
            waiting == 0,
            f"{waiting} approved or executing",
        )
        if waiting:
            return ev.finish()
        seed_evidence(conn, now)
        engine_ok = True

        for spec in SPECS:
            adapter, action, entity, sc = spec
            # ---- control: a valid command is executed exactly once
            cid, key = request(conn, "control", spec, now)
            status = review_command(
                conn, cid, APPROVER, "supervisor", now + timedelta(seconds=2), GEOMETRY
            )
            drain(conn)
            entry = record(conn, "control_valid_command", spec, cid, key, ("executed",))
            entry["ok"] = entry["ok"] and status == "approved" and entry["adapter_calls"] == 1

            # ---- evidence
            cid, key = request(conn, "stale-at-approval", spec, now)
            review_command(conn, cid, APPROVER, "supervisor", stale_time, GEOMETRY)
            drain(conn)
            record(conn, "no_fresh_evidence_at_approval", spec, cid, key, ("denied",))

            cid, key = request(conn, "stale-after-approval", spec, now)
            review_command(conn, cid, APPROVER, "supervisor", now + timedelta(seconds=2), GEOMETRY)
            with patch.object(
                executor_worker,
                "execution_gate",
                lambda c, command, _now, geometry: execution_gate(c, command, stale_time, geometry),
            ):
                drain(conn)
            record(
                conn, "evidence_stale_after_approval_before_execution", spec, cid, key, ("denied",)
            )

            # ---- decision
            cid, key = request(conn, "unapproved", spec, now)
            drain(conn)
            record(conn, "never_approved", spec, cid, key, ("requested",))

            if sc != "SC-0":  # SC-0 needs no second person
                cid, key = request(conn, "self-approval", spec, now)
                review_command(
                    conn, cid, REQUESTER, "supervisor", now + timedelta(seconds=2), GEOMETRY
                )
                drain(conn)
                record(conn, "approved_by_its_own_requester", spec, cid, key, ("denied",))

            cid, key = request(conn, "wrong-approver", spec, now)
            review_command(
                conn, cid, "dispatcher:dan", "dispatcher", now + timedelta(seconds=2), GEOMETRY
            )
            drain(conn)
            record(conn, "approved_by_a_role_with_no_authority", spec, cid, key, ("denied",))

            key = f"p12-safe02-{STAMP}-wrong-requester-{adapter}-{entity}"
            try:
                request_command(
                    conn,
                    key,
                    action,
                    adapter,
                    entity,
                    "auditor:ana",
                    now,
                    "auditor",
                    ttl_s=LONG,
                    geometry=GEOMETRY,
                )
                created = True
            except RequestNotPermitted:
                created = False
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM commands WHERE idempotency_key = %s", (key,))
                stored = cur.fetchone()[0]
            attempts.append(
                {
                    "case": "requested_by_a_role_with_no_authority",
                    "adapter": adapter,
                    "status": "refused, no command exists"
                    if not created and stored == 0
                    else "a command was created",
                    "error_code": None,
                    "adapter_calls": 0,
                    "expected": ["refused"],
                    "ok": not created and stored == 0,
                }
            )

            cid, key = request(
                conn, "unknown-target", spec, now, entity="target-that-does-not-exist"
            )
            review_command(conn, cid, APPROVER, "supervisor", now + timedelta(seconds=2), GEOMETRY)
            drain(conn)
            record(conn, "unknown_target", spec, cid, key, ("denied",))

            cid, key = request(conn, "expired", spec, now, ttl_s=1.0)
            review_command(conn, cid, APPROVER, "supervisor", now + timedelta(seconds=30), GEOMETRY)
            drain(conn)
            record(conn, "expired_before_review", spec, cid, key, ("expired",))

            other_action = "diversion" if action != "diversion" else "signal_plan_change"
            cid, key = request(
                conn, "rec-mismatch", spec, now, recommendation_id=proposal(conn, other_action, now)
            )
            review_command(conn, cid, APPROVER, "supervisor", now + timedelta(seconds=2), GEOMETRY)
            drain(conn)
            record(
                conn, "linked_recommendation_of_another_action_type", spec, cid, key, ("denied",)
            )

            first = proposal(conn, action, now)
            supersede(conn, first, proposal(conn, action, now))
            cid, key = request(conn, "rec-superseded", spec, now, recommendation_id=first)
            review_command(conn, cid, APPROVER, "supervisor", now + timedelta(seconds=2), GEOMETRY)
            drain(conn)
            record(conn, "linked_recommendation_superseded", spec, cid, key, ("denied",))

            # ---- state: the adapter cannot be reached when it is finally driven
            mode["adapter"] = "unreachable"
            cid, key = request(conn, "adapter-down", spec, now)
            review_command(conn, cid, APPROVER, "supervisor", now + timedelta(seconds=2), GEOMETRY)
            drain(conn)
            entry = record(conn, "adapter_unreachable_when_driven", spec, cid, key, ("failed",))
            entry["ok"] = (
                entry["status"] == "failed" and entry["error_code"] == "adapter_unreachable"
            )  # the boundary was crossed and nothing was acknowledged: it is not `executed`
            mode["adapter"] = "acknowledge"

        # ---- a lab that cannot fail proves nothing: approve a valid command, take its evidence away, and switch the executor's own policy gate off - it IS executed, and this lab counts it
        mutation_spec = SPECS[1]
        cid, key = request(conn, "mutation", mutation_spec, now)
        review_command(conn, cid, APPROVER, "supervisor", now + timedelta(seconds=2), GEOMETRY)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM corridor_kpis WHERE window_start > %s", (now - timedelta(hours=6),)
            )
        conn.commit()
        with patch.object(executor_worker, "execution_gate", lambda *_a: PolicyResult("approved")):
            drain(conn)
        mutant = record(conn, "mutation_gate_switched_off", mutation_spec, cid, key, ("executed",))
        attempts.remove(mutant)  # it is a probe of the lab, not one of the platform's attempts
        ev.check(
            "the_lab_can_fail_with_the_gate_switched_off_a_command_with_no_evidence_is_executed_and_counted",
            mutant["status"] == "executed" and mutant["adapter_calls"] == 1,
            f"status {mutant['status']}, adapter calls {mutant['adapter_calls']}",
        )
        seed_evidence(conn, now)  # the evidence the later cases need

        # ---- the policy engine: approved before it stops, requested while it is stopped
        ready = []
        for spec in SPECS:
            cid, key = request(conn, "engine-hold", spec, now)
            review_command(conn, cid, APPROVER, "supervisor", now + timedelta(seconds=2), GEOMETRY)
            ready.append((spec, cid, key))
        try:
            engine(False)
            engine_ok = False
            during = []
            for spec in SPECS:
                cid, key = request(conn, "engine-down-approval", spec, now)
                status = review_command(
                    conn, cid, APPROVER, "supervisor", now + timedelta(seconds=2), GEOMETRY
                )
                during.append((spec, cid, key, status))
            executor_worker._gate_retry_after.clear()  # noqa: SLF001
            drain(conn)
            for spec, cid, key, status in during:
                entry = record(
                    conn, "policy_engine_unreachable_at_approval", spec, cid, key, ("requested",)
                )
                entry["ok"] = entry["ok"] and status == "requested"
            for spec, cid, key in ready:
                record(
                    conn, "policy_engine_unreachable_at_execution", spec, cid, key, ("approved",)
                )
        finally:
            engine(True)
            engine_ok = True
        executor_worker._gate_retry_after.clear()  # noqa: SLF001
        drain(conn)
        released = [command_repo.get_command(conn, cid)["status"] for _, cid, _ in ready]
        ev.check(
            "with_the_engine_back_the_commands_that_were_held_are_executed_so_holding_was_a_wait_not_a_refusal",
            all(s == "executed" for s in released),
            str(released),
        )
        ev.check(
            "the_policy_engine_is_running_again", engine_ok and pdp.loaded_version() is not None
        )

    # ---- the verdicts
    by_case: dict[str, list[dict]] = {}
    for a in attempts:
        by_case.setdefault(a["case"], []).append(a)
    for case, rows in by_case.items():
        if case == "control_valid_command":
            ev.check(
                "control_a_valid_command_is_approved_and_executed_exactly_once_on_every_adapter",
                all(r["ok"] for r in rows),
                f"{len(rows)} adapters; statuses {[r['status'] for r in rows]}; calls {[r['adapter_calls'] for r in rows]}",
            )
            continue
        ev.check(
            f"{case}_is_never_executed_on_any_of_the_{len(rows)}_adapters_and_ends_{'/'.join(rows[0]['expected'])}",
            all(r["ok"] for r in rows),
            f"statuses {sorted({r['status'] for r in rows})}; adapter calls {sum(r['adapter_calls'] for r in rows)}; error codes {sorted({str(r['error_code']) for r in rows})}",
        )
    violating = [
        a
        for a in attempts
        if a["case"] not in ("control_valid_command", "adapter_unreachable_when_driven")
    ]
    ev.check(
        "safe_02_every_attempt_that_lacked_evidence_a_valid_decision_or_fresh_state_was_stopped_and_the_adapter_was_never_driven",
        all(a["adapter_calls"] == 0 and a["status"] != "executed" for a in violating)
        and len(violating) > 0,
        f"{len(violating)} attempts, {sum(1 for a in violating if a['status'] == 'executed')} executed, {sum(a['adapter_calls'] for a in violating)} adapter calls",
    )
    ev.metrics["attempts"] = attempts
    ev.metrics["counts"] = {
        "adapters": len(SPECS),
        "attempts": len(attempts),
        "violating_attempts": len(violating),
        "executed_among_violating": sum(1 for a in violating if a["status"] == "executed"),
    }
    ev.notes["what_is_stood_in"] = (
        "the simulator adapter is a counting stand-in (the SUMO runs are P07.06-P07.10); what is measured is whether the boundary that drives an adapter was crossed, through the real request, policy (real engine) and executor code"
    )
    ev.notes["not_proven"] = (
        "actions through the HTTP API and roles as Keycloak tokens (P08.08 and P09.10); a real adapter failure other than a timeout; commands the executor never sees because they were never approved are counted as such, not as denials"
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
