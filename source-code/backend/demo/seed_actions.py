"""Give the demo world a command history that covers every state a person will meet.

    python source-code/backend/demo/seed_actions.py            # the API on 127.0.0.1:8100 and the demo feeder must be running

What is real and what is recorded is written into the data, not left to memory:

* REAL, made now through the running API as named demo people with real Keycloak tokens: a command left waiting for approval, one
  denied with a reason, one whose approval was refused by an injected policy outage, and two that the running command executor
  really executes in the SUMO container (a sign message and a signal extension). The verifier worker later records their outcomes.
* REAL failure: a command whose target the adapter cannot find, so the executor really fails it.
* REAL expiry: a command nobody approved in time.
* RECORDED: four completed histories, one per outcome class - effective, ineffective, unsafe (rolled back, with the undo evidence)
  and unknown (escalated) - built with the measured numbers of the lock-step simulator runs of P07.09
  (`docs/evidence/p07_09_outcomes.json`). Their state history says so in words: they were not executed in this session, the numbers
  are those runs' own, and the outcome is classified by the same `verify_and_rollback` the verifier uses.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.api import oidc_client  # noqa: E402
from backend.control.command_service import request_command  # noqa: E402
from backend.control.outcome_verification import Metric, verify_and_rollback  # noqa: E402
from backend.demo import world  # noqa: E402
from backend.repositories import commands as command_repo  # noqa: E402
from backend.roles import EXECUTOR, OUTCOME_VERIFIER  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

API = "http://127.0.0.1:8100"
IDENTITIES = json.loads(
    (SOURCE_ROOT / "infra" / "platform" / "output" / "demo_identities.json").read_text(
        encoding="utf-8"
    )
)
EVIDENCE = json.loads(
    (SOURCE_ROOT.parent / "docs" / "evidence" / "p07_09_outcomes.json").read_text(encoding="utf-8")
)["metrics"]
RECORDED_NOTE = "recorded history from P07.09's lock-step simulator run (docs/evidence/p07_09_outcomes.json); not executed in this session"


def login(user: str) -> dict:
    return {
        "Authorization": f"Bearer {oidc_client.login(user, IDENTITIES[user]['password']).access_token}"
    }


def wait_for_recommendations(
    client: httpx.Client, headers: dict, timeout_s: float = 240
) -> list[dict]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        items = client.get(
            "/api/v1/recommendations", params={"status": "proposed", "limit": 100}, headers=headers
        ).json()["items"]
        if items:
            return items
        time.sleep(5)
    raise SystemExit(
        "no recommendation appeared: is the demo feeder running and have incidents formed?"
    )


def real_actions(client: httpx.Client, conn: psycopg.Connection) -> dict:
    alex, alex2, sam = login("alex.chen"), login("alex.two"), login("sam.okafor")
    recommendations = wait_for_recommendations(client, alex)
    diversion = next((r for r in recommendations if r["action_type"] == "diversion"), None)
    signal_rec = next(
        (r for r in recommendations if r["action_type"] == "signal_plan_change"), None
    )

    def alternative(rec: dict) -> str:
        return next(
            a["alternative_id"]
            for a in rec["alternatives"]
            if not a["description"].lower().startswith("take no action")
        )

    made: dict[str, str] = {}
    if diversion:
        r = client.post(
            f"/api/v1/recommendations/{diversion['recommendation_id']}/request",
            headers=alex,
            json={
                "alternative_id": alternative(diversion),
                "idempotency_key": f"seed-pending-{uuid.uuid4().hex}",
            },
        )
        made["waiting"] = r.json()["command_id"]
    if signal_rec:
        r = client.post(
            f"/api/v1/recommendations/{signal_rec['recommendation_id']}/request",
            headers=alex,
            json={
                "alternative_id": alternative(signal_rec),
                "idempotency_key": f"seed-signal-{uuid.uuid4().hex}",
            },
        )
    else:
        r = client.post(
            "/api/v1/commands",
            headers=alex,
            json={
                "action_type": "signal_plan_change",
                "target_entity_id": "int-b2",
                "deviation_s": 8,
                "idempotency_key": f"seed-signal-{uuid.uuid4().hex}",
            },
        )
    cid = r.json()["command_id"]
    client.post(f"/api/v1/commands/{cid}/review", headers=sam, json={"decision": "approve"})
    made["signal_to_execute"] = cid

    def direct(text: str, target: str, who: dict) -> str:
        r = client.post(
            "/api/v1/commands",
            headers=who,
            json={
                "action_type": "variable_message_sign",
                "target_entity_id": target,
                "message": text,
                "idempotency_key": f"seed-{uuid.uuid4().hex}",
            },
        )
        return r.json()["command_id"]

    denied = direct("Congestion ahead: use int-c corridor", "int-b2_int-b3", alex)
    client.post(
        f"/api/v1/commands/{denied}/review",
        headers=sam,
        json={
            "decision": "deny",
            "reason": "Peak hour: the sign would send traffic onto corridor c, which is already busy",
        },
    )
    made["denied"] = denied
    outage = direct("Slow traffic ahead", "int-a3_int-a4", alex)
    command_repo.apply_policy_unavailable(
        conn,
        outage,
        "internal",
        "policy evaluation failed: seeded outage (fault injection), so the command waits rather than being approved",
    )
    made["policy_unavailable"] = outage
    sign = direct("Incident ahead, expect delays", "int-a1_int-a2", alex)
    client.post(f"/api/v1/commands/{sign}/review", headers=alex2, json={"decision": "approve"})
    made["sign_to_execute"] = sign
    return made


def recorded_histories(conn: psycopg.Connection) -> dict:
    """One completed history per outcome class, using P07.09's measured numbers."""
    now = datetime.now(timezone.utc)
    made: dict[str, str] = {}
    unsafe = EVIDENCE["unsafe_scenario"]
    ineffective = EVIDENCE["ineffective_scenario"]
    cases = [
        (
            "effective",
            "transit_priority",
            "transit_priority_adapter",
            "corridor-a",
            Metric("mean_travel_time", 109.0, "s"),
            Metric("mean_travel_time", 79.0, "s"),
            True,
            None,
            {
                "basis": "paired lock-step run: transit priority against the same-seed run without it",
                "baseline_s": 109.0,
                "with_priority_s": 79.0,
            },
        ),
        (
            "ineffective",
            "signal_plan_change",
            "signal_controller_adapter",
            "int-a2",
            Metric("waiting", ineffective["pre_waiting"], "vehicle_s"),
            Metric("waiting", ineffective["post_waiting"], "vehicle_s"),
            True,
            None,
            {
                "basis": "real signal extension against the same-seed no-action control",
                "control_no_action": ineffective["control_no_action"],
                "note": ineffective["note"],
            },
        ),
        (
            "unsafe",
            "diversion",
            "diversion_adapter",
            "int-b2_int-b3",
            Metric("waiting", unsafe["pre_waiting"], "vehicle_s"),
            Metric("waiting", unsafe["post_waiting"], "vehicle_s"),
            True,
            lambda command: {
                "restored": True,
                "mode": "reopened_lanes",
                "recovery_waiting_after_rollback": unsafe["recovery_waiting_after_rollback"],
                "vehicles_in_network": unsafe["vehicles_in_network"],
                "evidence": "the closed lanes were reopened and read back from TraCI; waiting recovered",
            },
            {
                "basis": "a real closure of int-b2_int-b3 in the live simulator, then its physical undo"
            },
        ),
        (
            "unknown",
            "signal_plan_change",
            "signal_controller_adapter",
            "int-c2",
            Metric("waiting", 20.0, "vehicle_s"),
            Metric("waiting", None, "vehicle_s"),
            True,
            None,
            {
                "basis": "a gap in the after-action telemetry, recorded as a zero sample count, never defaulted to effective"
            },
        ),
    ]
    for index, (
        label,
        action,
        adapter,
        target,
        pre,
        post,
        higher_is_worse,
        undo,
        detail,
    ) in enumerate(cases):
        at = now - timedelta(minutes=70 - index * 12)
        params = (
            {"deviation_s": 10.0, "max_deviation_s": 20.0}
            if action == "signal_plan_change"
            else None
        )
        command_id, _ = request_command(
            conn,
            f"seed-recorded-{label}-{uuid.uuid4().hex[:8]}",
            action,
            adapter,
            target,
            "alex.chen",
            at,
            "operator",
            params=params,
        )
        command_repo.transition_command(
            conn,
            command_id,
            "approved",
            "sam.okafor",
            "policy approved",
            at=at + timedelta(minutes=1),
            approved_by="sam.okafor",
            policy_decision="approved",
            approved_by_role="supervisor",
        )
        command_repo.transition_command(
            conn, command_id, "executing", EXECUTOR, RECORDED_NOTE, at=at + timedelta(minutes=2)
        )
        command_repo.transition_command(
            conn,
            command_id,
            "executed",
            EXECUTOR,
            RECORDED_NOTE,
            at=at + timedelta(minutes=2, seconds=5),
            acknowledged=True,
        )
        pre_window = (at + timedelta(minutes=-8), at + timedelta(minutes=2))
        post_window = (at + timedelta(minutes=2), at + timedelta(minutes=12))
        verify_and_rollback(
            conn,
            command_id,
            pre,
            post,
            pre_window,
            post_window,
            OUTCOME_VERIFIER,
            at + timedelta(minutes=13),
            safety_regression_threshold=15.0 if adapter != "transit_priority_adapter" else 30.0,
            effectiveness_improvement_threshold=15.0
            if adapter != "transit_priority_adapter"
            else 10.0,
            higher_is_worse=higher_is_worse,
            undo=undo,
            extra_detail={
                **detail,
                "recorded_from": "docs/evidence/p07_09_outcomes.json",
                "not_executed_in_this_session": True,
            },
        )
        made[label] = command_id
    return made


