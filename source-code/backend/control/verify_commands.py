"""P07.05 acceptance evidence, against the real Postgres and the real seeded
network (P07.02):

    python source-code/backend/control/verify_commands.py

Proves the full request -> policy -> approve/deny path on real targets: role
enforcement, four-eyes, expiry, target validity, bounds (via a real
recommendation), the SAFE-03 fresh-evidence gate on real corridor KPI data,
a genuine policy-outage path that fails closed, and idempotency - a
resubmission with the same key never creates a second command.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

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
from backend.control.command_service import (  # noqa: E402
    RequestNotPermitted,
    deny_command,
    request_command,
    review_command,
)  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.repositories import commands as cmd_repo  # noqa: E402
from backend.repositories.incidents import create_incident  # noqa: E402
from backend.repositories.recommendations import create_recommendation  # noqa: E402
from backend.roles import EXECUTOR, NotPermitted, require_executor  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

GEOMETRY = "2026-09-18.1"
SCHEMA = json.loads(
    (SOURCE_ROOT / "contracts" / "command" / "v1" / "schema.json").read_text(encoding="utf-8")
)
HOST, PORT = "127.0.0.1", 8802
ev = Evidence("P07.05")


def run_server() -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("uvicorn did not start")


async def api_checks(command_id: str) -> None:
    server = run_server()
    try:
        async with httpx.AsyncClient(base_url=f"http://{HOST}:{PORT}", timeout=30) as client:
            page = (await client.get("/api/v1/commands", params={"limit": 500})).json()
            validator = Draft202012Validator(SCHEMA)
            errors = [e.message for item in page["items"] for e in validator.iter_errors(item)]
            ev.check(
                "api_commands_are_contract_valid",
                bool(page["items"]) and not errors,
                detail=f"{len(page['items'])} records; {errors[:1]}",
            )
            detail = (await client.get(f"/api/v1/commands/{command_id}")).json()
            ev.check(
                "api_command_detail_includes_transitions",
                detail["transitions"] and detail["transitions"][0]["to_status"] == "requested",
            )
            missing = await client.get(f"/api/v1/commands/{'0' * 8}-0000-0000-0000-{'0' * 12}")
            ev.check("api_unknown_command_is_404", missing.status_code == 404)
    finally:
        server.should_exit = True
        await asyncio.sleep(0.5)


def fresh_kpi(conn: psycopg.Connection, corridor_id: str, direction: str, now: datetime) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO corridor_kpis (corridor_id, direction, window_start, window_seconds, geometry_version, kpis, quality, coverage, "
            "sample_count, segments_reporting, segments_expected) VALUES (%s, %s, %s, 300, %s, %s::jsonb, 'valid', 1.0, 10, 1, 1) "
            "ON CONFLICT DO NOTHING",
            (
                corridor_id,
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


def main() -> int:
    with psycopg.connect(dsn_from_env()) as conn:
        now = datetime.now(timezone.utc)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM corridor_kpis WHERE window_start > %s", (now - timedelta(hours=1),)
            )
        conn.commit()
        fresh_kpi(conn, "corridor-a", "east", now)  # int-a2_int-a3 is corridor-a east

        # ---- happy path: role-authorized, four-eyes, fresh evidence, real target ----
        cmd_id, created = request_command(
            conn,
            "idem-happy-1",
            "diversion",
            "diversion_adapter",
            "int-a2_int-a3",
            "operator:alice",
            now,
            "operator",
        )
        ev.check("real_command_created_for_a_real_target", created)
        status = review_command(
            conn, cmd_id, "supervisor:bob", "supervisor", now + timedelta(seconds=5), GEOMETRY
        )
        ev.check(
            "a_valid_command_with_fresh_evidence_and_an_authorized_reviewer_is_approved",
            status == "approved",
        )
        stored = cmd_repo.get_command(conn, cmd_id)
        ev.check(
            "approval_records_who_and_when",
            stored["approved_by"] == "supervisor:bob"
            and stored["approved_at"] is not None
            and stored["policy_decision"] == "approved",
        )

        # ---- four-eyes: the requester cannot approve their own command ----
        cmd2, _ = request_command(
            conn,
            "idem-self-1",
            "diversion",
            "diversion_adapter",
            "int-a2_int-a3",
            "operator:alice",
            now,
            "operator",
        )
        status2 = review_command(
            conn, cmd2, "operator:alice", "supervisor", now + timedelta(seconds=5), GEOMETRY
        )
        ev.check(
            "the_requester_reviewing_their_own_command_is_genuinely_denied_not_silently_approved",
            status2 == "denied"
            and cmd_repo.get_command(conn, cmd2)["error_code"] == "policy_denied",
        )

        # ---- role: an unauthorized role is denied ----
        cmd3, _ = request_command(
            conn,
            "idem-role-1",
            "diversion",
            "diversion_adapter",
            "int-a2_int-a3",
            "operator:alice",
            now,
            "operator",
        )
        status3 = review_command(
            conn, cmd3, "operator:carol", "operator", now + timedelta(seconds=5), GEOMETRY
        )
        ev.check(
            "an_operator_may_not_approve_an_sc_1_action_only_a_supervisor_may",
            status3 == "denied"
            and cmd_repo.get_command(conn, cmd3)["error_code"] == "policy_denied",
        )

        # ---- expiry: a command reviewed after its own expires_at is denied and expire_stale sweeps it ----
        cmd4, _ = request_command(
            conn,
            "idem-expiry-1",
            "diversion",
            "diversion_adapter",
            "int-a2_int-a3",
            "operator:alice",
            now,
            "operator",
            ttl_s=1.0,
        )
        status4 = review_command(
            conn, cmd4, "supervisor:bob", "supervisor", now + timedelta(seconds=30), GEOMETRY
        )
        ev.check(
            "a_command_reviewed_after_its_own_expiry_moves_to_the_expired_status_not_denied",
            status4 == "expired"
            and cmd_repo.get_command(conn, cmd4)["status"] == "expired"
            and cmd_repo.get_command(conn, cmd4)["error_code"] == "expired",
        )
        cmd5, _ = request_command(
            conn,
            "idem-expiry-2",
            "diversion",
            "diversion_adapter",
            "int-a2_int-a3",
            "operator:alice",
            now,
            "operator",
            ttl_s=1.0,
        )
        swept = cmd_repo.expire_stale(conn, now + timedelta(seconds=30))
        ev.check(
            "a_never_reviewed_command_past_its_own_expiry_is_swept_to_expired",
            swept >= 1 and cmd_repo.get_command(conn, cmd5)["status"] == "expired",
        )

        # ---- target validity: unknown adapter and a target that does not exist ----
        cmd6, _ = request_command(
            conn,
            "idem-badadapter-1",
            "diversion",
            "made_up_adapter",
            "int-a2_int-a3",
            "operator:alice",
            now,
            "operator",
        )
        status6 = review_command(
            conn, cmd6, "supervisor:bob", "supervisor", now + timedelta(seconds=5), GEOMETRY
        )
        ev.check(
            "an_unknown_adapter_is_an_invalid_target",
            status6 == "denied"
            and cmd_repo.get_command(conn, cmd6)["error_code"] == "invalid_target",
        )
        cmd7, _ = request_command(
            conn,
            "idem-badentity-1",
            "diversion",
            "diversion_adapter",
            "segment-does-not-exist",
            "operator:alice",
            now,
            "operator",
        )
        status7 = review_command(
            conn, cmd7, "supervisor:bob", "supervisor", now + timedelta(seconds=5), GEOMETRY
        )
        ev.check(
            "a_target_entity_that_does_not_exist_on_the_real_network_is_an_invalid_target",
            status7 == "denied"
            and cmd_repo.get_command(conn, cmd7)["error_code"] == "invalid_target",
        )

        # ---- bounds via a real linked recommendation ----
        rec_id = create_recommendation(
            conn,
            "diversion",
            [
                {
                    "alternative_id": "a1",
                    "description": "x",
                    "predicted_benefit": [],
                    "predicted_harm": [],
                    "confidence": 0.5,
                }
            ],
            {"min_pedestrian_clearance_s": 7.0, "max_signal_deviation_s": 20.0},
            [],
            now,
            now + timedelta(seconds=600),
        )
        cmd8, _ = request_command(
            conn,
            "idem-linked-1",
            "diversion",
            "diversion_adapter",
            "int-a2_int-a3",
            "operator:alice",
            now,
            "operator",
            recommendation_id=rec_id,
        )
        status8 = review_command(
            conn, cmd8, "supervisor:bob", "supervisor", now + timedelta(seconds=5), GEOMETRY
        )
        ev.check(
            "a_command_linked_to_a_live_matching_recommendation_is_approved", status8 == "approved"
        )
        mismatched = create_recommendation(
            conn,
            "signal_plan_change",
            [
                {
                    "alternative_id": "a1",
                    "description": "x",
                    "predicted_benefit": [],
                    "predicted_harm": [],
                    "confidence": 0.5,
                }
            ],
            {"min_pedestrian_clearance_s": 7.0, "max_signal_deviation_s": 20.0},
            [],
            now,
            now + timedelta(seconds=600),
        )
        cmd9, _ = request_command(
            conn,
            "idem-mismatch-1",
            "diversion",
            "diversion_adapter",
            "int-a2_int-a3",
            "operator:alice",
            now,
            "operator",
            recommendation_id=mismatched,
        )
        status9 = review_command(
            conn, cmd9, "supervisor:bob", "supervisor", now + timedelta(seconds=5), GEOMETRY
        )
        ev.check(
            "a_command_whose_action_type_does_not_match_its_recommendation_is_denied",
            status9 == "denied"
            and cmd_repo.get_command(conn, cmd9)["error_code"] == "policy_denied",
        )

        # ---- SAFE-03: stale evidence is genuinely denied, on a real target with no recent KPI ----
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM corridor_kpis WHERE corridor_id = 'corridor-c' AND window_start > %s",
                (now - timedelta(hours=1),),
            )
        conn.commit()
        cmd10, _ = request_command(
            conn,
            "idem-stale-1",
            "diversion",
            "diversion_adapter",
            "int-c1_int-c2",
            "operator:alice",
            now,
            "operator",
        )
        status10 = review_command(
            conn, cmd10, "supervisor:bob", "supervisor", now + timedelta(seconds=5), GEOMETRY
        )
        ev.check(
            "a_real_target_with_no_recent_kpi_window_is_denied_stale_evidence_safe_03",
            status10 == "denied"
            and cmd_repo.get_command(conn, cmd10)["error_code"] == "stale_evidence",
        )
        ev.check(
            "stale_evidence_denials_are_marked_retryable_the_operator_can_ask_again_once_fresh",
            cmd_repo.get_command(conn, cmd10)["error_retryable"] is True,
        )

        # ---- policy outage: the policy evaluation itself fails -> fails CLOSED, never silently approved ----
        cmd11, _ = request_command(
            conn,
            "idem-outage-1",
            "diversion",
            "diversion_adapter",
            "int-a2_int-a3",
            "operator:alice",
            now,
            "operator",
        )
        with patch(
            "backend.control.policy.build_context",
            side_effect=RuntimeError("simulated policy engine outage"),
        ):
            status11 = review_command(
                conn, cmd11, "supervisor:bob", "supervisor", now + timedelta(seconds=5), GEOMETRY
            )
        after_outage = cmd_repo.get_command(conn, cmd11)
        ev.check(
            "a_policy_engine_outage_leaves_the_command_requested_not_approved_fail_closed",
            status11 == "requested"
            and after_outage["status"] == "requested"
            and after_outage["policy_decision"] == "policy_unavailable",
        )
        # once the outage clears, the same command can still be reviewed normally
        status11b = review_command(
            conn, cmd11, "supervisor:bob", "supervisor", now + timedelta(seconds=10), GEOMETRY
        )
        ev.check(
            "the_command_can_be_reviewed_normally_once_the_policy_engine_recovers",
            status11b == "approved",
        )

        # ---- idempotency: a resubmission with the same key never creates a second command ----
        again_id, created_again = request_command(
            conn,
            "idem-happy-1",
            "diversion",
            "diversion_adapter",
            "int-a2_int-a3",
            "operator:alice",
            now,
            "operator",
        )
        ev.check(
            "resubmitting_the_same_idempotency_key_returns_the_existing_command_not_a_new_one",
            again_id == cmd_id and not created_again,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM commands WHERE idempotency_key = 'idem-happy-1'")
            ev.check(
                "exactly_one_row_exists_for_that_idempotency_key_at_the_database_level",
                cur.fetchone()[0] == 1,
            )

        # ---- the repository's own state machine rejects an illegal transition regardless of policy ----
        illegal = False
        try:
            cmd_repo.transition_command(conn, cmd_id, "requested", "supervisor:bob")
        except cmd_repo.InvalidTransition:
            illegal = True
        ev.check("the_state_machine_rejects_an_illegal_transition_independent_of_policy", illegal)

        # ---- P02.08 roles and safety classes ----
        ev.check(
            "approval_records_the_requesters_and_approvers_operating_roles",
            stored["requested_by_role"] == "operator"
            and stored["approved_by_role"] == "supervisor",
        )
        refused_role = False
        try:  # a dispatcher holds REQUEST only for SC-2; a diversion is SC-1
            request_command(
                conn,
                "idem-dispatcher-sc1",
                "diversion",
                "diversion_adapter",
                "int-a2_int-a3",
                "dispatcher:dana",
                now,
                "dispatcher",
            )
        except RequestNotPermitted:
            refused_role = True
        ev.check("a_dispatcher_may_not_request_an_sc_1_traffic_action", refused_role)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM commands WHERE idempotency_key = 'idem-dispatcher-sc1'"
            )
            ev.check("a_refused_request_leaves_no_command_behind", cur.fetchone()[0] == 0)

        vms, _ = request_command(
            conn,
            "idem-sc0-1",
            "variable_message_sign",
            "vms_adapter",
            "int-a2_int-a3",
            "operator:alice",
            now,
            "operator",
        )
        ev.check(
            "sc_0_advisory_needs_no_second_person_the_requesting_operator_may_approve_it",
            review_command(
                conn, vms, "operator:alice", "operator", now + timedelta(seconds=5), GEOMETRY
            )
            == "approved",
        )

        pre, _ = request_command(
            conn,
            "idem-sc2-1",
            "emergency_preemption",
            "emergency_preemption_adapter",
            "corridor-a",
            "dispatcher:dana",
            now,
            "dispatcher",
        )
        ev.check(
            "an_operator_may_not_approve_an_sc_2_action",
            review_command(
                conn, pre, "operator:carol", "operator", now + timedelta(seconds=5), GEOMETRY
            )
            == "denied",
        )
        pre2, _ = request_command(
            conn,
            "idem-sc2-2",
            "emergency_preemption",
            "emergency_preemption_adapter",
            "corridor-a",
            "dispatcher:dana",
            now,
            "dispatcher",
        )
        ev.check(
            "an_incident_commander_approves_a_dispatchers_emergency_preemption_with_a_second_person",
            review_command(
                conn,
                pre2,
                "commander:eve",
                "incident_commander",
                now + timedelta(seconds=5),
                GEOMETRY,
            )
            == "approved",
        )
        pre3, _ = request_command(
            conn,
            "idem-sc2-3",
            "emergency_preemption",
            "emergency_preemption_adapter",
            "corridor-a",
            "dispatcher:dana",
            now,
            "dispatcher",
        )
        ev.check(
            "sc_2_four_eyes_holds_even_for_an_incident_commander",
            review_command(
                conn,
                pre3,
                "dispatcher:dana",
                "incident_commander",
                now + timedelta(seconds=5),
                GEOMETRY,
            )
            == "denied",
        )

        critical = create_incident(
            conn,
            "collision",
            "critical",
            "segment",
            "int-a2_int-a3",
            GEOMETRY,
            [str(uuid.uuid4())],
            "EMERG",
            0.95,
            "test:synthetic",
            at=now,
        )
        esc, _ = request_command(
            conn,
            "idem-escalated-1",
            "diversion",
            "diversion_adapter",
            "int-a2_int-a3",
            "dispatcher:dana",
            now,
            "dispatcher",
            geometry=GEOMETRY,
        )
        ev.check(
            "an_active_critical_incident_on_the_target_raises_any_action_to_sc_2_so_a_dispatcher_may_request_it",
            esc is not None,
        )
        ev.check(
            "and_an_operator_may_no_longer_approve_it",
            review_command(
                conn, esc, "operator:carol", "operator", now + timedelta(seconds=5), GEOMETRY
            )
            == "denied",
        )
        esc2, _ = request_command(
            conn,
            "idem-escalated-2",
            "diversion",
            "diversion_adapter",
            "int-a2_int-a3",
            "dispatcher:dana",
            now,
            "dispatcher",
            geometry=GEOMETRY,
        )
        ev.check(
            "while_an_incident_commander_can",
            review_command(
                conn,
                esc2,
                "commander:eve",
                "incident_commander",
                now + timedelta(seconds=5),
                GEOMETRY,
            )
            == "approved",
        )
        with conn.cursor() as cur:
            cur.execute("DELETE FROM incident_transitions WHERE incident_id = %s", (critical,))
            cur.execute("DELETE FROM incidents WHERE incident_id = %s", (critical,))
        conn.commit()

        denied_by_human, _ = request_command(
            conn,
            "idem-human-deny-1",
            "diversion",
            "diversion_adapter",
            "int-a2_int-a3",
            "operator:alice",
            now,
            "operator",
        )
        deny_command(
            conn,
            denied_by_human,
            "supervisor:bob",
            "supervisor",
            "traffic is already recovering",
            now + timedelta(seconds=5),
            GEOMETRY,
        )
        human_denied = cmd_repo.get_command(conn, denied_by_human)
        ev.check(
            "a_reviewer_can_deny_with_a_recorded_reason",
            human_denied["status"] == "denied"
            and human_denied["error_code"] == "policy_denied"
            and "already recovering" in human_denied["error_message"],
        )
        cannot_deny = False
        try:
            other, _ = request_command(
                conn,
                "idem-human-deny-2",
                "diversion",
                "diversion_adapter",
                "int-a2_int-a3",
                "operator:alice",
                now,
                "operator",
            )
            deny_command(
                conn,
                other,
                "operator:carol",
                "operator",
                "no authority",
                now + timedelta(seconds=5),
                GEOMETRY,
            )
        except RequestNotPermitted:
            cannot_deny = True
        ev.check("only_a_role_that_may_approve_the_class_may_deny_it", cannot_deny)

        not_executor = False
        try:
            require_executor("supervisor:bob")
        except NotPermitted:
            not_executor = True
        require_executor(EXECUTOR)
        ev.check("execute_authority_belongs_to_system_command_executor_alone", not_executor)

        asyncio.run(api_checks(cmd_id))

        with conn.cursor() as cur:
            cur.execute("DELETE FROM commands WHERE idempotency_key LIKE 'idem-%'")
            cur.execute(
                "DELETE FROM recommendations WHERE recommendation_id = ANY(%s::uuid[])",
                ([rec_id, mismatched],),
            )
            cur.execute(
                "DELETE FROM corridor_kpis WHERE corridor_id = 'corridor-a' AND window_start = %s",
                (now - timedelta(minutes=1),),
            )
        conn.commit()
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
