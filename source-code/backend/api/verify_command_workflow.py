"""P08.08 acceptance evidence (API side): request, approve, execute, verify - against the real Keycloak, Postgres, FastAPI app under
uvicorn, the real command executor (the real simulator adapter in the pinned SUMO container) and the real outcome verifier:

    python source-code/backend/api/verify_command_workflow.py

Every human action carries a real access token from the Authorization Code + PKCE flow, for a named demo person. What is proven:
a command is requested from a stored recommendation (or directly) by an entitled role; a second person approves, the requester and
an entitled-by-name-only role cannot; the execution-time policy runs again and its denials and outage are shown, never silently
turned into an approval; nothing a person can call executes a command - the executor service does, and records what the adapter
observed; the verifier, independent of everyone who touched it, records an outcome; and every step is audited to a person.
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

import httpx
import psycopg
import uvicorn
from psycopg.types.json import Jsonb

os.environ.pop("AIOPS_AUTH_MODE", None)

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.api import oidc_client  # noqa: E402
from backend.api.app import app  # noqa: E402
from backend.control import policy as policy_module  # noqa: E402
from backend.control import verifier_worker  # noqa: E402
from backend.control.executor_worker import run_once as execute_next  # noqa: E402
from backend.control.recommendation_service import recommend_for_incident  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.repositories import incidents as incident_repo  # noqa: E402
from database.migrate import dsn_from_env, migrate  # noqa: E402

HOST, PORT = "127.0.0.1", 8813
BASE = f"http://{HOST}:{PORT}"
GEOMETRY = "2026-09-18.1"
IDENTITIES = json.loads(
    (SOURCE_ROOT / "infra" / "platform" / "output" / "demo_identities.json").read_text(
        encoding="utf-8"
    )
)
ev = Evidence("P08.08")

PEOPLE = {
    "requester": "alex.chen",
    "second_operator": "alex.two",
    "approver": "sam.okafor",
    "second_approver": "sam.two",
    "dispatcher": "dana.rivera",
    "auditor": "ana.petrov",
    "field": "fin.hassan",
    "demo": "dee.moreno",
    "commander": "eve.laurent",
}


def run_server() -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(80):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("uvicorn did not start")


def fresh_kpi(conn: psycopg.Connection, corridor: str, direction: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO corridor_kpis (corridor_id, direction, window_start, window_seconds, geometry_version, kpis, quality, coverage, sample_count, segments_reporting, segments_expected) "
            "VALUES (%s, %s, %s, 300, %s, %s::jsonb, 'valid', 1.0, 10, 1, 1) ON CONFLICT DO NOTHING",
            (
                corridor,
                direction,
                datetime.now(timezone.utc) - timedelta(minutes=1),
                GEOMETRY,
                Jsonb(
                    {
                        "delay_s": 22.0,
                        "queue_fraction": 0.3,
                        "travel_time_s": 70.0,
                        "free_flow_travel_time_s": 50.0,
                    }
                ),
            ),
        )
    conn.commit()


def incident(conn: psycopg.Connection, kind: str, element: str) -> str:
    return incident_repo.create_incident(
        conn,
        kind,
        "high",
        "segment",
        element,
        GEOMETRY,
        [str(uuid.uuid4())],
        "OPS",
        0.8,
        "verify-fixture",
    )


def key() -> str:
    return f"verify-{uuid.uuid4().hex}"


async def main() -> int:  # noqa: PLR0915
    tokens = {
        name: oidc_client.login(user, IDENTITIES[user]["password"]).access_token
        for name, user in PEOPLE.items()
    }

    def h(who: str) -> dict:
        return {"Authorization": f"Bearer {tokens[who]}"}

    with psycopg.connect(dsn_from_env()) as db:
        migrate(db)
        now = datetime.now(timezone.utc)
        # `verifier_worker.verify_ready` scans EVERY executed-but-unverified command in the database, with no fixture
        # scoping - a command an earlier run (this script's own LAT-04 measurement loop included, or any other verify
        # script) left `executed` with no outcome is picked up ahead of the one this run is about to create and about
        # to check, and this run's own outcome-verification assertion fails on a command it never made. Sweep away
        # anything that predates this run before building today's fixtures.
        with db.cursor() as cur:
            cur.execute(
                "DELETE FROM commands c WHERE c.status = 'executed' AND c.acknowledged_at < %s "
                "AND NOT EXISTS (SELECT 1 FROM command_outcomes o WHERE o.command_id = c.command_id)",
                (now,),
            )
        db.commit()
        for corridor in ("corridor-a", "corridor-b"):
            fresh_kpi(db, corridor, "east")
        blocked = incident(db, "stalled_vehicle", "int-a2_int-a3")
        crowded = incident(db, "congestion", "int-b2_int-b3")
        diversion_rec = recommend_for_incident(
            db,
            blocked,
            "stalled_vehicle",
            "segment",
            "int-a2_int-a3",
            GEOMETRY,
            now,
            diversion_endpoints=("int-a1", "int-c4"),
        )
        signal_rec = recommend_for_incident(
            db,
            crowded,
            "congestion",
            "intersection",
            "int-b3",
            GEOMETRY,
            now,
            corridor_context=("corridor-b", "east"),
        )
        ev.check(
            "fixtures_recommendations_exist_from_the_platforms_own_generators",
            diversion_rec is not None and signal_rec is not None,
            detail=f"{diversion_rec} {signal_rec}",
        )
        server = run_server()
        try:
            async with httpx.AsyncClient(base_url=BASE, timeout=90) as c:

                async def alt(rec_id: str, wanted: str | None = None) -> str:
                    rec = next(
                        r
                        for r in (
                            await c.get(
                                "/api/v1/recommendations",
                                params={"limit": 500},
                                headers=h("requester"),
                            )
                        ).json()["items"]
                        if r["recommendation_id"] == rec_id
                    )
                    if wanted:
                        return next(
                            a["alternative_id"]
                            for a in rec["alternatives"]
                            if a["description"].lower().startswith(wanted)
                        )
                    return next(
                        a["alternative_id"]
                        for a in rec["alternatives"]
                        if not a["description"].lower().startswith("take no action")
                    )

                # ------------------------------------------------------------------ request from a recommendation
                first = await c.post(
                    f"/api/v1/recommendations/{diversion_rec}/request",
                    headers=h("requester"),
                    json={"alternative_id": await alt(diversion_rec), "idempotency_key": key()},
                )
                ev.check(
                    "an_operator_requests_a_command_from_a_recommendation",
                    first.status_code == 201
                    and first.json()["created"]
                    and first.json()["status"] == "requested"
                    and first.json()["safety_class"] == "SC-1",
                    detail=first.text[:200],
                )
                command_id = first.json()["command_id"]
                shared_key = key()
                one = await c.post(
                    f"/api/v1/recommendations/{signal_rec}/request",
                    headers=h("requester"),
                    json={"alternative_id": await alt(signal_rec), "idempotency_key": shared_key},
                )
                two = await c.post(
                    f"/api/v1/recommendations/{signal_rec}/request",
                    headers=h("requester"),
                    json={"alternative_id": await alt(signal_rec), "idempotency_key": shared_key},
                )
                ev.check(
                    "a_repeated_request_with_the_same_key_returns_the_same_command_and_creates_no_second_one",
                    one.status_code == 201
                    and two.status_code == 201
                    and one.json()["command_id"] == two.json()["command_id"]
                    and one.json()["created"]
                    and not two.json()["created"],
                    detail=f"{one.text[:100]} / {two.text[:100]}",
                )
                signal_command = one.json()["command_id"]
                detail = (
                    await c.get(f"/api/v1/commands/{command_id}", headers=h("auditor"))
                ).json()
                ev.check(
                    "the_request_records_who_and_in_which_role_and_the_recommendation_it_came_from",
                    detail["command"]["requested_by"] == PEOPLE["requester"]
                    and detail["requested_by_role"] == "operator"
                    and detail["recommendation"]["recommendation_id"] == diversion_rec
                    and detail["safety_class"] == "SC-1",
                )
                sig = (
                    await c.get(f"/api/v1/commands/{signal_command}", headers=h("auditor"))
                ).json()
                ev.check(
                    "a_signal_command_carries_the_seconds_it_moves_and_the_bound_it_was_checked_against",
                    sig["params"]["deviation_s"] > 0
                    and sig["params"]["max_deviation_s"] == 20.0
                    and sig["command"]["target"]
                    == {"adapter": "signal_controller_adapter", "entity_id": "int-b3"},
                    detail=str(sig["params"]),
                )
                reco = next(
                    r
                    for r in (
                        await c.get(
                            "/api/v1/recommendations", params={"limit": 500}, headers=h("auditor")
                        )
                    ).json()["items"]
                    if r["recommendation_id"] == diversion_rec
                )
                ev.check(
                    "the_recommendation_moves_to_requested_it_is_never_shown_as_executed",
                    reco["status"] == "requested",
                )
                refused = {}
                for who in ("dispatcher", "second_approver"):
                    refused[who] = (
                        await c.post(
                            f"/api/v1/recommendations/{diversion_rec}/request",
                            headers=h(who),
                            json={
                                "alternative_id": await alt(diversion_rec),
                                "idempotency_key": key(),
                            },
                        )
                    ).status_code
                ev.check(
                    "roles_that_may_not_request_this_safety_class_are_refused",
                    set(refused.values()) == {403},
                    detail=str(refused),
                )
                gate = {
                    who: (
                        await c.post(
                            f"/api/v1/recommendations/{diversion_rec}/request",
                            headers=h(who),
                            json={"alternative_id": "x", "idempotency_key": key()},
                        )
                    ).status_code
                    for who in ("auditor", "field", "demo")
                }
                ev.check(
                    "roles_without_the_request_capability_are_refused_at_the_door",
                    set(gate.values()) == {403},
                    detail=str(gate),
                )
                bad = [
                    await c.post(
                        f"/api/v1/recommendations/{diversion_rec}/request",
                        headers=h("requester"),
                        json={"alternative_id": "nope", "idempotency_key": key()},
                    ),
                    await c.post(
                        f"/api/v1/recommendations/{diversion_rec}/request",
                        headers=h("requester"),
                        json={
                            "alternative_id": await alt(diversion_rec),
                            "idempotency_key": "short",
                        },
                    ),
                    await c.post(
                        f"/api/v1/recommendations/{diversion_rec}/request",
                        headers=h("requester"),
                        json={
                            "alternative_id": await alt(diversion_rec),
                            "idempotency_key": key(),
                            "requested_by": "someone.else",
                        },
                    ),
                    await c.post(
                        f"/api/v1/recommendations/{uuid.uuid4()}/request",
                        headers=h("requester"),
                        json={"alternative_id": "x", "idempotency_key": key()},
                    ),
                    await c.post(
                        "/api/v1/recommendations/not-a-uuid/request",
                        headers=h("requester"),
                        json={"alternative_id": "x", "idempotency_key": key()},
                    ),
                ]
                ev.check(
                    "unknown_alternative_short_key_forged_requester_and_unknown_recommendation_are_refused",
                    [b.status_code for b in bad] == [422, 422, 422, 404, 404],
                    detail=str([b.status_code for b in bad]),
                )
                no_action = await alt(signal_rec, "take no action")
                nothing = await c.post(
                    f"/api/v1/recommendations/{signal_rec}/request",
                    headers=h("requester"),
                    json={"alternative_id": no_action, "idempotency_key": key()},
                )
                ev.check(
                    "taking_no_action_is_not_a_command",
                    nothing.status_code == 422
                    and nothing.json()["detail"]["error"] == "nothing_to_request",
                )

                # ------------------------------------------------------------------ approval and four-eyes
                same_role = await c.post(
                    f"/api/v1/commands/{command_id}/review",
                    headers=h("second_operator"),
                    json={"decision": "approve"},
                )
                ev.check(
                    "an_operator_may_not_approve_a_supervisor_class_command",
                    same_role.status_code == 403
                    and same_role.json()["detail"]["error"] == "role_cannot_review",
                    detail=same_role.text[:160],
                )
                still = (
                    await c.get(f"/api/v1/commands/{command_id}", headers=h("auditor"))
                ).json()["command"]
                ev.check(
                    "neither_refused_attempt_touched_the_command_it_is_still_waiting",
                    still["status"] == "requested" and still["policy_decision"] == "pending",
                )
                no_reason = await c.post(
                    f"/api/v1/commands/{signal_command}/review",
                    headers=h("approver"),
                    json={"decision": "deny"},
                )
                denied = await c.post(
                    f"/api/v1/commands/{signal_command}/review",
                    headers=h("approver"),
                    json={"decision": "deny", "reason": "peak hour, do not touch this junction"},
                )
                after_deny = (
                    await c.get(f"/api/v1/commands/{signal_command}", headers=h("auditor"))
                ).json()
                ev.check(
                    "a_denial_needs_a_reason_and_records_it_with_who_denied",
                    no_reason.status_code == 422
                    and denied.status_code == 200
                    and denied.json()["status"] == "denied"
                    and "peak hour" in after_deny["command"]["error"]["message"]
                    and after_deny["transitions"][-1]["changed_by"] == PEOPLE["approver"],
                    detail=denied.text[:200],
                )
                approve = await c.post(
                    f"/api/v1/commands/{command_id}/review",
                    headers=h("approver"),
                    json={"decision": "approve"},
                )
                after = (await c.get(f"/api/v1/commands/{command_id}", headers=h("auditor"))).json()
                ev.check(
                    "a_supervisor_approves_after_the_policy_runs_again_against_what_is_true_now",
                    approve.status_code == 200
                    and approve.json()["status"] == "approved"
                    and approve.json()["policy_decision"] == "approved"
                    and after["command"]["approved_by"] == PEOPLE["approver"]
                    and after["approved_by_role"] == "supervisor",
                    detail=approve.text[:200],
                )
                again = await c.post(
                    f"/api/v1/commands/{command_id}/review",
                    headers=h("second_approver"),
                    json={"decision": "approve"},
                )
                ev.check(
                    "an_already_decided_command_cannot_be_decided_again",
                    again.status_code == 409
                    and again.json()["detail"]["current_status"] == "approved",
                )

                # two supervisors deciding the same command at once
                race = (
                    await c.post(
                        "/api/v1/commands",
                        headers=h("requester"),
                        json={
                            "action_type": "variable_message_sign",
                            "target_entity_id": "int-a2_int-a3",
                            "message": "Incident ahead, use int-b corridor",
                            "idempotency_key": key(),
                        },
                    )
                ).json()["command_id"]
                results = await asyncio.gather(
                    c.post(
                        f"/api/v1/commands/{race}/review",
                        headers=h("approver"),
                        json={"decision": "approve"},
                    ),
                    c.post(
                        f"/api/v1/commands/{race}/review",
                        headers=h("second_approver"),
                        json={"decision": "approve"},
                    ),
                )
                codes = sorted(r.status_code for r in results)
                ev.check(
                    "two_approvers_deciding_at_once_one_wins_and_the_other_is_told_it_is_already_decided",
                    codes == [200, 409],
                    detail=str(codes),
                )

                # ------------------------------------------------------------------ direct commands, policy denials, policy outage
                cmd_key = key()
                sign = await c.post(
                    "/api/v1/commands",
                    headers=h("requester"),
                    json={
                        "action_type": "variable_message_sign",
                        "target_entity_id": "int-a2_int-a3",
                        "message": "Lane closed ahead",
                        "idempotency_key": cmd_key,
                    },
                )
                ev.check(
                    "an_operator_requests_a_sign_message_directly_and_it_is_sc0",
                    sign.status_code == 201 and sign.json()["safety_class"] == "SC-0",
                    detail=sign.text[:160],
                )
                own = await c.post(
                    f"/api/v1/commands/{sign.json()['command_id']}/review",
                    headers=h("requester"),
                    json={"decision": "approve"},
                )
                ev.check(
                    "the_requester_cannot_approve_their_own_command_even_when_their_role_could_approve_that_class",
                    own.status_code == 403 and own.json()["detail"]["error"] == "four_eyes",
                    detail=own.text[:160],
                )
                own_state = (
                    await c.get(
                        f"/api/v1/commands/{sign.json()['command_id']}", headers=h("auditor")
                    )
                ).json()["command"]
                ev.check(
                    "the_refused_self_approval_did_not_deny_or_change_the_command",
                    own_state["status"] == "requested"
                    and own_state["policy_decision"] == "pending",
                )
                sc0_approve = await c.post(
                    f"/api/v1/commands/{sign.json()['command_id']}/review",
                    headers=h("second_operator"),
                    json={"decision": "approve"},
                )
                ev.check(
                    "a_different_operator_may_approve_an_sc0_command",
                    sc0_approve.status_code == 200 and sc0_approve.json()["status"] == "approved",
                    detail=sc0_approve.text[:200],
                )
                invalid = [
                    await c.post(
                        "/api/v1/commands",
                        headers=h("requester"),
                        json={
                            "action_type": "variable_message_sign",
                            "target_entity_id": "int-a2_int-a3",
                            "idempotency_key": key(),
                        },
                    ),
                    await c.post(
                        "/api/v1/commands",
                        headers=h("requester"),
                        json={
                            "action_type": "variable_message_sign",
                            "target_entity_id": "int-a2_int-a3",
                            "message": "x" * 121,
                            "idempotency_key": key(),
                        },
                    ),
                    await c.post(
                        "/api/v1/commands",
                        headers=h("requester"),
                        json={
                            "action_type": "signal_plan_change",
                            "target_entity_id": "int-b2",
                            "idempotency_key": key(),
                        },
                    ),
                    await c.post(
                        "/api/v1/commands",
                        headers=h("requester"),
                        json={
                            "action_type": "emergency_preemption",
                            "target_entity_id": "corridor-a",
                            "idempotency_key": key(),
                        },
                    ),
                ]
                ev.check(
                    "commands_with_missing_or_out_of_range_parameters_or_an_unsupported_action_are_refused",
                    [r.status_code for r in invalid] == [422, 422, 422, 422],
                    detail=str([r.status_code for r in invalid]),
                )
                unreal = (
                    await c.post(
                        "/api/v1/commands",
                        headers=h("requester"),
                        json={
                            "action_type": "variable_message_sign",
                            "target_entity_id": "no-such-segment",
                            "message": "hello",
                            "idempotency_key": key(),
                        },
                    )
                ).json()["command_id"]
                unreal_review = await c.post(
                    f"/api/v1/commands/{unreal}/review",
                    headers=h("approver"),
                    json={"decision": "approve"},
                )
                unreal_detail = (
                    await c.get(f"/api/v1/commands/{unreal}", headers=h("auditor"))
                ).json()["command"]
                ev.check(
                    "a_target_that_does_not_exist_is_denied_by_the_policy_with_its_reason",
                    unreal_review.json()["status"] == "denied"
                    and unreal_detail["error"]["error_code"] == "invalid_target",
                    detail=str(unreal_detail.get("error")),
                )
                stale = (
                    await c.post(
                        "/api/v1/commands",
                        headers=h("requester"),
                        json={
                            "action_type": "diversion",
                            "target_entity_id": "int-c2_int-c3",
                            "idempotency_key": key(),
                        },
                    )
                ).json()["command_id"]
                with db.cursor() as cur:
                    cur.execute("DELETE FROM corridor_kpis WHERE corridor_id = 'corridor-c'")
                db.commit()
                stale_review = await c.post(
                    f"/api/v1/commands/{stale}/review",
                    headers=h("approver"),
                    json={"decision": "approve"},
                )
                stale_detail = (
                    await c.get(f"/api/v1/commands/{stale}", headers=h("auditor"))
                ).json()["command"]
                ev.check(
                    "without_fresh_evidence_the_approval_is_denied_stale_evidence_and_marked_retryable",
                    stale_review.json()["status"] == "denied"
                    and stale_detail["error"]["error_code"] == "stale_evidence"
                    and stale_detail["error"]["retryable"] is True,
                    detail=str(stale_detail.get("error")),
                )

                outage = (
                    await c.post(
                        "/api/v1/commands",
                        headers=h("requester"),
                        json={
                            "action_type": "variable_message_sign",
                            "target_entity_id": "int-b2_int-b3",
                            "message": "Slow traffic ahead",
                            "idempotency_key": key(),
                        },
                    )
                ).json()["command_id"]
                real_build = policy_module.build_context
                policy_module.build_context = lambda *a, **k: (_ for _ in ()).throw(
                    RuntimeError("injected policy outage")
                )  # the fault a real outage would be
                try:
                    unavailable = await c.post(
                        f"/api/v1/commands/{outage}/review",
                        headers=h("approver"),
                        json={"decision": "approve"},
                    )
                finally:
                    policy_module.build_context = real_build
                out_detail = (
                    await c.get(f"/api/v1/commands/{outage}", headers=h("auditor"))
                ).json()["command"]
                ev.check(
                    "a_policy_outage_leaves_the_command_waiting_and_says_so_it_is_never_approved_by_default",
                    unavailable.status_code == 200
                    and unavailable.json()["status"] == "requested"
                    and unavailable.json()["policy_decision"] == "policy_unavailable"
                    and out_detail["status"] == "requested"
                    and out_detail["policy_decision"] == "policy_unavailable",
                    detail=unavailable.text[:220],
                )

                # ------------------------------------------------------------------ nobody can execute through the API
                attempts = [
                    (
                        await c.post(
                            f"/api/v1/commands/{command_id}/{verb}", headers=h(who), json={}
                        )
                    ).status_code
                    for who in ("approver", "commander", "requester")
                    for verb in ("execute", "run", "dispatch")
                ]
                ev.check(
                    "there_is_no_way_for_a_person_to_execute_a_command_through_the_api",
                    set(attempts) <= {404, 405},
                    detail=str(sorted(set(attempts))),
                )

                # ------------------------------------------------------------------ the executor and the verifier
                with db.cursor() as cur:
                    cur.execute(
                        "UPDATE commands SET status = 'expired', updated_at = now() WHERE status = 'approved' AND command_id <> %s AND command_id <> %s",
                        (command_id, sign.json()["command_id"]),
                    )
                db.commit()
                fresh_kpi(db, "corridor-a", "east")
                started = time.monotonic()
                first_done = execute_next(db)
                second_done = execute_next(db)
                third_done = execute_next(db)
                after_exec = {
                    cid: (await c.get(f"/api/v1/commands/{cid}", headers=h("auditor"))).json()
                    for cid in (command_id, sign.json()["command_id"])
                }
                statuses = {cid: d["command"]["status"] for cid, d in after_exec.items()}
                ev.check(
                    "the_executor_service_picks_up_approved_commands_and_records_what_the_adapter_observed",
                    {first_done, second_done} == {command_id, sign.json()["command_id"]}
                    and third_done is None
                    and set(statuses.values()) <= {"executed", "failed"}
                    and "executed" in statuses.values(),
                    detail=f"{statuses} in {time.monotonic() - started:.0f}s",
                )
                executed_detail = next(
                    d for d in after_exec.values() if d["command"]["status"] == "executed"
                )
                who_executed = [
                    t["changed_by"]
                    for t in executed_detail["transitions"]
                    if t["to_status"] in ("executing", "executed")
                ]
                ev.check(
                    "only_the_command_executor_identity_appears_as_the_executor_never_a_person",
                    who_executed and set(who_executed) == {"system:command-executor"},
                    detail=str(who_executed),
                )
                ev.check(
                    "the_executed_command_carries_the_adapters_acknowledgement_time",
                    executed_detail["command"].get("acknowledged_at") is not None,
                )

                time.sleep(2.2)
                verified = verifier_worker.verify_ready(
                    db, timedelta(seconds=1), datetime.now(timezone.utc)
                )
                outcomes = (
                    await c.get(
                        "/api/v1/outcomes",
                        params={"command_id": executed_detail["command"]["command_id"]},
                        headers=h("auditor"),
                    )
                ).json()["items"]
                ev.check(
                    "the_verifier_records_an_outcome_for_the_executed_command_independent_of_requester_approver_and_executor",
                    len(outcomes) == 1
                    and outcomes[0]["verifier"] == "system:outcome-verifier"
                    and outcomes[0]["classification"]
                    in ("effective", "ineffective", "unsafe", "unknown")
                    and bool(verified),
                    detail=str(outcomes[0]["classification"] if outcomes else outcomes),
                )
                with_outcome = (
                    await c.get(
                        f"/api/v1/commands/{executed_detail['command']['command_id']}",
                        headers=h("auditor"),
                    )
                ).json()
                ev.check(
                    "the_command_detail_links_its_verified_outcome",
                    with_outcome["outcome"] is not None
                    and with_outcome["outcome"]["command_id"]
                    == executed_detail["command"]["command_id"],
                )
                ev.check(
                    "an_outcome_with_no_measurement_is_unknown_and_escalated_not_guessed",
                    outcomes[0]["classification"] != "unknown"
                    or bool(outcomes[0].get("escalation_reason")),
                    detail=str(outcomes[0].get("escalation_reason")),
                )

                # ------------------------------------------------------------------ audit
                with db.cursor() as cur:
                    cur.execute(
                        "SELECT actor, actor_roles, action, outcome FROM operator_audit WHERE entity_id = %s ORDER BY audit_id",
                        (command_id,),
                    )
                    rows = cur.fetchall()
                actions = [(r[0], r[2], r[3]) for r in rows]
                ev.check(
                    "the_command_history_is_audited_request_refusals_and_approval_each_to_a_person",
                    (PEOPLE["requester"], "command.request", "allowed") in actions
                    and (PEOPLE["second_operator"], "command.approve", "denied") in actions
                    and (PEOPLE["approver"], "command.approve", "allowed") in actions,
                    detail=str(actions),
                )
        finally:
            server.should_exit = True
            await asyncio.sleep(0.5)
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
