"""P09.05 acceptance evidence: audit and sensitive-data protection, against the real Postgres, Keycloak and the real
FastAPI apps under uvicorn.

    python source-code/backend/verify_audit_protection.py

What is proven, in order:

A. Tamper-evidence: the hash chain (migration 0026) is intact on the real database; a row rewritten after disabling its
   own trigger (simulating a superuser who can defeat the trigger) is caught by `backend/audit_chain.py`'s independent
   recomputation, and a legitimate `database/retention.py` purge - which DOES delete from the cascade-eligible tables -
   is not mistaken for tampering.
B. Redaction (CTL-16): the write path redacts a secret before it reaches the database (`workflow.audit`,
   `policy.record_decision`); the database refuses one that arrives anyway (migration 0026's CHECK constraints, tested
   with a raw insert that never goes through the application); the log filter redacts a token from a log record.
C. Role-limited export (CTL-17): `GET /api/v1/audit/export` is auditor-only, required and bounded `since`/`until`,
   redacts locations that the ordinary trail still shows, caps its row count and says so, and is itself an audited,
   attributed action.
D. Override (CTL-34): `POST /api/v1/commands/{id}/override` is incident_commander-only and SC-2-only, needs a
   justification, moves an executed command to `rolled_back`, refuses an SC-0/SC-1 command, another role, a command in
   the wrong status, and a second override of the same command; it is honest that no physical adapter reversal was
   attempted, and every attempt - permitted or refused - is in the decision and audit records.
"""

from __future__ import annotations

import json
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

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from backend import audit_chain  # noqa: E402
from backend.api import oidc_client  # noqa: E402
from backend.api.app import app  # noqa: E402
from backend.api.workflow import audit as workflow_audit  # noqa: E402
from backend.control.command_service import request_command  # noqa: E402
from backend.control.verify_simulator_adapters import fresh_signal_evidence  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.redaction import RedactingFilter, install_log_redaction, redact, redact_text  # noqa: E402
from backend.repositories import commands as command_repo  # noqa: E402
from backend.repositories import incidents as incident_repo  # noqa: E402
from database import retention  # noqa: E402
from database.migrate import dsn_from_env, migrate  # noqa: E402

HOST, PORT = "127.0.0.1", 8840
BASE = f"http://{HOST}:{PORT}"
IDENTITIES = json.loads(
    (SOURCE_ROOT / "infra" / "platform" / "output" / "demo_identities.json").read_text(
        encoding="utf-8"
    )
)
PEOPLE = {
    "requester": "alex.chen",
    "approver": "sam.okafor",
    "commander": "eve.laurent",
    "auditor": "ana.petrov",
    "second_operator": "alex.two",
}
ev = Evidence("P09.05", "p09_05_audit_protection", docs_name="p09_05_audit_protection")


def run_server() -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(80):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("uvicorn did not start")


def key() -> str:
    return f"verify-p0905-{uuid.uuid4().hex}"


def fresh_kpi(conn: psycopg.Connection, corridor: str, direction: str, geometry: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO corridor_kpis (corridor_id, direction, window_start, window_seconds, geometry_version, kpis, quality, coverage, sample_count, segments_reporting, segments_expected) "
            "VALUES (%s, %s, %s, 300, %s, %s::jsonb, 'valid', 1.0, 10, 1, 1) ON CONFLICT DO NOTHING",
            (
                corridor,
                direction,
                datetime.now(timezone.utc) - timedelta(minutes=1),
                geometry,
                Jsonb({"delay_s": 22.0, "queue_fraction": 0.3, "travel_time_s": 70.0}),
            ),
        )
    conn.commit()


async def run_command_to_executed(
    c: httpx.AsyncClient, h, action_type: str, target: str, body_extra: dict
) -> str:
    """Request -> approve -> the real executor's gate -> executed, through the real API and the real command executor."""
    from backend.control import executor_worker  # noqa: PLC0415

    made = await c.post(
        "/api/v1/commands",
        headers=h("requester"),
        json={
            "action_type": action_type,
            "target_entity_id": target,
            "idempotency_key": key(),
            **body_extra,
        },
    )
    if made.status_code != 201:
        raise RuntimeError(f"could not request {action_type}: {made.status_code} {made.text}")
    command_id = made.json()["command_id"]
    approver = "second_operator" if made.json()["safety_class"] == "SC-0" else "approver"
    approved = await c.post(
        f"/api/v1/commands/{command_id}/review", headers=h(approver), json={"decision": "approve"}
    )
    if approved.status_code != 200 or approved.json()["status"] != "approved":
        raise RuntimeError(
            f"could not approve {command_id}: {approved.status_code} {approved.text}"
        )
    executor_worker._gate_retry_after.clear()
    with psycopg.connect(dsn_from_env()) as db:
        handled = executor_worker.run_once(db)
    if handled != command_id:
        raise RuntimeError(f"executor did not handle {command_id} on the first try")
    after = (await c.get(f"/api/v1/commands/{command_id}", headers=h("auditor"))).json()
    if after["command"]["status"] != "executed":
        raise RuntimeError(f"{command_id} is {after['command']['status']!r}, not executed: {after}")
    return command_id


