"""P05.08 acceptance evidence, run against the real P05.01/P05.04 Postgres:

    python source-code/backend/repositories/verify_repositories.py

Proves valid/invalid transitions, idempotent command creation, required
audit references, and - the part that needs a real database, not a mock -
that two concurrent transition attempts on the same row serialize correctly
via row locking rather than racing to a lost update.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.repositories import commands, incidents  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

GEOMETRY_VERSION = "2026-09-18.1"
OUTPUT_DIR = SOURCE_ROOT / "infra" / "platform" / "output"

results: dict[str, bool] = {}
notes: dict[str, str] = {}


def check(name: str, ok: bool, detail: str = "") -> None:
    results[name] = ok
    notes[name] = detail
    print(f"{'PASS' if ok else 'FAIL'}: {name} {detail}")


def main() -> int:
    with psycopg.connect(dsn_from_env()) as conn:
        # ---- incidents: valid transition chain ----
        incident_id = incidents.create_incident(
            conn,
            "collision",
            "high",
            "lane",
            "verify-repo-lane",
            GEOMETRY_VERSION,
            ["11111111-1111-1111-1111-111111111111"],
            "OPS",
            0.8,
            "verify-script",
        )
        incidents.transition_incident(conn, incident_id, "acknowledged", "verify-script")
        incidents.transition_incident(conn, incident_id, "investigating", "verify-script")
        incidents.transition_incident(conn, incident_id, "resolved", "verify-script", note="fixed")
        state = incidents.get_incident(conn, incident_id)
        check(
            "incident_valid_transition_chain_succeeds",
            state["status"] == "resolved" and state["resolved_at"] is not None,
            detail=str(state),
        )

        history = incidents.transition_history(conn, incident_id)
        check(
            "incident_audit_trail_records_every_transition_in_order",
            [h["to_status"] for h in history]
            == ["open", "acknowledged", "investigating", "resolved"],
            detail=str([h["to_status"] for h in history]),
        )

        # ---- incidents: invalid transition rejected, no partial change ----
        try:
            incidents.transition_incident(conn, incident_id, "investigating", "verify-script")
            invalid_rejected = False
        except incidents.InvalidTransition:
            invalid_rejected = True
        state_after = incidents.get_incident(conn, incident_id)
        check(
            "incident_invalid_transition_rejected_and_status_unchanged",
            invalid_rejected and state_after["status"] == "resolved",
            detail=str(state_after),
        )

        try:
            incidents.transition_incident(
                conn, "00000000-0000-0000-0000-000000000000", "resolved", "verify-script"
            )
            not_found_raised = False
        except incidents.IncidentNotFound:
            not_found_raised = True
        check("incident_transition_on_unknown_id_raises_not_found", not_found_raised)

        # ---- commands: idempotent creation ----
        key = f"verify-repo-idem-{time.time()}"
        cmd_id_1, created_1 = commands.create_command(
            conn,
            key,
            "diversion",
            "signal-adapter",
            "int-a1",
            "verify-script",
            expires_in_seconds=3600,
            policy_decision="pending",
        )
        cmd_id_2, created_2 = commands.create_command(
            conn,
            key,
            "diversion",
            "signal-adapter",
            "int-a1",
            "verify-script",
            expires_in_seconds=3600,
            policy_decision="pending",
        )
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM commands WHERE idempotency_key = %s", (key,))
            (row_count,) = cur.fetchone()
        check(
            "command_idempotency_key_resubmission_returns_same_id_no_duplicate_row",
            cmd_id_1 == cmd_id_2 and created_1 is True and created_2 is False and row_count == 1,
            detail=f"id1={cmd_id_1} id2={cmd_id_2} created=({created_1},{created_2}) rows={row_count}",
        )

        # ---- commands: audit reference required for 'approved' ----
        try:
            commands.transition_command(conn, cmd_id_1, "approved", "verify-script")
            missing_audit_raised = False
        except commands.MissingAuditReference:
            missing_audit_raised = True
        check("command_approve_without_approved_by_is_refused", missing_audit_raised)

        commands.transition_command(
            conn, cmd_id_1, "approved", "verify-script", approved_by="ops-lead"
        )
        cmd_state = commands.get_command(conn, cmd_id_1)
        check(
            "command_approve_with_audit_reference_records_it",
            cmd_state["status"] == "approved"
            and cmd_state["approved_by"] == "ops-lead"
            and cmd_state["approved_at"] is not None,
            detail=str(cmd_state),
        )

        # ---- commands: invalid transition rejected ----
        try:
            commands.transition_command(conn, cmd_id_1, "denied", "verify-script")
            cmd_invalid_rejected = False
        except commands.InvalidTransition:
            cmd_invalid_rejected = True
        check("command_invalid_transition_from_approved_to_denied_rejected", cmd_invalid_rejected)

        # ---- commands: 'failed' requires structured error ----
        commands.transition_command(conn, cmd_id_1, "executing", "verify-script")
        try:
            commands.transition_command(conn, cmd_id_1, "failed", "verify-script")
            failed_without_error_raised = False
        except commands.MissingAuditReference:
            failed_without_error_raised = True
        check("command_fail_without_error_fields_is_refused", failed_without_error_raised)
        commands.transition_command(
            conn,
            cmd_id_1,
            "failed",
            "verify-script",
            error_code="adapter_unreachable",
            error_message="timeout",
            error_retryable=True,
        )
        failed_state = commands.get_command(conn, cmd_id_1)
        check(
            "command_fail_with_error_fields_recorded",
            failed_state["status"] == "failed"
            and failed_state["error_code"] == "adapter_unreachable",
        )

    # ---- concurrency: two transitions race on the same incident row ----
    concurrent_incident_id = None
    with psycopg.connect(dsn_from_env()) as setup_conn:
        concurrent_incident_id = incidents.create_incident(
            setup_conn,
            "congestion",
            "medium",
            "lane",
            "verify-repo-lane-concurrent",
            GEOMETRY_VERSION,
            ["22222222-2222-2222-2222-222222222222"],
            "OPS",
            0.6,
            "verify-script",
        )

    outcomes: dict[str, str] = {}

    def attempt(name: str, target: str) -> None:
        with psycopg.connect(dsn_from_env()) as conn2:
            try:
                incidents.transition_incident(
                    conn2, concurrent_incident_id, target, f"thread-{name}"
                )
                outcomes[name] = f"succeeded:{target}"
            except incidents.InvalidTransition:
                outcomes[name] = "invalid_transition"

    t_a = threading.Thread(target=attempt, args=("A", "escalated"))
    t_b = threading.Thread(target=attempt, args=("B", "acknowledged"))
    t_a.start()
    t_b.start()
    t_a.join(timeout=10)
    t_b.join(timeout=10)

    with psycopg.connect(dsn_from_env()) as conn3:
        final = incidents.get_incident(conn3, concurrent_incident_id)

    successes = [v for v in outcomes.values() if v.startswith("succeeded")]
    failures = [v for v in outcomes.values() if v == "invalid_transition"]
    winning_target = successes[0].split(":")[1] if successes else None
    check(
        "concurrent_transitions_serialize_exactly_one_wins",
        len(successes) == 1 and len(failures) == 1 and final["status"] == winning_target,
        detail=f"outcomes={outcomes} final_status={final['status'] if final else None}",
    )

    all_passed = all(results.values())
    evidence = {
        "task": "P05.08",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": results,
        "notes": notes,
        "all_passed": all_passed,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "p05_08_evidence.json"
    out_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f">> Evidence written to {out_path}")
    print("P05.08: ALL CHECKS PASSED" if all_passed else "P05.08: ONE OR MORE CHECKS FAILED")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
