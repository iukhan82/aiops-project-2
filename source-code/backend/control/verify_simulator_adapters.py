"""P07.06 acceptance evidence, against the real Postgres, the real seeded
network and the real pinned SUMO image via TraCI:

    python source-code/backend/control/verify_simulator_adapters.py

Proves the full pipeline on real infrastructure: request -> P07.05 policy
approval -> P07.06 execution against a real running SUMO instance -> the
command's own status reflects what the simulator actually acknowledged.
Idempotency is proven at the simulator layer itself (a second execution of
the same command never re-issues the TraCI action - the container's own
ledger, a real file, proves it), not merely re-asserted from P07.05's
database-level guarantee. An unregistered adapter and an unapproved command
are both refused before anything is dispatched.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.control.command_service import request_command, review_command  # noqa: E402
from backend.control.simulator_adapters import AdapterExecutionError, OUTPUT_DIR, execute_command  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.loader.platform_loader import load_events, register_devices  # noqa: E402
from backend.repositories import commands as cmd_repo  # noqa: E402
from backend.roles import EXECUTOR, NotPermitted  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

GEOMETRY = "2026-09-18.1"
ev = Evidence("P07.06")


def fresh_signal_evidence(
    conn: psycopg.Connection, device_id: str, intersection_id: str, now: datetime
) -> None:
    """P07.05's SAFE-03 freshness gate needs a recent observation for the signal's own device - a real
    envelope through the real ingestion path, not a raw insert. `ingest_one` refuses an event from a
    device it does not already know (REJECTED_UNKNOWN_DEVICE, silent - it does not raise), so the
    device is registered first, idempotently (ON CONFLICT DO NOTHING); this fixture device is not part
    of any durable seed, so a verification database that never saw it before needs this every run."""
    register_devices(
        conn,
        [
            {
                "device_id": device_id,
                "device_type": "signal_controller",
                "deployment_type": "simulated",
                "agency_scope": "city-traffic-ops",
                "location": {
                    "geometry_version": GEOMETRY,
                    "longitude": 74.36,
                    "latitude": 31.52,
                    "intersection_id": intersection_id,
                },
                "capabilities": ["signal_state"],
                "status": "active",
                "registered_at": now,
                "privacy_classification": "none",
                "retention_class": "standard",
            }
        ],
    )
    event = {
        "schema_version": "1.0.0",
        "event_id": str(uuid.uuid4()),
        "event_type": "signal.controller.spat",
        "device_id": device_id,
        "agency_scope": "city-traffic-ops",
        "observation_time": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "ingest_time": now.strftime("%Y-%m-%dT%H:%M:%S.250Z"),
        "sequence_number": 0,
        "clock_quality": "synced",
        "geometry_version": GEOMETRY,
        "location": {
            "coordinate_reference": "EPSG:4326",
            "latitude": 31.52,
            "longitude": 74.36,
            "intersection_id": intersection_id,
        },
        "measurements": [
            {
                "name": "active_phase",
                "value": 0,
                "unit": "index",
                "quality": "valid",
                "confidence": 1.0,
            },
            {
                "name": "signal_state",
                "value": "GGGGGGr",
                "unit": "category",
                "quality": "valid",
                "confidence": 1.0,
            },
        ],
        "truth_label": "simulated",
        "privacy_classification": "none",
        "retention_class": "standard",
        "provenance": {"producer": "verify-p07.06", "pipeline_version": "verify-1"},
    }
    load_events(conn, [event])


def main() -> int:
    ledger_path = OUTPUT_DIR / "ledger.jsonl"
    with psycopg.connect(dsn_from_env()) as conn:
        now = datetime.now(timezone.utc)
        fresh_signal_evidence(conn, "signal-int-a2", "int-a2", now)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO corridor_kpis (corridor_id, direction, window_start, window_seconds, geometry_version, kpis, quality, coverage, "
                "sample_count, segments_reporting, segments_expected) VALUES ('corridor-a', 'east', %s, 300, %s, %s::jsonb, 'valid', 1.0, 10, 1, 1) "
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

        # ---- signal: request -> approve -> execute against real SUMO ----
        cmd1, _ = request_command(
            conn,
            f"verify-p0706-signal-{now.timestamp()}",
            "signal_plan_change",
            "signal_controller_adapter",
            "int-a2",
            "operator:alice",
            now,
            "operator",
        )
        status1 = review_command(
            conn, cmd1, "supervisor:bob", "supervisor", now + timedelta(seconds=5), GEOMETRY
        )
        ev.check("a_real_signal_command_is_approved_by_policy", status1 == "approved")
        result1 = execute_command(
            conn,
            cmd1,
            {"deviation_s": 15.0, "max_deviation_s": 20.0},
            EXECUTOR,
            now + timedelta(seconds=6),
        )
        ev.check(
            "the_signal_adapter_actually_moved_a_real_traffic_lights_next_switch",
            result1["acknowledged"]
            and result1["observation"]["deviation_applied_s"] == 15.0
            and result1["observation"]["next_switch_after"]
            > result1["observation"]["next_switch_before"],
            detail=str(result1.get("observation")),
        )
        final1 = cmd_repo.get_command(conn, cmd1)
        ev.check(
            "the_command_status_reflects_real_simulator_acknowledgement",
            final1["status"] == "executed" and final1["acknowledged_at"] is not None,
        )

        # ---- idempotency at the simulator layer: a second execution never re-issues the TraCI action ----
        before_ledger = ledger_path.read_text(encoding="utf-8") if ledger_path.is_file() else ""
        with conn.cursor() as cur:
            cur.execute("UPDATE commands SET status = 'approved' WHERE command_id = %s", (cmd1,))
        conn.commit()
        result1b = execute_command(
            conn,
            cmd1,
            {"deviation_s": 15.0, "max_deviation_s": 20.0},
            EXECUTOR,
            now + timedelta(seconds=7),
        )
        after_ledger = ledger_path.read_text(encoding="utf-8")
        ev.check(
            "re_executing_the_same_idempotency_key_is_a_replay_the_ledger_is_unchanged",
            result1b["idempotent_replay"] is True and after_ledger == before_ledger,
            detail=f"ledger lines before/after: {before_ledger.count(chr(10))}/{after_ledger.count(chr(10))}",
        )
        ev.check(
            "the_replayed_result_matches_the_original_observation",
            result1b["observation"] == result1["observation"],
        )

        # ---- diversion: real lane closure, observed vehicle presence ----
        cmd2, _ = request_command(
            conn,
            f"verify-p0706-diversion-{now.timestamp()}",
            "diversion",
            "diversion_adapter",
            "int-a2_int-a3",
            "operator:alice",
            now,
            "operator",
        )
        review_command(
            conn, cmd2, "supervisor:bob", "supervisor", now + timedelta(seconds=5), GEOMETRY
        )
        result2 = execute_command(conn, cmd2, {}, EXECUTOR, now + timedelta(seconds=6))
        ev.check(
            "the_diversion_adapter_actually_closed_the_real_general_traffic_lanes",
            result2["acknowledged"]
            and result2["observation"]["lanes_closed"]
            and all(
                "passenger" in v for v in result2["observation"]["observed_disallowed"].values()
            ),
            detail=str(result2.get("observation")),
        )

        # ---- VMS: recorded, honestly labelled as no simulator actuation ----
        cmd3, _ = request_command(
            conn,
            f"verify-p0706-vms-{now.timestamp()}",
            "variable_message_sign",
            "vms_adapter",
            "int-a2_int-a3",
            "operator:alice",
            now,
            "operator",
        )
        review_command(
            conn, cmd3, "supervisor:bob", "supervisor", now + timedelta(seconds=5), GEOMETRY
        )
        result3 = execute_command(
            conn, cmd3, {"message": "Expect delays ahead"}, EXECUTOR, now + timedelta(seconds=6)
        )
        ev.check(
            "the_vms_adapter_completes_and_honestly_reports_no_simulator_actuation",
            result3["acknowledged"] and result3["observation"]["simulator_actuation"] is False,
        )

        # ---- a target the simulator itself rejects fails the command, not silently succeeds ----
        cmd4, _ = request_command(
            conn,
            f"verify-p0706-badtarget-{now.timestamp()}",
            "signal_plan_change",
            "signal_controller_adapter",
            "int-zz-fake",
            "operator:alice",
            now,
            "operator",
        )
        with (
            conn.cursor() as cur
        ):  # bypass policy's own (correct) target-validity rejection to exercise the adapter's own defense
            cur.execute(
                "UPDATE commands SET status = 'approved', policy_decision = 'approved' WHERE command_id = %s",
                (cmd4,),
            )
        conn.commit()
        result4 = execute_command(
            conn,
            cmd4,
            {"deviation_s": 10.0, "max_deviation_s": 20.0},
            EXECUTOR,
            now + timedelta(seconds=6),
        )
        ev.check(
            "a_target_the_real_simulator_does_not_recognize_fails_the_command_not_silently_succeeds",
            not result4["acknowledged"] and cmd_repo.get_command(conn, cmd4)["status"] == "failed",
        )

        # ---- an unapproved command is refused before anything is dispatched ----
        cmd5, _ = request_command(
            conn,
            f"verify-p0706-unapproved-{now.timestamp()}",
            "diversion",
            "diversion_adapter",
            "int-a2_int-a3",
            "operator:alice",
            now,
            "operator",
        )
        refused = False
        try:
            execute_command(conn, cmd5, {}, EXECUTOR, now)
        except AdapterExecutionError:
            refused = True
        ev.check(
            "a_command_that_is_not_approved_is_refused_before_any_simulator_dispatch",
            refused and cmd_repo.get_command(conn, cmd5)["status"] == "requested",
        )

        # ---- an unregistered adapter is refused before any simulator dispatch ----
        cmd6, _ = request_command(
            conn,
            f"verify-p0706-unregistered-{now.timestamp()}",
            "other",
            "made_up_adapter",
            "int-a2",
            "operator:alice",
            now,
            "operator",
        )
        with conn.cursor() as cur:
            cur.execute("UPDATE commands SET status = 'approved' WHERE command_id = %s", (cmd6,))
        conn.commit()
        refused6 = False
        try:
            execute_command(conn, cmd6, {}, EXECUTOR, now)
        except AdapterExecutionError:
            refused6 = True
        ev.check("an_unregistered_adapter_is_refused_before_any_simulator_dispatch", refused6)

        refused_identity = False
        try:
            execute_command(conn, cmd1, {}, "supervisor:bob", now)
        except NotPermitted:
            refused_identity = True
        ev.check(
            "a_human_identity_cannot_execute_only_system_command_executor_can", refused_identity
        )

        ev.metrics["ledger_entries_after_test"] = (
            ledger_path.read_text(encoding="utf-8").count("\n") if ledger_path.is_file() else 0
        )

        with conn.cursor() as cur:
            cur.execute("DELETE FROM commands WHERE idempotency_key LIKE 'verify-p0706-%'")
            cur.execute(
                "DELETE FROM observation_events WHERE device_id = 'signal-int-a2' AND provenance->>'producer' = 'verify-p07.06'"
            )
            cur.execute(
                "DELETE FROM corridor_kpis WHERE corridor_id = 'corridor-a' AND direction = 'east' AND window_start = %s",
                (now - timedelta(minutes=1),),
            )
        conn.commit()
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