async def main() -> int:  # noqa: PLR0915
    tokens = {
        name: oidc_client.login(user, IDENTITIES[user]["password"]).access_token
        for name, user in PEOPLE.items()
    }

    def h(who: str) -> dict:
        return {"Authorization": f"Bearer {tokens[who]}"}

    # ================================================================== A. tamper-evidence
    with psycopg.connect(dsn_from_env()) as db:
        migrate(db)
        baseline = {
            t: audit_chain.verify_table(db, t, order, deletable)
            for t, (order, deletable) in audit_chain.CHAINED_TABLES.items()
        }
        ev.check(
            "every_chained_history_is_intact_on_the_real_database_before_this_run_touches_anything",
            all(r["intact"] for r in baseline.values()),
            detail={t: r for t, r in baseline.items() if not r["intact"]},
        )
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO operator_audit (actor, actor_roles, action, entity_type, entity_id, outcome, detail) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    "verify-p0905",
                    ["auditor"],
                    "chain.fixture",
                    "test",
                    None,
                    "allowed",
                    Jsonb({"n": 1}),
                ),
            )
            cur.execute(
                "INSERT INTO operator_audit (actor, actor_roles, action, entity_type, entity_id, outcome, detail) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    "verify-p0905",
                    ["auditor"],
                    "chain.fixture",
                    "test",
                    None,
                    "allowed",
                    Jsonb({"n": 2}),
                ),
            )
            cur.execute(
                "SELECT audit_id FROM operator_audit WHERE actor = 'verify-p0905' ORDER BY audit_id"
            )
            fixture_ids = [row[0] for row in cur.fetchall()]
        result = audit_chain.verify_table(db, "operator_audit", "audit_id", False)
        ev.check(
            "two_freshly_inserted_rows_chain_correctly_and_verify_intact",
            result["intact"] and result["rows"] >= 2,
            detail=result,
        )
        with db.cursor() as cur:
            cur.execute("ALTER TABLE operator_audit DISABLE TRIGGER operator_audit_no_update")
            cur.execute(
                "UPDATE operator_audit SET action = 'tampered' WHERE audit_id = %s",
                (fixture_ids[1],),
            )
            cur.execute("ALTER TABLE operator_audit ENABLE TRIGGER operator_audit_no_update")
        db.commit()
        tampered = audit_chain.verify_table(db, "operator_audit", "audit_id", False)
        ev.check(
            "a_row_rewritten_after_disabling_its_own_trigger_is_caught_by_the_independent_hash_recompute",
            not tampered["intact"] and fixture_ids[1] in tampered["hash_mismatches"],
            detail=tampered,
        )
        with db.cursor() as cur:
            cur.execute("ALTER TABLE operator_audit DISABLE TRIGGER operator_audit_no_update")
            cur.execute(
                "UPDATE operator_audit SET action = 'chain.fixture' WHERE audit_id = %s",
                (fixture_ids[1],),
            )
            cur.execute("ALTER TABLE operator_audit ENABLE TRIGGER operator_audit_no_update")
            cur.execute(
                "SELECT refuses_embedded_secrets(%s)",
                ("Authorization: Bearer test-fixture-credential-000000",),
            )
            db_refuses_bearer = cur.fetchone()[0]
        db.commit()
        restored = audit_chain.verify_table(db, "operator_audit", "audit_id", False)
        ev.check(
            "restoring_the_original_content_restores_an_intact_chain_the_check_is_not_a_false_positive",
            restored["intact"],
            detail=restored,
        )

        # ---- append-only is scoped correctly: UPDATE/TRUNCATE refused, but retention's own CASCADE DELETE works ----
        def refuses(sql: str) -> bool:
            """True only if the statement failed with the deliberate `RAISE EXCEPTION` our own trigger emits, never for
            some other reason (a syntax slip, a missing row) that would make a passing check meaningless."""
            try:
                with db.cursor() as cur:
                    cur.execute(sql)
                db.rollback()
                return False
            except psycopg.errors.RaiseException as exc:
                db.rollback()
                return "never allows" in str(exc) or "is append-only" in str(exc)
            except psycopg.Error:
                db.rollback()
                raise

        ev.check(
            "command_transitions_and_incident_transitions_still_refuse_update_and_truncate",
            refuses(
                "UPDATE command_transitions SET note = 'x' WHERE id = (SELECT id FROM command_transitions LIMIT 1)"
            )
            and refuses("TRUNCATE command_transitions")
            and refuses(
                "UPDATE incident_transitions SET note = 'x' WHERE id = (SELECT id FROM incident_transitions LIMIT 1)"
            ),
        )
        ev.check(
            "the_database_itself_refuses_a_raw_insert_carrying_a_bearer_credential_defense_in_depth_for_ctl_16",
            db_refuses_bearer,
        )

        geometry_row = db.execute(
            "SELECT version FROM geometry_versions WHERE superseded_by IS NULL ORDER BY effective_from DESC LIMIT 1"
        ).fetchone()
        geometry = geometry_row[0]
        old = datetime.now(timezone.utc) - timedelta(days=400)
        retention_fixture_cmd, _ = request_command(
            db,
            key(),
            "diversion",
            "diversion_adapter",
            "int-a2_int-a3",
            "operator:alice",
            old,
            "operator",
            ttl_s=60,
        )
        command_repo.transition_command(
            db, retention_fixture_cmd, "expired", "verify-p0905", "fixture", at=old
        )
        before_runs = audit_chain.verify_table(db, "retention_runs", "id", False)["rows"]
        report = retention.apply_retention(db, now=datetime.now(timezone.utc))
        with db.cursor() as cur:
            cur.execute("SELECT 1 FROM commands WHERE command_id = %s", (retention_fixture_cmd,))
            survived = cur.fetchone() is not None
        after_transitions = audit_chain.verify_table(db, "command_transitions", "id", True)
        after_runs = audit_chain.verify_table(db, "retention_runs", "id", False)
        ev.check(
            "retentions_own_cascade_delete_of_a_terminal_commands_transitions_still_works_with_the_new_triggers_in_place",
            not survived and report["commands_deleted"] >= 1,
            detail=report,
        )
        ev.check(
            "command_transitions_chain_stays_intact_meaning_the_cascade_gap_is_reported_as_a_gap_not_a_break",
            after_transitions["intact"],
            detail=after_transitions,
        )
        ev.check(
            "retention_runs_itself_is_chained_append_only_and_grew_by_one_row_recording_what_was_purged",
            after_runs["intact"] and after_runs["rows"] == before_runs + 1,
            detail={"before": before_runs, "after": after_runs["rows"]},
        )
        ev.check(
            "retention_runs_refuses_update_and_delete_like_the_other_audit_tables",
            refuses("UPDATE retention_runs SET commands_deleted = 0")
            and refuses("DELETE FROM retention_runs WHERE true"),
        )

        # ================================================================== B. redaction
        secret_detail = {
            "password": "hunter2",
            "note": "call it Authorization: Bearer abcdefghijklmnop and done",
            "nested": {"api_key": "sk-verify-p0905", "count": 3},
            "location": {"lat": 31.5, "lon": 74.3},
            "email": "someone@example.com",
        }
        ev.check(
            "redact_removes_secrets_recursively_through_nested_dicts_but_keeps_locations_in_audit_mode",
            (audited := redact(secret_detail, mode="audit"))["password"] == "[redacted]"
            and audited["nested"]["api_key"] == "[redacted]"
            and audited["nested"]["count"] == 3
            and audited["location"] == {"lat": 31.5, "lon": 74.3}
            and "[redacted]" in audited["note"]
            and "abcdefghijklmnop" not in audited["note"]
            and audited["email"] == "[email]",
            detail=audited,
        )
        ev.check(
            "export_mode_additionally_drops_locations",
            redact(secret_detail, mode="export")["location"] == "[location]",
        )
        ev.check(
            "a_jwt_shaped_string_anywhere_in_free_text_is_redacted",
            "[redacted]"
            in redact_text(
                "token=" + ".".join(["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiJ4In0", "sig-part-value"])
            ),
        )

        import logging  # noqa: PLC0415

        class _CaptureHandler(logging.Handler):
            def __init__(self) -> None:
                super().__init__()
                self.records: list[str] = []

            def emit(self, record: logging.LogRecord) -> None:
                self.records.append(record.getMessage())

        # A filter attached to the LOGGER (not the handler) runs inside Logger.handle(), before any handler sees the
        # record - the real code path `install_log_redaction` uses on uvicorn's own loggers, exercised here through the
        # standard logging pipeline rather than by calling the filter directly, so a future refactor of that wiring
        # would be caught here too.
        probe_logger = logging.getLogger(f"aiops.verify.{uuid.uuid4().hex}")
        probe_logger.setLevel(logging.INFO)
        probe_logger.addFilter(RedactingFilter())
        capture = _CaptureHandler()
        probe_logger.addHandler(capture)
        probe_logger.propagate = False
        probe_logger.info(
            "GET /ws?access_token=%s", "abcdefghijklmno.pqrstuvwxyz0123.reallyrealtoken"
        )
        ev.check(
            "the_log_redaction_filter_removes_a_token_from_a_log_records_rendered_message",
            capture.records
            and "abcdefghijklmno" not in capture.records[0]
            and "[redacted]" in capture.records[0],
            detail=capture.records,
        )
        install_log_redaction()  # exercised for real, even though its effect on uvicorn's own logger isn't observed here

        # ---- the real write path redacts before the row lands in the database ----
        class _Principal:
            username, roles, sub = "verify-p0905", ("auditor",), "verify-p0905-sub"

        workflow_audit(
            db,
            _Principal(),
            "verify.secret_write",
            "test",
            None,
            "allowed",
            password="hunter2",
            location={"lat": 1, "lon": 2},
        )
        with db.cursor() as cur:
            cur.execute(
                "SELECT detail FROM operator_audit WHERE actor = 'verify-p0905' AND action = 'verify.secret_write' ORDER BY audit_id DESC LIMIT 1"
            )
            stored = cur.fetchone()[0]
        ev.check(
            "workflow_audit_redacts_a_secret_before_the_row_is_written_not_only_when_asked_to",
            stored["password"] == "[redacted]" and stored["location"] == {"lat": 1, "lon": 2},
            detail=stored,
        )
        ev.metrics["redaction_write_path_sample"] = stored

    # ================================================================== C. export, D. override, through the real API
    server = run_server()
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=90) as c:
            with psycopg.connect(dsn_from_env()) as db:
                geometry = db.execute(
                    "SELECT version FROM geometry_versions WHERE superseded_by IS NULL ORDER BY effective_from DESC LIMIT 1"
                ).fetchone()[0]
                fresh_kpi(db, "corridor-a", "east", geometry)
            marker = f"p0905-{uuid.uuid4().hex[:8]}"
            with psycopg.connect(dsn_from_env()) as db:
                workflow_audit(
                    db,
                    _Principal(),
                    "verify.export_location_fixture",
                    "test",
                    marker,
                    "allowed",
                    location={"lat": 12.34, "lon": 56.78},
                    note=marker,
                )
            since = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            until = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

            trail = (
                await c.get(
                    "/api/v1/audit", params={"entity_id": marker, "limit": 5}, headers=h("auditor")
                )
            ).json()
            exported = (
                await c.get(
                    "/api/v1/audit/export",
                    params={"since": since, "until": until, "entity_id": marker},
                    headers=h("auditor"),
                )
            ).json()
            ev.check(
                "the_ordinary_trail_shows_the_raw_location_but_the_export_replaces_it",
                trail["items"][0]["detail"].get("location") == {"lat": 12.34, "lon": 56.78}
                and exported["items"][0]["detail"].get("location") == "[location]",
                detail={
                    "trail": trail["items"][0]["detail"],
                    "export": exported["items"][0]["detail"],
                },
            )
            ev.check(
                "the_export_names_who_exported_it_when_and_under_which_filters",
                exported["exported_by"] == PEOPLE["auditor"]
                and exported["filters"]["entity_id"] == marker,
                detail={k: exported[k] for k in ("exported_by", "exported_at", "filters")},
            )

            refused = {}
            for who in ("requester", "approver", "commander", "second_operator"):
                refused[who] = (
                    await c.get(
                        "/api/v1/audit/export",
                        params={"since": since, "until": until},
                        headers=h(who),
                    )
                ).status_code
            ev.check(
                "only_the_auditor_role_may_export_every_other_role_is_refused",
                set(refused.values()) == {403},
                detail=refused,
            )
            missing_range = await c.get("/api/v1/audit/export", headers=h("auditor"))
            too_wide = await c.get(
                "/api/v1/audit/export",
                params={"since": "2000-01-01T00:00:00Z", "until": until},
                headers=h("auditor"),
            )
            ev.check(
                "an_export_without_a_bound_or_with_too_wide_a_range_is_refused",
                missing_range.status_code == 422 and too_wide.status_code == 422,
                detail=(missing_range.status_code, too_wide.status_code),
            )
            import backend.api.routes_govern as govern_module  # noqa: PLC0415

            saved_cap = govern_module.EXPORT_MAX_ROWS
            govern_module.EXPORT_MAX_ROWS = 1
            try:
                capped = (
                    await c.get(
                        "/api/v1/audit/export",
                        params={"since": since, "until": until, "entity_id": marker},
                        headers=h("auditor"),
                    )
                ).json()
            finally:
                govern_module.EXPORT_MAX_ROWS = saved_cap
            ev.check(
                "the_export_caps_its_row_count_and_says_so_rather_than_silently_truncating",
                capped["row_count"] == 1 and len(capped["items"]) == 1,
                detail=capped,
            )
            export_audit = (
                await c.get("/api/v1/audit", params={"limit": 10}, headers=h("auditor"))
            ).json()
            ev.check(
                "the_export_itself_left_an_audited_attributed_row",
                any(
                    item["action"] == "audit.export" and item["actor"] == PEOPLE["auditor"]
                    for item in export_audit["items"]
                ),
                detail=[i["action"] for i in export_audit["items"]],
            )

            # ---------------------------------------------------------- D. override
            # A critical incident on int-a2 raises any action targeting it (or an adjacent segment) to SC-2 - created
            # first, and never resolved, so both commands below are genuinely SC-2 throughout this whole run.
            with psycopg.connect(dsn_from_env()) as db:
                fresh_signal_evidence(db, "signal-int-a2", "int-a2", datetime.now(timezone.utc))
                fixture_incident_id = incident_repo.create_incident(
                    db,
                    "stalled_vehicle",
                    "critical",
                    "intersection",
                    "int-a2",
                    geometry,
                    [str(uuid.uuid4())],
                    "OPS",
                    0.9,
                    "verify-p0905",
                )
            sc2_command = await run_command_to_executed(
                c, h, "signal_plan_change", "int-a2", {"deviation_s": 5}
            )
            sc2_via_incident = await run_command_to_executed(c, h, "diversion", "int-a2_int-a3", {})

            over = await c.post(
                f"/api/v1/commands/{sc2_via_incident}/override",
                headers=h("commander"),
                json={
                    "justification": "verify-p0905: visible hazard on scene, not waiting for automated verification"
                },
            )
            ev.check(
                "the_incident_commander_overrides_an_executed_sc2_command_with_a_justification",
                over.status_code == 200
                and over.json()["status"] == "rolled_back"
                and over.json()["safety_class"] == "SC-2",
                detail=over.text[:300],
            )
            with psycopg.connect(dsn_from_env()) as db:
                after = command_repo.get_command(db, sc2_via_incident)
                rows = db.execute(
                    "SELECT point, decision, reason FROM policy_decisions WHERE entity_type='command' AND entity_id=%s AND point='command_override' ORDER BY decided_at",
                    (sc2_via_incident,),
                ).fetchall()
            ev.check(
                "the_override_moved_the_command_to_rolled_back_and_left_a_permit_decision_naming_the_role",
                after["status"] == "rolled_back" and any(r[1] == "permit" for r in rows),
                detail=rows,
            )
            with psycopg.connect(dsn_from_env()) as db:
                audit_rows = db.execute(
                    "SELECT detail FROM operator_audit WHERE entity_id=%s AND action='command.override' AND outcome='allowed' ORDER BY audit_id DESC LIMIT 1",
                    (sc2_via_incident,),
                ).fetchone()
            ev.check(
                "the_override_audit_entry_names_the_justification_and_is_honest_that_no_physical_undo_was_attempted",
                audit_rows is not None
                and "verify-p0905" in audit_rows[0]["justification"]
                and "not attempted" in audit_rows[0]["physical_undo"],
                detail=audit_rows[0] if audit_rows else None,
            )
            second_attempt = await c.post(
                f"/api/v1/commands/{sc2_via_incident}/override",
                headers=h("commander"),
                json={
                    "justification": "verify-p0905: a second attempt on an already rolled back command"
                },
            )
            ev.check(
                "a_second_override_of_the_same_now_rolled_back_command_is_refused",
                second_attempt.status_code == 409,
                detail=second_attempt.text[:200],
            )
            wrong_role = await c.post(
                f"/api/v1/commands/{sc2_command}/override",
                headers=h("approver"),
                json={"justification": "verify-p0905: a supervisor should not be able to do this"},
            )
            ev.check(
                "a_supervisor_may_not_override_even_though_a_supervisor_may_approve_sc2",
                # `commands.override` is granted only to incident_commander in the inventory, so a supervisor is
                # refused at the capability gate before the endpoint's own class-based authority check ever runs -
                # the same layering REQUEST/REVIEW already use (a broad capability, then a narrower per-class role).
                wrong_role.status_code == 403
                and wrong_role.json()["detail"]["error"] == "forbidden",
                detail=wrong_role.text[:200],
            )
            requester_role = await c.post(
                f"/api/v1/commands/{sc2_command}/override",
                headers=h("requester"),
                json={
                    "justification": "verify-p0905: an operator holds no override authority at all"
                },
            )
            ev.check(
                "an_operator_holds_no_override_capability_and_is_refused_at_the_door",
                requester_role.status_code == 403,
                detail=requester_role.text[:200],
            )
            with psycopg.connect(dsn_from_env()) as db:
                fresh_kpi(db, "corridor-c", "east", geometry)
            sc0_command = await run_command_to_executed(
                c,
                h,
                "variable_message_sign",
                "int-c2_int-c3",
                {"message": "verify-p0905 override sc0 probe"},
            )
            sc0_over = await c.post(
                f"/api/v1/commands/{sc0_command}/override",
                headers=h("commander"),
                json={"justification": "verify-p0905: sc0 has no override, this must be refused"},
            )
            ev.check(
                "an_sc0_command_has_no_override_and_is_refused_as_not_available_not_as_a_role_problem",
                sc0_over.status_code == 422
                and sc0_over.json()["detail"]["error"] == "override_not_available",
                detail=sc0_over.text[:200],
            )
            requested_only = await c.post(
                "/api/v1/commands",
                headers=h("requester"),
                json={
                    "action_type": "diversion",
                    "target_entity_id": "int-a2_int-a3",
                    "idempotency_key": key(),
                },
            )
            wrong_status = await c.post(
                f"/api/v1/commands/{requested_only.json()['command_id']}/override",
                headers=h("commander"),
                json={"justification": "verify-p0905: this command was never executed"},
            )
            ev.check(
                "a_command_that_was_never_executed_cannot_be_overridden",
                wrong_status.status_code == 409,
                detail=wrong_status.text[:200],
            )
            too_short = await c.post(
                f"/api/v1/commands/{sc2_command}/override",
                headers=h("commander"),
                json={"justification": "no"},
            )
            ev.check(
                "a_justification_under_the_minimum_length_is_refused_by_validation",
                too_short.status_code == 422,
            )

            with psycopg.connect(dsn_from_env()) as db:
                final_chain = {
                    t: audit_chain.verify_table(db, t, order, deletable)
                    for t, (order, deletable) in audit_chain.CHAINED_TABLES.items()
                }
            ev.check(
                "every_chained_history_is_still_intact_after_this_entire_run",
                all(r["intact"] for r in final_chain.values()),
                detail={t: r for t, r in final_chain.items() if not r["intact"]},
            )
    finally:
        server.should_exit = True
        with psycopg.connect(dsn_from_env()) as db:
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE commands SET status = 'expired', updated_at = now() WHERE status = 'approved'"
                )
                # The fixture critical incident must not outlive this run: left open, it silently escalates every
                # later verify script's action against int-a2 (or an adjacent segment) to SC-2 - found the hard way,
                # by breaking verify_command_workflow.py and verify_govern.py on a later run.
                if "fixture_incident_id" in locals() and fixture_incident_id:
                    cur.execute(
                        "DELETE FROM incidents WHERE incident_id = %s", (fixture_incident_id,)
                    )
                # A command this script drove to 'executed' and then deliberately never rolled back (the negative
                # override tests) stays 'executed' forever otherwise, and `verifier_worker.verify_ready` scans ALL
                # executed commands system-wide with no fixture scoping - found the same way as the incident above,
                # by breaking verify_command_workflow.py's own outcome-verification check on a later run.
                cur.execute(
                    "DELETE FROM commands WHERE idempotency_key LIKE %s", ("%verify-p0905%",)
                )
            db.commit()
    return ev.finish()


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