def failed_and_expired(conn: psycopg.Connection) -> dict:
    now = datetime.now(timezone.utc)
    ghost, _ = request_command(
        conn,
        f"seed-ghost-{uuid.uuid4().hex[:8]}",
        "variable_message_sign",
        "vms_adapter",
        "int-z9_int-z9",
        "alex.chen",
        now,
        "operator",
        params={"message": "This target does not exist in the simulator"},
    )
    command_repo.transition_command(
        conn,
        ghost,
        "approved",
        "alex.two",
        "approved for the seed: the target exists nowhere, so the adapter must fail it",
        at=now,
        approved_by="alex.two",
        policy_decision="approved",
        approved_by_role="operator",
    )
    lapsed, _ = request_command(
        conn,
        f"seed-lapsed-{uuid.uuid4().hex[:8]}",
        "variable_message_sign",
        "vms_adapter",
        "int-a1_int-a2",
        "alex.chen",
        now - timedelta(minutes=30),
        "operator",
        ttl_s=60.0,
        params={"message": "Nobody approved this in time"},
    )
    command_repo.expire_stale(conn, now)
    return {"failed_by_executor": ghost, "expired": lapsed}


def wait_for_executor(
    conn: psycopg.Connection, command_ids: list[str], timeout_s: float = 240
) -> dict:
    deadline = time.monotonic() + timeout_s
    states: dict[str, str] = {}
    while time.monotonic() < deadline:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT command_id, status FROM commands WHERE command_id = ANY(%s::uuid[])",
                (command_ids,),
            )
            states = {str(a): b for a, b in cur.fetchall()}
        conn.commit()
        if all(s in ("executed", "failed") for s in states.values()):
            return states
        time.sleep(3)
    return states


def main() -> int:
    world.load_platform_env()
    world.use_database(world.DEFAULT_DATABASE)
    with psycopg.connect(dsn_from_env()) as conn, httpx.Client(base_url=API, timeout=60) as client:
        made = {}
        made.update(recorded_histories(conn))
        made.update(failed_and_expired(conn))
        made.update(real_actions(client, conn))
        watch = [
            made[k]
            for k in ("failed_by_executor", "signal_to_execute", "sign_to_execute")
            if k in made
        ]
        print("waiting for the command executor to run:", watch, flush=True)
        print(json.dumps({"commands": made, "executor": wait_for_executor(conn, watch)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
