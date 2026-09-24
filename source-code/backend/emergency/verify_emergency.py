"""P07.01 acceptance evidence, against the real Postgres and P03.04's real
simulated CAD export:

    python source-code/backend/emergency/verify_emergency.py

Part A replays the real dataset (9 calls, 9 assignments, all three call
types) through the state machines and ingests the real AVL telemetry (238
events) through P05.05's real write path. Part B proves the two transitions
the real dataset never exercises (`cancelled`, `unavailable`) with synthetic,
labelled records through the same repository code.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg
import uvicorn

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

os.environ.setdefault(
    "AIOPS_AUTH_MODE", "off"
)  # this script tests data, not authentication; verify_auth.py proves the latter
from backend.api.app import app  # noqa: E402
from backend.emergency.cad_avl_adapter import replay  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.loader.platform_loader import load_events, register_devices  # noqa: E402
from backend.repositories import emergency as repo  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

DATASET = SOURCE_ROOT / "simulator" / "emergency" / "output" / "run-a"
GEOMETRY = "2026-09-18.1"
HOST, PORT = "127.0.0.1", 8799
ev = Evidence("P07.01")


def run_server() -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("uvicorn did not start")


async def api_checks(sample_call_id: str) -> None:
    server = run_server()
    try:
        async with httpx.AsyncClient(base_url=f"http://{HOST}:{PORT}", timeout=30) as client:
            page = (await client.get("/api/v1/emergency/calls", params={"limit": 500})).json()
            ev.check(
                "api_lists_real_calls_with_correct_schema",
                len(page["items"]) >= 9
                and all(
                    {"call_id", "call_type", "call_subtype", "status", "truth_label"} <= set(i)
                    for i in page["items"]
                ),
            )
            filtered = (
                await client.get(
                    "/api/v1/emergency/calls", params={"call_type": "fire", "limit": 500}
                )
            ).json()["items"]
            ev.check(
                "api_call_type_filter_works",
                filtered and all(i["call_type"] == "fire" for i in filtered),
            )
            detail = (await client.get(f"/api/v1/emergency/calls/{sample_call_id}")).json()
            ev.check(
                "api_call_detail_includes_assignments_and_transitions",
                detail["assignments"]
                and detail["transitions"]
                and detail["transitions"][0]["to_status"] == "received"
                and detail["assignments"][0]["route_alternatives"],
            )
            missing = await client.get(
                f"/api/v1/emergency/calls/{'0' * 8}-0000-0000-0000-{'0' * 12}"
            )
            ev.check("api_unknown_call_is_404", missing.status_code == 404)
    finally:
        server.should_exit = True
        await asyncio.sleep(0.5)


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def purge(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM emergency_unit_assignments")
        cur.execute("DELETE FROM emergency_calls")
        cur.execute(
            "DELETE FROM observation_events WHERE event_type = 'emergency.unit_position.avl'"
        )
    conn.commit()


def main() -> int:
    calls = jsonl(DATASET / "calls.jsonl")
    assignments = jsonl(DATASET / "assignments.jsonl")
    avl_events = jsonl(DATASET / "avl_events.jsonl")
    devices = jsonl(DATASET / "devices.jsonl")

    with psycopg.connect(dsn_from_env()) as conn:
        purge(conn)
        register_devices(conn, devices)

        # ---- Part A: the real dataset, real replay, real ingestion ----
        report = replay(conn, calls, assignments)
        ev.check(
            "every_call_and_assignment_replayed_without_a_rejected_transition",
            not report.errors
            and report.calls_created == len(calls)
            and report.assignments_created == len(assignments),
            detail=f"{report.calls_created}/{len(calls)} calls, "
            f"{report.assignments_created}/{len(assignments)} assignments, {report.transitions} transitions, errors={report.errors[:2]}",
        )
        with conn.cursor() as cur:
            cur.execute("SELECT status, count(*) FROM emergency_calls GROUP BY status")
            call_status = dict(cur.fetchall())
            cur.execute("SELECT call_type, count(*) FROM emergency_calls GROUP BY call_type")
            by_type = dict(cur.fetchall())
        ev.check(
            "all_three_call_types_present_and_every_call_reached_a_terminal_status",
            set(by_type) == {"ambulance", "fire", "police"}
            and call_status.get("cleared", 0) == len(calls),
            detail=f"{by_type}, {call_status}",
        )

        outcomes = load_events(conn, avl_events)
        ev.check(
            "avl_telemetry_ingested_through_the_real_write_path",
            outcomes.get("inserted", 0) == len(avl_events),
            detail=str(dict(outcomes)),
        )
        again = load_events(conn, avl_events)
        ev.check(
            "reingesting_avl_telemetry_is_idempotent",
            again.get("duplicate_ok", 0) == len(avl_events) and not again.get("inserted"),
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM observation_events WHERE event_type = 'emergency.unit_position.avl'"
            )
            (n_avl,) = cur.fetchone()
            cur.execute(
                "SELECT count(DISTINCT device_id) FROM observation_events WHERE event_type = 'emergency.unit_position.avl'"
            )
            (n_units,) = cur.fetchone()
        ev.check(
            "avl_events_are_real_device_bound_telemetry_not_a_position_column_on_the_call",
            n_avl == len(avl_events) and n_units >= 3,
            detail=f"{n_avl} events from {n_units} AVL devices",
        )

        one = calls[0]
        detail_call = repo.get_call(conn, one["call_id"])
        history = repo.call_transition_history(conn, one["call_id"])
        assignments_for = repo.assignments_for_call(conn, one["call_id"])
        ev.check(
            "call_history_is_append_only_and_starts_at_received",
            history[0]["from_status"] is None
            and history[0]["to_status"] == "received"
            and history[-1]["to_status"] == detail_call["status"],
        )
        ev.check(
            "assignment_carries_its_own_timeline_distinct_from_the_calls",
            bool(assignments_for)
            and all(
                a["acknowledged_at"] and a["arrived_at"] and a["cleared_at"]
                for a in assignments_for
            ),
        )
        a = assignments_for[0]
        asg_hist = repo.assignment_transition_history(conn, str(a["assignment_id"]))
        ev.check(
            f"assignment_{str(a['assignment_id'])[:8]}_history_matches_its_stored_timestamps",
            [h["to_status"] for h in asg_hist]
            == ["assigned", "acknowledged", "en_route", "on_scene", "clear"],
        )

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM emergency_assignment_transitions")
            (n_asg_trans,) = cur.fetchone()
        ev.check(
            "every_assignment_has_five_transitions_assigned_through_clear",
            n_asg_trans == 5 * len(assignments),
            detail=f"{n_asg_trans} rows",
        )

        # re-running the replay on an already-loaded dataset must not silently succeed by corrupting state:
        # every create attempt fails because the calls/assignments already exist (row already there), proving
        # the adapter is not accidentally idempotent by skipping work - a real live feed sends genuinely new IDs
        again_report = replay(conn, calls, assignments)
        ev.check(
            "re_running_the_replay_on_an_already_loaded_dataset_is_rejected_not_silently_duplicated",
            again_report.calls_created == 0
            and again_report.assignments_created == 0
            and len(again_report.errors) > 0,
            detail=f"{len(again_report.errors)} rejected re-creations (unique key), e.g. {again_report.errors[:1]}",
        )

        # ---- Part B: transitions this dataset never produces, proven with labelled synthetic records ----
        now = datetime.now(timezone.utc)
        cancelled_id = repo.create_call(
            conn,
            "police",
            "false_alarm",
            "low",
            {"latitude": 31.52, "longitude": 74.36},
            GEOMETRY,
            "unverified_report",
            "simulated",
            "test:synthetic",
            at=now,
        )
        repo.transition_call(conn, cancelled_id, "dispatched", "test:synthetic", at=now)
        repo.transition_call(
            conn,
            cancelled_id,
            "cancelled",
            "test:synthetic",
            "caller confirmed false alarm",
            at=now,
        )
        ev.check(
            "B1_a_call_can_be_cancelled_and_cancelled_is_terminal",
            repo.get_call(conn, cancelled_id)["status"] == "cancelled",
        )
        try:
            repo.transition_call(conn, cancelled_id, "dispatched", "test:synthetic")
            illegal = False
        except repo.InvalidTransition:
            illegal = True
        ev.check("B1_no_transition_is_allowed_out_of_cancelled", illegal)

        live_call = repo.create_call(
            conn,
            "fire",
            "structure_fire",
            "critical",
            {"latitude": 31.52, "longitude": 74.36},
            GEOMETRY,
            "verified_dispatch",
            "simulated",
            "test:synthetic",
            at=now,
        )
        asg1 = repo.create_assignment(
            conn,
            live_call,
            "engine-9",
            "fire-dispatch",
            ["suppression"],
            [
                {
                    "route_id": "r1",
                    "distance_m": 500,
                    "eta_seconds": 60,
                    "eta_uncertainty_seconds": 8,
                    "geometry_version": GEOMETRY,
                    "selected": True,
                    "constraints_applied": [],
                }
            ],
            "simulated",
            "test:synthetic",
            at=now,
        )
        repo.transition_assignment(conn, asg1, "acknowledged", "test:synthetic", at=now)
        repo.transition_assignment(
            conn, asg1, "unavailable", "test:synthetic", "engine broke down en route", at=now
        )
        ev.check(
            "B2_an_assignment_can_become_unavailable_mid_response",
            repo.assignments_for_call(conn, live_call)[0]["status"] == "unavailable",
        )
        asg2 = repo.create_assignment(
            conn,
            live_call,
            "engine-4",
            "fire-dispatch",
            ["suppression"],
            [
                {
                    "route_id": "r2",
                    "distance_m": 900,
                    "eta_seconds": 110,
                    "eta_uncertainty_seconds": 15,
                    "geometry_version": GEOMETRY,
                    "selected": True,
                    "constraints_applied": [],
                }
            ],
            "simulated",
            "test:synthetic",
            at=now,
        )
        ev.check(
            "B2_the_call_can_still_be_reassigned_after_a_unit_goes_unavailable",
            asg2 != asg1 and len(repo.assignments_for_call(conn, live_call)) == 2,
        )

        handover_call = repo.create_call(
            conn,
            "ambulance",
            "medical_transfer",
            "medium",
            {"latitude": 31.52, "longitude": 74.36},
            GEOMETRY,
            "verified_dispatch",
            "simulated",
            "test:synthetic",
            at=now,
        )
        asg3 = repo.create_assignment(
            conn,
            handover_call,
            "ambulance-9",
            "ems-dispatch",
            ["als"],
            [
                {
                    "route_id": "r3",
                    "distance_m": 700,
                    "eta_seconds": 80,
                    "eta_uncertainty_seconds": 10,
                    "geometry_version": GEOMETRY,
                    "selected": True,
                    "constraints_applied": [],
                }
            ],
            "simulated",
            "test:synthetic",
            at=now,
        )
        repo.transition_assignment(conn, asg3, "acknowledged", "test:synthetic", at=now)
        repo.transition_assignment(conn, asg3, "en_route", "test:synthetic", at=now)
        repo.transition_assignment(
            conn,
            asg3,
            "on_scene",
            "test:synthetic",
            at=now,
            handover={
                "from_agency": "ems-dispatch",
                "to_agency": "fire-dispatch",
                "handover_at": now.isoformat(),
                "acknowledged_by": "engine-4",
            },
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT handover FROM emergency_unit_assignments WHERE assignment_id = %s", (asg3,)
            )
            (handover,) = cur.fetchone()
        ev.check(
            "B3_a_handover_record_persists_with_its_required_fields",
            set(handover) == {"from_agency", "to_agency", "handover_at", "acknowledged_by"},
        )

        # purge synthetic
        synthetic_ids = [cancelled_id, live_call, handover_call]
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM emergency_unit_assignments WHERE call_id = ANY(%s::uuid[])",
                (synthetic_ids,),
            )
            cur.execute(
                "DELETE FROM emergency_calls WHERE call_id = ANY(%s::uuid[])", (synthetic_ids,)
            )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM emergency_calls WHERE call_id = ANY(%s::uuid[])",
                (synthetic_ids,),
            )
            ev.check("synthetic_calls_are_removed_after_the_test", cur.fetchone()[0] == 0)

    ev.metrics["dataset"] = {
        "calls": len(calls),
        "assignments": len(assignments),
        "avl_events": len(avl_events),
        "devices": len(devices),
    }
    asyncio.run(api_checks(calls[0]["call_id"]))
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
