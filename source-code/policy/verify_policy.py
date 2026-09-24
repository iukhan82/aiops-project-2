"""P09.03 acceptance evidence: OPA authorization at the API and at the worker, against the real engine, Keycloak, PostgreSQL, the real
FastAPI apps under uvicorn and the real command executor's gate.

    python source-code/policy/verify_policy.py

What is proven, in order:

A. The Rego is checked and tested by OPA itself (`opa check --strict`, `opa test`), runs from a pinned image, and the engine is serving
   the policy version the generator wrote - the data cannot have drifted from `inventory.json` and `backend/roles.py`.
B. Differential test: the Python reference (`authz.capabilities_of`/`policy_for`, `policy.evaluate`, `roles.REQUEST_ROLES`...) and the
   engine agree on every (endpoint x role-set) of the access inventory, every (kind x action x role-set) of command authority, and
   thousands of generated command contexts - decision, error code and message.
C. Default denial: an endpoint the engine does not list is denied even when the enforcement point's own table would have let it
   through; an empty or garbage input is denied, never undefined.
D. Cross-role and target-escape negatives through the real API with real tokens, and the decision record they leave.
E. The executor's own gate refuses what an approval alone would have let through: an approver who is the requester, a role that could
   not approve, evidence gone stale, a target that no longer exists, no recorded approval, an unregistered adapter, a caller that is
   not the executor.
F. Policy outage: with the engine stopped every protected API request is 503 (public health stays up), the scenario-control API is 503,
   an approval leaves the command requested and says the policy was unavailable, the executor holds a command instead of executing it,
   and everything recovers when the engine returns. An engine that answers nonsense is treated the same way.
G. Hardening: security headers on every response, no cross-origin header, oversize bodies refused, per-person and per-address rate limits
   that throttle a flood without touching anyone else.
"""

from __future__ import annotations

import asyncio
import http.server
import importlib.util
import itertools
import json
import os
import random
import re
import statistics
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg
import uvicorn
from fastapi import Depends, FastAPI
from psycopg.types.json import Jsonb

os.environ.pop("AIOPS_AUTH_MODE", None)

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from backend import pdp  # noqa: E402
from backend.api import auth, hardening, oidc_client  # noqa: E402
from backend.api.app import app  # noqa: E402
from backend.api.auth import KNOWN_ROLES, enforce  # noqa: E402
from backend.api.authz import EndpointPolicy, capabilities_of, inventory, policy_for  # noqa: E402
from backend.control import executor_worker  # noqa: E402
from backend.control import policy as policy_module  # noqa: E402
from backend.control.command_service import request_command  # noqa: E402
from backend.control.policy import (  # noqa: E402
    KNOWN_ADAPTERS,
    PolicyContext,
    build_context,
    context_to_input,
    current_geometry,
    evaluate,
)
from backend.evidence import Evidence  # noqa: E402
from backend.repositories import commands as command_repo  # noqa: E402
from backend.roles import (  # noqa: E402
    APPROVE_ROLES,
    EXECUTOR,
    HUMAN_ROLES,
    REQUEST_ROLES,
    SERVICE_IDENTITIES,
    safety_class,
)
from backend.scenario_control.app import app as scenario_app  # noqa: E402
from database.migrate import dsn_from_env, migrate  # noqa: E402

POLICY_DIR = Path(__file__).resolve().parent
COMPOSE = SOURCE_ROOT / "infra" / "platform" / "docker-compose.yml"
HOST, API_PORT, DEMO_PORT, FAKE_PORT = "127.0.0.1", 8830, 8831, 8832
BASE, DEMO_BASE = f"http://{HOST}:{API_PORT}", f"http://{HOST}:{DEMO_PORT}"
IDENTITIES = json.loads(
    (SOURCE_ROOT / "infra" / "platform" / "output" / "demo_identities.json").read_text(
        encoding="utf-8"
    )
)
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
ALL_ROLES = sorted(set(HUMAN_ROLES) | set(SERVICE_IDENTITIES))
ev = Evidence("P09.03", "p09_03_policy", docs_name="p09_03_policy")


# ------------------------------------------------------------------------------------------------ WSL / docker helpers
def wsl_path(path: Path) -> str:
    text = str(path).replace("\\", "/")
    return re.sub(r"^([A-Za-z]):", lambda m: f"/mnt/{m.group(1).lower()}", text)


def wsl(command: str, timeout: float = 240) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["wsl.exe", "-e", "bash", "-lc", command], capture_output=True, text=True, timeout=timeout
    )


def compose_image() -> str:
    match = re.search(
        r"image:\s*(openpolicyagent/opa:\S+@sha256:[0-9a-f]{64})", COMPOSE.read_text()
    )
    assert match, "the compose file does not pin the policy engine image by digest"
    return match.group(1)


def opa_cli(*args: str) -> subprocess.CompletedProcess:
    image = compose_image().split(":", 1)[1].split("@", 1)[1]
    return wsl(
        f"docker run --rm -v '{wsl_path(POLICY_DIR)}:/policy:ro' openpolicyagent/opa@{image} "
        + " ".join(args)
    )


def engine_running() -> bool:
    return pdp.loaded_version() is not None


def stop_engine() -> None:
    wsl("docker stop aiops-opa", timeout=60)
    for _ in range(60):
        if not engine_running():
            return
        time.sleep(0.5)
    raise RuntimeError("the policy engine did not stop")


def start_engine() -> None:
    wsl("docker start aiops-opa", timeout=60)
    for _ in range(120):
        if engine_running():
            return
        time.sleep(0.5)
    raise RuntimeError("the policy engine did not come back")


def load_build_data():
    spec = importlib.util.spec_from_file_location("policy_build_data", POLICY_DIR / "build_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------------------------------------ servers
def run_server(application, port: int) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(application, host=HOST, port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(80):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("uvicorn did not start")


class FakeEngine(http.server.BaseHTTPRequestHandler):
    mode = "empty"

    def _answer(self) -> None:
        if self.command == "POST":
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
        modes = {
            "empty": (200, b"{}"),
            "error": (500, b"boom"),
            "html": (200, b"<html>not json</html>"),
            "string": (200, b'{"result": "allow"}'),
            "loose": (200, b'{"result": {"allow": "true", "reason": "permit"}}'),
        }
        status, body = modes[FakeEngine.mode]
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET = _answer

    def log_message(self, *_args) -> None:
        pass


# ------------------------------------------------------------------------------------------------ database fixtures
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


def key() -> str:
    return f"verify-{uuid.uuid4().hex}"


def approved_command(
    db: psycopg.Connection,
    *,
    requester: str = "operator:alice",
    approver: str | None = "supervisor:bob",
    approver_role: str | None = "supervisor",
    action: str = "diversion",
    adapter: str = "diversion_adapter",
    entity: str = "int-a2_int-a3",
) -> str:
    """An approved command written the way a person with write access to the database (not the API) could write it."""
    command_id, _ = request_command(
        db, key(), action, adapter, entity, requester, datetime.now(timezone.utc), "operator"
    )
    if approver is not None:
        command_repo.transition_command(
            db,
            command_id,
            "approved",
            approver,
            "fixture: approved by a direct write",
            approved_by=approver,
            policy_decision="approved",
            approved_by_role=approver_role,
        )
    return command_id


def target_decisions(db: psycopg.Connection, point: str, entity_id: str) -> list[tuple]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT decision, reason, policy_version, opa_decision_id FROM policy_decisions "
            "WHERE point = %s AND entity_type = 'target' AND entity_id = %s ORDER BY decided_at",
            (point, entity_id),
        )
        return cur.fetchall()


def status_of(db: psycopg.Connection, command_id: str) -> dict:
    return command_repo.get_command(db, command_id)


def decisions(db: psycopg.Connection, command_id: str) -> list[tuple]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT point, decision, reason, policy_version, opa_decision_id FROM policy_decisions WHERE entity_id = %s ORDER BY decided_at",
            (command_id,),
        )
        return cur.fetchall()


# ------------------------------------------------------------------------------------------------ Python reference oracles
def api_oracle(service: str, method: str, path: str, roles: list[str]) -> tuple[bool, str]:
    policy = policy_for(method, path, service)
    if policy is None:
        return False, "unlisted_endpoint"
    if policy.public:
        return True, "public"
    known = [r for r in roles if r in KNOWN_ROLES]
    if not known:
        return False, "no_operating_role"
    if policy.capability is None or policy.capability in capabilities_of(known):
        return True, "permit"
    return False, "forbidden"


def authority_oracle(
    kind: str, action: str, critical: bool, roles: list[str]
) -> tuple[bool, str | None, str]:
    sc = safety_class(action, critical)
    table = REQUEST_ROLES if kind == "request" else APPROVE_ROLES
    usable = sorted(r for r in roles if r in table[sc])
    reason = (
        "permit"
        if usable
        else ("role_cannot_request" if kind == "request" else "role_cannot_review")
    )
    return bool(usable), usable[0] if usable else None, reason


BASE_TIME = datetime(2026, 9, 18, 9, 0, 0, tzinfo=timezone.utc)
ACTIONS = [
    "variable_message_sign",
    "signal_plan_change",
    "diversion",
    "transit_priority",
    "emergency_preemption",
    "other",
    "made_up_action",
]
REC_STATUSES = ["proposed", "requested", "approved", "superseded", "expired", "executed", None]


def valid_base(rng: random.Random, phase: str) -> PolicyContext:
    action = rng.choice(
        [
            "variable_message_sign",
            "signal_plan_change",
            "diversion",
            "transit_priority",
            "emergency_preemption",
        ]
    )
    critical = rng.random() < 0.2
    sc = safety_class(action, critical)
    now = BASE_TIME + timedelta(microseconds=rng.randrange(0, 10**9))
    execution = phase == "execution"
    return PolicyContext(
        action_type=action,
        target_adapter=rng.choice(sorted(KNOWN_ADAPTERS)),
        target_entity_id="seg-1",
        requested_by="operator:alice",
        expires_at=now + timedelta(seconds=300),
        approver="supervisor:dan",
        approver_role=sorted(APPROVE_ROLES[sc])[0],
        now=now,
        target_exists=True,
        target_evidence_fresh=True,
        recommendation_id=None,
        recommendation_action_type=None,
        recommendation_status=None,
        safety_class=sc,
        requester_role=sorted(REQUEST_ROLES[sc])[0],
        critical_incident_on_target=critical,
        phase=phase,
        actor=EXECUTOR if execution else None,
        command_status="approved" if execution else None,
    )


def mutated_context(rng: random.Random) -> PolicyContext:
    """A context the policy would approve, with one to three things made wrong - so every rule is the deciding one many times."""
    ctx = valid_base(rng, rng.choice(["approval", "execution"]))

    def only_execution(change):
        return lambda c: replace(c, **change(c)) if c.phase == "execution" else c

    mutations = [
        lambda c: replace(c, expires_at=c.now - timedelta(seconds=rng.choice([0, 1, 60]))),
        lambda c: replace(c, approver=c.requested_by),
        lambda c: replace(c, approver_role=rng.choice(ALL_ROLES + ["made_up_role", None])),
        lambda c: replace(c, requester_role=rng.choice(ALL_ROLES + ["made_up_role"])),
        lambda c: replace(c, target_adapter="made_up_adapter"),
        lambda c: replace(c, target_exists=False),
        lambda c: replace(c, target_evidence_fresh=False),
        only_execution(
            lambda c: {"actor": rng.choice(["supervisor:dan", "system:outcome-verifier"])}
        ),
        only_execution(
            lambda c: {
                "command_status": rng.choice(["requested", "executing", "executed", "denied"])
            }
        ),
        only_execution(lambda c: {"approver": None, "approver_role": None}),
        lambda c: replace(
            c,
            recommendation_id="rec-1",
            recommendation_action_type=rng.choice([c.action_type, "diversion", None]),
            recommendation_status=rng.choice(REC_STATUSES),
        ),
    ]
    for mutate in rng.sample(mutations, rng.choice([0, 1, 1, 1, 2, 3])):
        ctx = mutate(ctx)
    return ctx


def random_context(rng: random.Random) -> PolicyContext:
    return mutated_context(rng) if rng.random() < 0.5 else fully_random_context(rng)


def fully_random_context(rng: random.Random) -> PolicyContext:
    phase = rng.choice(["approval", "approval", "execution", "execution"])
    action = rng.choice(ACTIONS)
    critical = rng.random() < 0.25
    requester = rng.choice(["operator:alice", "operator:bob", "dispatcher:carol"])
    approver: str | None = rng.choice([requester, "supervisor:dan", "supervisor:erin"])
    approver_role: str | None = rng.choice(
        ["supervisor", "supervisor", "incident_commander", "operator"]
        + ALL_ROLES
        + ["made_up_role"]
    )
    if phase == "execution" and rng.random() < 0.2:
        approver, approver_role = None, None
    elif phase == "execution" and rng.random() < 0.1:
        approver_role = None
    now = BASE_TIME + timedelta(microseconds=rng.randrange(0, 10**9))
    expires = now + timedelta(seconds=rng.choice([-3600, -1, 0, 1, 60, 300, 300, 300, 3600]))
    recommendation = rng.choice(["none", "none", "missing", "match", "match", "other"])
    rec_id = None if recommendation == "none" else "rec-1"
    rec_action = {"none": None, "missing": None, "match": action, "other": "diversion"}[
        recommendation
    ]
    if recommendation == "other" and action == "diversion":
        rec_action = "signal_plan_change"
    rec_status = None if recommendation in ("none", "missing") else rng.choice(REC_STATUSES[:-1])
    return PolicyContext(
        action_type=action,
        target_adapter=rng.choice(sorted(KNOWN_ADAPTERS) * 3 + ["made_up_adapter"]),
        target_entity_id=rng.choice(["seg-1", "int-a2", "corridor-a"]),
        requested_by=requester,
        expires_at=expires,
        approver=approver,
        approver_role=approver_role,
        now=now,
        target_exists=rng.random() < 0.85,
        target_evidence_fresh=rng.random() < 0.8,
        recommendation_id=rec_id,
        recommendation_action_type=rec_action,
        recommendation_status=rec_status,
        safety_class=safety_class(action, critical),
        requester_role=rng.choice(
            [None, "operator", "operator", "dispatcher"] + ALL_ROLES + ["made_up_role"]
        ),
        critical_incident_on_target=critical,
        phase=phase,
        actor=rng.choice([EXECUTOR] * 6 + ["supervisor:dan", "system:outcome-verifier"])
        if phase == "execution"
        else None,
        command_status=rng.choice(["approved"] * 5 + ["requested", "executing", "executed"])
        if phase == "execution"
        else None,
    )


# ------------------------------------------------------------------------------------------------ main
async def main() -> int:  # noqa: PLR0915
    # ============================================================ A. the policy is checked, tested and the engine serves it
    check = opa_cli("check", "--strict", "-b", "/policy")
    ev.check(
        "opa_check_strict_reports_no_problem_in_any_policy",
        check.returncode == 0 and not check.stdout.strip() and not check.stderr.strip(),
        detail=(check.stdout + check.stderr)[:200],
    )
    tests = opa_cli("test", "-b", "/policy")
    match = re.search(r"PASS:\s*(\d+)/(\d+)", tests.stdout)
    rego_passed = int(match.group(1)) if match else 0
    rego_total = int(match.group(2)) if match else -1
    ev.check(
        "every_rego_unit_test_passes_when_opa_runs_them",
        tests.returncode == 0 and rego_total > 0 and rego_passed == rego_total,
        detail=f"{rego_passed}/{rego_total}",
    )
    ev.metrics["rego_unit_tests"] = rego_total
    image = compose_image()
    running = wsl("docker inspect aiops-opa --format '{{.Config.Image}} {{.State.Running}}'")
    ev.check(
        "the_running_engine_is_the_image_pinned_by_digest_in_the_compose_file",
        running.stdout.strip() == f"{image} true",
        detail=running.stdout.strip()[:160],
    )
    engine_hardening = wsl(
        "docker inspect aiops-opa --format '{{.HostConfig.ReadonlyRootfs}} {{.HostConfig.CapDrop}} {{.HostConfig.SecurityOpt}} {{json .HostConfig.PortBindings}}'"
    )
    ev.check(
        "the_engine_runs_read_only_with_no_capabilities_and_listens_on_loopback_only",
        engine_hardening.stdout.startswith("true [ALL] [no-new-privileges:true]")
        and '"HostIp":"127.0.0.1"' in engine_hardening.stdout,
        detail=engine_hardening.stdout.strip()[:200],
    )
    build_data = load_build_data()
    on_disk = json.loads(build_data.DATA_PATH.read_text(encoding="utf-8"))
    ev.check(
        "the_policy_data_is_exactly_what_the_inventory_and_role_tables_generate",
        build_data.DATA_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")
        == build_data.render(build_data.build()),
    )
    ev.check(
        "the_engine_serves_the_policy_version_that_is_on_disk",
        pdp.loaded_version() == on_disk["version"],
        detail=f"engine={pdp.loaded_version()} disk={on_disk['version']}",
    )
    ev.metrics["policy_version"] = on_disk["version"]

    # ============================================================ B. differential: engine == Python reference
    inv = inventory()
    endpoints = [(a.get("service", "api"), a["method"], a["path"]) for a in inv["apis"]]
    endpoints += [
        ("api", "GET", "/api/v1/nothing"),
        ("api", "DELETE", "/api/v1/incidents"),
        ("api", "POST", "/api/v1/health"),
        ("scenario-control", "GET", "/api/v1/devices"),
        ("api", "POST", "/scenario-control/v1/runs"),
        ("made-up-service", "GET", "/api/v1/health"),
    ]
    role_sets: list[list[str]] = [[], ["made_up_role"], ["made_up_role", "auditor"]]
    role_sets += [[r] for r in ALL_ROLES]
    role_sets += [list(p) for p in itertools.combinations(HUMAN_ROLES, 2)]
    role_sets += [list(HUMAN_ROLES), ["system:command-executor", "operator"]]
    mismatches: list[str] = []
    api_cases = 0
    for (service, method, path), roles in itertools.product(endpoints, role_sets):
        got = pdp.api_decision(service, method, path, roles)
        want_allow, want_reason = api_oracle(service, method, path, roles)
        api_cases += 1
        if got["allow"] != want_allow or got["reason"] != want_reason:
            mismatches.append(
                f"{service} {method} {path} {roles}: {got['allow']}/{got['reason']} != {want_allow}/{want_reason}"
            )
    ev.check(
        "engine_and_reference_agree_on_every_endpoint_and_role_set_of_the_access_inventory",
        not mismatches and api_cases == len(endpoints) * len(role_sets),
        detail=f"{api_cases} cases; {mismatches[:2]}",
    )
    ev.metrics["differential_api_cases"] = api_cases

    authority_cases = 0
    authority_bad: list[str] = []
    role_sets_small = (
        [[]] + [[r] for r in ALL_ROLES] + [list(p) for p in itertools.combinations(ALL_ROLES, 2)]
    )
    for kind, action, critical, roles in itertools.product(
        ("request", "review"), ACTIONS, (False, True), role_sets_small
    ):
        got = pdp.command_authority(kind, action, critical, roles)
        want = authority_oracle(kind, action, critical, roles)
        authority_cases += 1
        if (got["allow"], got["role"], got["reason"]) != want or got[
            "safety_class"
        ] != safety_class(action, critical):
            authority_bad.append(f"{kind} {action} {critical} {roles}: {got}")
    ev.check(
        "engine_and_reference_agree_on_who_may_request_or_review_each_action_in_each_safety_class",
        not authority_bad,
        detail=f"{authority_cases} cases; {authority_bad[:1]}",
    )
    ev.metrics["differential_authority_cases"] = authority_cases

    rng = random.Random(20260921)
    command_cases, command_bad = 6000, []
    outcomes: dict[str, int] = {}
    for _ in range(command_cases):
        ctx = random_context(rng)
        want = evaluate(ctx)
        got = pdp.command_decision(context_to_input(ctx))
        outcomes[want.error_code or "approved"] = outcomes.get(want.error_code or "approved", 0) + 1
        if (got["decision"], got["error_code"], got["message"]) != (
            want.decision,
            want.error_code,
            want.message,
        ):
            command_bad.append(f"{ctx}\n  engine={got}\n  python={want}")
    ev.check(
        "engine_and_reference_agree_on_thousands_of_generated_command_contexts_decision_code_and_message",
        not command_bad,
        detail=f"{command_cases} contexts; {command_bad[:1]}",
    )
    ev.check(
        "the_generated_contexts_reach_every_kind_of_outcome_so_the_agreement_is_not_vacuous",
        {"approved", "expired", "policy_denied", "invalid_target", "stale_evidence"}
        <= set(outcomes)
        and min(outcomes.values()) >= 20,
        detail=str(outcomes),
    )
    ev.metrics["differential_command_contexts"] = command_cases
    ev.metrics["differential_command_outcomes"] = outcomes

    # ============================================================ C. default denial at the engine
    raw = httpx.Client(timeout=5)
    denials = {
        "empty_api_input": raw.post(
            f"{pdp.OPA_URL}/v1/data/aiops/api/authz/decision", json={"input": {}}
        ).json(),
        "garbage_api_input": raw.post(
            f"{pdp.OPA_URL}/v1/data/aiops/api/authz/decision",
            json={"input": {"service": 7, "method": None, "path": [], "roles": "operator"}},
        ).json(),
        "no_input_at_all": raw.post(
            f"{pdp.OPA_URL}/v1/data/aiops/api/authz/decision", json={}
        ).json(),
    }
    ev.check(
        "an_empty_or_garbage_api_input_is_a_denial_never_a_permit_or_an_undefined_answer",
        all(v.get("result", {}).get("allow") is False for v in denials.values()),
        detail=str(denials)[:200],
    )
    command_denials = [
        raw.post(f"{pdp.OPA_URL}/v1/data/aiops/command/decision", json={"input": body}).json()
        for body in (
            {},
            {"phase": "approval"},
            {"phase": "override"},
            {"phase": "execution", "actor": EXECUTOR},
        )
    ]
    ev.check(
        "an_empty_or_incomplete_command_input_is_denied_as_malformed_never_approved",
        all(
            v["result"]["decision"] == "denied" and "malformed" in v["result"]["message"]
            for v in command_denials
        ),
        detail=str(command_denials[0])[:200],
    )
    authority_denial = raw.post(
        f"{pdp.OPA_URL}/v1/data/aiops/command/authority", json={"input": {"kind": "request"}}
    ).json()["result"]
    ev.check(
        "an_incomplete_authority_question_is_denied",
        authority_denial["allow"] is False,
        detail=str(authority_denial),
    )
    unknown_query = raw.post(f"{pdp.OPA_URL}/v1/data/aiops/nothing/here", json={"input": {}}).json()
    ev.check(
        "a_question_the_engine_has_no_policy_for_yields_no_result_which_the_client_treats_as_unavailable",
        "result" not in unknown_query,
    )

    # ============================================================ D. through the real API, real tokens
    tokens = {
        name: oidc_client.login(user, IDENTITIES[user]["password"]).access_token
        for name, user in PEOPLE.items()
    }

    def h(who: str) -> dict:
        return {"Authorization": f"Bearer {tokens[who]}"}

    with psycopg.connect(dsn_from_env()) as db:
        migrate(db)
        with db.cursor() as cur:
            cur.execute(
                "UPDATE commands SET status = 'expired', updated_at = now() WHERE status = 'approved'"
            )
        db.commit()
        geometry = current_geometry(db)
        for corridor in ("corridor-a", "corridor-b"):
            fresh_kpi(db, corridor, "east", geometry)
        ev.check(
            "fixture_the_verification_database_has_a_geometry_and_topology", geometry is not None
        )

        server = run_server(app, API_PORT)
        demo_server = run_server(scenario_app, DEMO_PORT)
        fake_server = http.server.ThreadingHTTPServer((HOST, FAKE_PORT), FakeEngine)
        threading.Thread(target=fake_server.serve_forever, daemon=True).start()
        try:
            async with (
                httpx.AsyncClient(base_url=BASE, timeout=60) as c,
                httpx.AsyncClient(base_url=DEMO_BASE, timeout=60) as demo,
            ):
                # -------------------------------------------------------- default denial through the enforcement point
                mini = FastAPI(dependencies=[Depends(enforce)])

                @mini.get("/api/v1/not-in-the-inventory")
                def secret() -> dict:
                    return {"secret": True}

                mini_client = httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=mini), base_url="http://mini"
                )
                closed = await mini_client.get(
                    "/api/v1/not-in-the-inventory", headers=h("approver")
                )
                ev.check(
                    "a_route_missing_from_the_inventory_is_denied_by_default_even_to_a_supervisor",
                    closed.status_code == 403
                    and closed.json()["detail"]["error"] == "unlisted_endpoint",
                    detail=closed.text[:160],
                )
                original_policy_for = auth.policy_for
                auth.policy_for = lambda *_a, **_k: EndpointPolicy("map.view", False)
                try:
                    reached = await mini_client.get(
                        "/api/v1/not-in-the-inventory", headers=h("approver")
                    )
                finally:
                    auth.policy_for = original_policy_for
                ev.check(
                    "the_engine_denies_an_unlisted_route_on_its_own_when_the_enforcement_points_table_would_have_let_it_through",
                    reached.status_code == 403
                    and reached.json()["detail"]["error"] == "unlisted_endpoint",
                    detail=reached.text[:160],
                )
                await mini_client.aclose()

                # -------------------------------------------------------- cross-role: request and review authority
                created = await c.post(
                    "/api/v1/commands",
                    headers=h("requester"),
                    json={
                        "action_type": "diversion",
                        "target_entity_id": "int-a2_int-a3",
                        "idempotency_key": key(),
                    },
                )
                command_id = created.json().get("command_id")
                ev.check(
                    "an_operator_may_request_an_sc_1_diversion_and_the_engine_names_the_role_it_used",
                    created.status_code == 201 and created.json()["safety_class"] == "SC-1",
                    detail=created.text[:160],
                )
                asked = target_decisions(db, "command_request", "diversion_adapter/int-a2_int-a3")
                ev.check(
                    "the_request_decision_is_recorded_with_the_policy_version_and_the_engines_own_decision_id",
                    any(d == "permit" and v == on_disk["version"] and o for d, _r, v, o in asked),
                    detail=str(asked)[:200],
                )
                request_refusals = {}
                for who in ("dispatcher", "approver", "commander", "auditor", "field", "demo"):
                    r = await c.post(
                        "/api/v1/commands",
                        headers=h(who),
                        json={
                            "action_type": "diversion",
                            "target_entity_id": "int-a2_int-a3",
                            "idempotency_key": key(),
                        },
                    )
                    request_refusals[who] = (r.status_code, r.json()["detail"]["error"])
                ev.check(
                    "every_other_role_is_refused_a_request_for_an_sc_1_action_by_the_capability_or_by_the_engines_authority_decision",
                    all(code == 403 for code, _ in request_refusals.values())
                    and request_refusals["dispatcher"][1] == "role_cannot_request"
                    and request_refusals["approver"][1] == "forbidden",
                    detail=str(request_refusals),
                )
                review_refusals = {}
                for who in (
                    "second_operator",
                    "commander",
                    "dispatcher",
                    "auditor",
                    "field",
                    "demo",
                ):
                    r = await c.post(
                        f"/api/v1/commands/{command_id}/review",
                        headers=h(who),
                        json={"decision": "approve"},
                    )
                    review_refusals[who] = (r.status_code, r.json()["detail"]["error"])
                ev.check(
                    "a_role_holding_the_review_capability_but_not_authority_over_this_class_is_refused_by_the_engine",
                    review_refusals["second_operator"] == (403, "role_cannot_review")
                    and review_refusals["commander"] == (403, "role_cannot_review"),
                    detail=str(review_refusals),
                )
                ev.check(
                    "roles_without_the_review_capability_never_reach_a_decision",
                    all(
                        review_refusals[w][0] == 403 and review_refusals[w][1] == "forbidden"
                        for w in ("dispatcher", "auditor", "field", "demo")
                    ),
                    detail=str(review_refusals),
                )
                self_approval = await c.post(
                    f"/api/v1/commands/{command_id}/review",
                    headers=h("requester"),
                    json={"decision": "approve"},
                )
                ev.check(
                    "the_requester_cannot_approve_their_own_command",
                    self_approval.status_code == 403,
                    detail=self_approval.text[:160],
                )
                still = status_of(db, command_id)
                ev.check(
                    "none_of_the_refusals_changed_the_command",
                    still["status"] == "requested" and still["policy_decision"] == "pending",
                    detail=str(still["status"]),
                )
                refused_rows = [
                    d
                    for d in decisions(db, command_id)
                    if d[0] == "command_review" and d[1] == "denied"
                ]
                ev.check(
                    "the_engines_refusals_to_review_are_in_the_decision_record_with_the_reason",
                    len(refused_rows) >= 2
                    and all(r[2] == "role_cannot_review" for r in refused_rows),
                    detail=str(refused_rows)[:200],
                )
                approved = await c.post(
                    f"/api/v1/commands/{command_id}/review",
                    headers=h("approver"),
                    json={"decision": "approve"},
                )
                ev.check(
                    "a_supervisor_a_different_person_in_the_right_role_approves_and_the_engine_decides_the_approval",
                    approved.status_code == 200 and approved.json()["status"] == "approved",
                    detail=approved.text[:200],
                )
                trail = decisions(db, command_id)
                ev.check(
                    "the_approval_leaves_a_review_permit_and_an_approval_decision_naming_the_same_policy_version",
                    {p for p, d, *_ in trail if d in ("permit", "approved")}
                    >= {"command_review", "command_approval"}
                    and {v for _p, _d, _r, v, _o in trail if v} == {on_disk["version"]},
                    detail=str(trail)[:240],
                )
                transitions = command_repo.get_command(db, command_id)
                ev.check(
                    "an_approved_command_carries_no_error_from_an_earlier_failed_attempt",
                    transitions["error_code"] is None and transitions["error_message"] is None,
                )
                with db.cursor() as cur:
                    cur.execute(
                        "SELECT note FROM command_transitions WHERE command_id = %s AND to_status = 'approved'",
                        (command_id,),
                    )
                    note = cur.fetchone()[0]
                ev.check(
                    "the_approval_transition_names_the_policy_version_that_approved_it",
                    on_disk["version"] in note,
                    detail=note,
                )
                ev.check(
                    "the_policy_decision_table_refuses_update_delete_and_truncate",
                    all(
                        _refuses(db, sql)
                        for sql in (
                            "UPDATE policy_decisions SET decision = 'permit'",
                            "DELETE FROM policy_decisions",
                            "TRUNCATE policy_decisions",
                        )
                    ),
                )
                _ = transitions
                command_repo.transition_command(
                    db, command_id, "expired", "verify", "fixture cleanup"
                )

                # -------------------------------------------------------- target escape
                escapes = {}
                for label, action, entity in (
                    ("a_diversion_aimed_at_an_intersection", "diversion", "int-a2"),
                    (
                        "a_signal_change_aimed_at_a_road_segment",
                        "signal_plan_change",
                        "int-a2_int-a3",
                    ),
                    ("a_sign_aimed_at_a_corridor", "variable_message_sign", "corridor-a"),
                    ("a_target_that_does_not_exist", "diversion", "no-such-segment"),
                    ("a_target_that_is_an_injection_attempt", "diversion", "x' OR '1'='1"),
                    ("a_target_with_path_traversal", "diversion", "../../etc/passwd"),
                ):
                    body = {
                        "action_type": action,
                        "target_entity_id": entity,
                        "idempotency_key": key(),
                    }
                    if action == "signal_plan_change":
                        body["deviation_s"] = 5
                    if action == "variable_message_sign":
                        body["message"] = "Slow traffic ahead"
                    made = await c.post("/api/v1/commands", headers=h("requester"), json=body)
                    if made.status_code != 201:
                        escapes[label] = f"request {made.status_code}"
                        continue
                    who = "approver" if action != "variable_message_sign" else "second_operator"
                    verdict = await c.post(
                        f"/api/v1/commands/{made.json()['command_id']}/review",
                        headers=h(who),
                        json={"decision": "approve"},
                    )
                    after = status_of(db, made.json()["command_id"])
                    escapes[label] = (verdict.status_code, after["status"], after["error_code"])
                ev.check(
                    "a_command_aimed_outside_what_its_adapter_controls_is_denied_at_approval_as_an_invalid_target",
                    all(v == (200, "denied", "invalid_target") for v in escapes.values()),
                    detail=str(escapes),
                )
                escaped_status = {v[1] for v in escapes.values() if isinstance(v, tuple)}
                ev.check(
                    "no_escaped_target_reached_the_approved_state",
                    "approved" not in escaped_status and len(escapes) == 6,
                )

                # -------------------------------------------------------- E. the executor's own gate
                geom = geometry
                fresh_kpi(db, "corridor-a", "east", geometry)

                async def gate_for(command_id: str) -> policy_module.PolicyResult:
                    return policy_module.execution_gate(db, status_of(db, command_id), None, geom)

                legit = approved_command(db)
                verdict = await gate_for(legit)
                ev.check(
                    "a_legitimately_approved_command_with_fresh_evidence_passes_the_executors_gate",
                    verdict.decision == "approved" and verdict.policy_version == on_disk["version"],
                    detail=f"{verdict.decision} {verdict.message}",
                )
                command_repo.transition_command(db, legit, "expired", "verify", "fixture cleanup")

                def run_gate_case(
                    label: str, command_id: str, expect: tuple[str, str | None], fragment: str
                ) -> None:
                    executor_worker._gate_retry_after.clear()
                    handled = executor_worker.run_once(db)
                    after = status_of(db, command_id)
                    rows = [d for d in decisions(db, command_id) if d[0] == "command_execution"]
                    ev.check(
                        label,
                        handled == command_id
                        and (after["status"], after["error_code"]) == expect
                        and fragment in (after["error_message"] or "")
                        and rows
                        and rows[-1][1] in ("denied", "expired"),
                        detail=f"handled={handled} status={after['status']} code={after['error_code']} msg={after['error_message']}",
                    )

                same_person = approved_command(
                    db, requester="operator:alice", approver="operator:alice"
                )
                run_gate_case(
                    "the_executor_refuses_a_command_whose_recorded_approver_is_its_requester",
                    same_person,
                    ("denied", "policy_denied"),
                    "four-eyes",
                )
                wrong_role = approved_command(db, approver="operator:bob", approver_role="operator")
                run_gate_case(
                    "the_executor_refuses_a_command_approved_in_a_role_that_could_not_approve_its_class",
                    wrong_role,
                    ("denied", "policy_denied"),
                    "may not approve",
                )
                ghost = approved_command(db, entity="no-such-segment")
                run_gate_case(
                    "the_executor_refuses_a_command_whose_target_is_not_a_real_registered_target",
                    ghost,
                    ("denied", "invalid_target"),
                    "not a real, currently registered target",
                )
                bad_adapter = approved_command(db)
                with db.cursor() as cur:
                    cur.execute(
                        "UPDATE commands SET target_adapter = 'made_up_adapter' WHERE command_id = %s",
                        (bad_adapter,),
                    )
                db.commit()
                run_gate_case(
                    "the_executor_refuses_a_command_naming_an_unregistered_adapter",
                    bad_adapter,
                    ("denied", "invalid_target"),
                    "unknown adapter",
                )
                stale_target = None
                for candidate in ("int-c4", "int-c3", "int-d2", "int-d3", "int-b3"):
                    probe = approved_command(
                        db,
                        action="signal_plan_change",
                        adapter="signal_controller_adapter",
                        entity=candidate,
                    )
                    ctx = build_context(
                        db,
                        status_of(db, probe),
                        "sam",
                        "supervisor",
                        datetime.now(timezone.utc),
                        geom,
                    )
                    if ctx.target_exists and not ctx.target_evidence_fresh:
                        stale_target = probe
                        break
                    command_repo.transition_command(
                        db, probe, "expired", "verify", "fixture cleanup"
                    )
                ev.check(
                    "fixture_an_intersection_with_no_fresh_evidence_exists",
                    stale_target is not None,
                )
                if stale_target:
                    run_gate_case(
                        "the_executor_refuses_a_command_whose_evidence_went_stale_after_approval_and_marks_it_retryable",
                        stale_target,
                        ("denied", "stale_evidence"),
                        "not fresh",
                    )
                    ev.check(
                        "a_stale_evidence_refusal_is_recorded_as_retryable",
                        status_of(db, stale_target)["error_retryable"] is True,
                    )
                no_approver = approved_command(db, approver=None)
                try:
                    with db.cursor() as cur:
                        cur.execute(
                            "UPDATE commands SET status = 'approved', approved_at = now() WHERE command_id = %s",
                            (no_approver,),
                        )
                    db.commit()
                    constructed = True
                except psycopg.Error:
                    db.rollback()
                    constructed = False
                if constructed:
                    run_gate_case(
                        "the_executor_refuses_an_approved_command_that_records_no_approval",
                        no_approver,
                        ("denied", "policy_denied"),
                        "no approval is recorded",
                    )
                else:
                    ev.check("the_database_refuses_an_approved_command_with_no_approver", True)
                impostor = approved_command(db)
                imp_ctx = build_context(
                    db,
                    status_of(db, impostor),
                    "supervisor:bob",
                    "supervisor",
                    datetime.now(timezone.utc),
                    geom,
                    phase="execution",
                    actor="sam.okafor",
                )
                imp = policy_module._ask_engine(db, imp_ctx, "command_execution", impostor)
                ev.check(
                    "a_caller_that_is_not_the_executor_identity_is_refused_execution_even_for_a_perfect_command",
                    imp.decision == "denied" and "EXECUTE authority" in imp.message,
                    detail=str(imp),
                )
                command_repo.transition_command(
                    db, impostor, "expired", "verify", "fixture cleanup"
                )
                rows = decisions(db, same_person)
                ev.check(
                    "every_execution_time_decision_is_recorded_against_the_command_with_the_policy_version",
                    rows
                    and all(
                        v == on_disk["version"]
                        for _p, _d, _r, v, _o in rows
                        if _p == "command_execution"
                    ),
                    detail=str(rows)[:200],
                )
                with db.cursor() as cur:
                    cur.execute(
                        "SELECT changed_by, note FROM command_transitions WHERE command_id = %s AND to_status = 'denied'",
                        (same_person,),
                    )
                    who_denied, why = cur.fetchone()
                ev.check(
                    "the_refusal_is_attributed_to_the_executor_identity_and_names_the_policy_version",
                    who_denied == EXECUTOR and on_disk["version"] in why,
                    detail=f"{who_denied}: {why}",
                )

                # -------------------------------------------------------- F. policy outage
                hold = approved_command(db, requester="operator:alice", approver="operator:alice")
                waiting = await c.post(
                    "/api/v1/commands",
                    headers=h("requester"),
                    json={
                        "action_type": "diversion",
                        "target_entity_id": "int-a2_int-a3",
                        "idempotency_key": key(),
                    },
                )
                waiting_id = waiting.json()["command_id"]
                unavailable_before = sum(
                    1 for d in decisions(db, waiting_id) if d[1] == "unavailable"
                )
                stop_engine()
                try:
                    outage = {}
                    for who in (
                        "requester",
                        "approver",
                        "commander",
                        "auditor",
                        "field",
                        "demo",
                        "dispatcher",
                        "second_operator",
                    ):
                        r = await c.get("/api/v1/me", headers=h(who))
                        outage[who] = (r.status_code, r.json().get("detail", {}).get("error"))
                    ev.check(
                        "with_the_engine_stopped_every_role_is_refused_with_503_policy_unavailable_never_served",
                        all(v == (503, "policy_unavailable") for v in outage.values()),
                        detail=str(outage),
                    )
                    ev.check(
                        "a_protected_data_endpoint_is_also_503_during_the_outage_for_the_most_privileged_role",
                        (await c.get("/api/v1/commands", headers=h("approver"))).status_code == 503
                        and (await c.get("/api/v1/incidents", headers=h("commander"))).status_code
                        == 503,
                    )
                    health = await c.get("/api/v1/health")
                    ev.check(
                        "public_health_stays_available_during_the_outage",
                        health.status_code == 200,
                    )
                    demo_outage = await demo.get("/scenario-control/v1/runs", headers=h("demo"))
                    ev.check(
                        "the_scenario_control_api_also_fails_closed_during_the_outage",
                        demo_outage.status_code == 503
                        and demo_outage.json()["detail"]["error"] == "policy_unavailable",
                        detail=demo_outage.text[:160],
                    )
                    demo_health = await demo.get("/scenario-control/v1/health")
                    ev.check(
                        "scenario_control_public_health_stays_available_too",
                        demo_health.status_code == 200,
                    )
                    create_outage = await c.post(
                        "/api/v1/commands",
                        headers=h("requester"),
                        json={
                            "action_type": "diversion",
                            "target_entity_id": "int-a2_int-a3",
                            "idempotency_key": key(),
                        },
                    )
                    ev.check(
                        "a_command_cannot_be_requested_while_the_engine_is_down",
                        create_outage.status_code == 503,
                        detail=create_outage.text[:160],
                    )
                    review_outage = await c.post(
                        f"/api/v1/commands/{waiting_id}/review",
                        headers=h("approver"),
                        json={"decision": "approve"},
                    )
                    ev.check(
                        "an_approval_attempt_during_the_outage_is_refused_with_503_and_changes_nothing",
                        review_outage.status_code == 503
                        and status_of(db, waiting_id)["status"] == "requested",
                        detail=review_outage.text[:160],
                    )
                    # the token is validated by the enforcement point BEFORE the engine is asked: identity failures are still 401
                    ev.check(
                        "an_invalid_token_is_still_401_during_the_outage_identity_is_checked_before_policy",
                        (
                            await c.get("/api/v1/me", headers={"Authorization": "Bearer garbage"})
                        ).status_code
                        == 401,
                    )
                    # review at the service layer with the engine down: policy_unavailable, command stays requested
                    from backend.control.command_service import review_command  # noqa: PLC0415

                    svc = review_command(
                        db,
                        waiting_id,
                        "supervisor:bob",
                        "supervisor",
                        datetime.now(timezone.utc),
                        geom,
                    )
                    after = status_of(db, waiting_id)
                    ev.check(
                        "the_approval_service_leaves_the_command_requested_and_records_that_the_policy_was_unavailable",
                        svc == "requested"
                        and after["status"] == "requested"
                        and after["policy_decision"] == "policy_unavailable",
                        detail=f"{svc} {after['policy_decision']}",
                    )
                    # the executor holds an approved command rather than executing it
                    executor_worker._gate_retry_after.clear()
                    handled = executor_worker.run_once(db)
                    held = status_of(db, hold)
                    unavailable_rows = [d for d in decisions(db, hold) if d[1] == "unavailable"]
                    handled_again = executor_worker.run_once(db)
                    unavailable_rows_after = [
                        d for d in decisions(db, hold) if d[1] == "unavailable"
                    ]
                    ev.check(
                        "during_the_outage_the_executor_holds_an_approved_command_it_does_not_execute_or_refuse_it",
                        handled is None
                        and held["status"] == "approved"
                        and len(unavailable_rows) == 1,
                        detail=f"handled={handled} status={held['status']} rows={len(unavailable_rows)}",
                    )
                    ev.check(
                        "the_executor_asks_again_only_after_its_retry_interval_so_an_outage_does_not_flood_the_decision_record",
                        handled_again is None and len(unavailable_rows_after) == 1,
                        detail=f"rows after a second attempt: {len(unavailable_rows_after)}",
                    )
                    ev.check(
                        "the_outage_itself_is_in_the_decision_record_as_unavailable_not_as_a_denial",
                        unavailable_rows[0][1] == "unavailable",
                    )
                finally:
                    start_engine()
                recovered = await c.get("/api/v1/me", headers=h("approver"))
                ev.check(
                    "when_the_engine_returns_the_same_request_is_served",
                    recovered.status_code == 200,
                    detail=str(recovered.status_code),
                )
                ev.check(
                    "the_platform_status_screen_reports_the_policy_engine_healthy_with_its_version",
                    _policy_probe(
                        await c.get("/api/v1/ops/status", headers=h("auditor")),
                        "healthy",
                        on_disk["version"],
                    ),
                )
                executor_worker._gate_retry_after.clear()
                handled = executor_worker.run_once(db)
                resolved = status_of(db, hold)
                ev.check(
                    "once_the_engine_is_back_the_held_command_is_decided_and_this_one_is_refused_for_four_eyes",
                    handled == hold
                    and resolved["status"] == "denied"
                    and "four-eyes" in resolved["error_message"],
                    detail=f"{resolved['status']} {resolved['error_message']}",
                )
                after_recovery = await c.post(
                    f"/api/v1/commands/{waiting_id}/review",
                    headers=h("approver"),
                    json={"decision": "approve"},
                )
                ev.check(
                    "the_command_that_waited_out_the_outage_can_be_approved_normally_afterwards_and_no_longer_shows_the_outage_as_an_error",
                    after_recovery.status_code == 200
                    and after_recovery.json()["status"] == "approved"
                    and after_recovery.json()["error"] is None,
                    detail=after_recovery.text[:160],
                )
                command_repo.transition_command(
                    db, waiting_id, "expired", "verify", "fixture cleanup"
                )
                ev.check(
                    "the_outage_left_an_audit_row_for_the_signed_in_person_who_was_refused",
                    _audited_outage(db),
                )
                unavailable_after = sum(
                    1 for d in decisions(db, waiting_id) if d[1] == "unavailable"
                )
                ev.check(
                    "the_approval_outage_is_a_recorded_unavailable_decision_on_that_command",
                    unavailable_after > unavailable_before,
                )

                # -------------------------------------------------------- an engine that answers nonsense
                original_url = pdp.OPA_URL
                pdp.OPA_URL = f"http://{HOST}:{FAKE_PORT}"
                try:
                    nonsense = {}
                    for mode in ("empty", "error", "html", "string", "loose"):
                        FakeEngine.mode = mode
                        r = await c.get("/api/v1/me", headers=h("approver"))
                        nonsense[mode] = r.status_code
                finally:
                    pdp.OPA_URL = original_url
                ev.check(
                    "an_engine_that_answers_nothing_an_error_html_a_bare_string_or_a_loosely_typed_permit_is_treated_as_unavailable",
                    set(nonsense.values()) == {503},
                    detail=str(nonsense),
                )

                # -------------------------------------------------------- G. hardening
                samples = {
                    "200": await c.get("/api/v1/health"),
                    "401": await c.get("/api/v1/me"),
                    "403": await c.get("/api/v1/commands", headers=h("field")),
                    "404": await c.get("/api/v1/nothing-here", headers=h("approver")),
                    "422": await c.post("/api/v1/commands", headers=h("second_operator"), json={}),
                }
                wanted = {
                    "x-content-type-options": "nosniff",
                    "x-frame-options": "DENY",
                    "referrer-policy": "no-referrer",
                    "cache-control": "no-store",
                }
                ev.check(
                    "every_response_kind_carries_the_security_headers",
                    all(
                        all(r.headers.get(k) == v for k, v in wanted.items())
                        and "default-src 'none'" in r.headers.get("content-security-policy", "")
                        for r in samples.values()
                    ),
                    detail=str({k: r.status_code for k, r in samples.items()}),
                )
                cors = await c.get("/api/v1/health", headers={"Origin": "https://evil.example"})
                preflight = await c.options(
                    "/api/v1/commands",
                    headers={
                        "Origin": "https://evil.example",
                        "Access-Control-Request-Method": "POST",
                    },
                )
                ev.check(
                    "no_cross_origin_access_is_granted_to_any_origin_not_even_for_a_preflight",
                    not any(k.lower().startswith("access-control-") for k in cors.headers)
                    and not any(k.lower().startswith("access-control-") for k in preflight.headers),
                    detail=f"preflight {preflight.status_code}",
                )
                docs_codes = [
                    (await c.get(path)).status_code for path in ("/docs", "/redoc", "/openapi.json")
                ] + [(await demo.get("/openapi.json")).status_code]
                ev.check(
                    "the_interactive_docs_and_the_schema_are_not_served",
                    set(docs_codes) == {404},
                    detail=str(docs_codes),
                )
                big = await c.post(
                    "/api/v1/commands",
                    headers=h("second_operator"),
                    content=b"x" * 70_000,
                )
                ok_size = await c.post("/api/v1/commands", headers=h("second_operator"), json={})
                ev.check(
                    "a_body_over_the_size_limit_is_refused_with_413_before_it_is_parsed_and_a_small_one_is_not",
                    big.status_code == 413
                    and big.json()["detail"]["error"] == "body_too_large"
                    and ok_size.status_code == 422,
                    detail=f"{big.status_code} {ok_size.status_code}",
                )

                async def chunks():
                    for _ in range(100):
                        yield b"x" * 1024

                try:
                    streamed = (
                        await c.post(
                            "/api/v1/commands", headers=h("second_operator"), content=chunks()
                        )
                    ).status_code
                except httpx.TransportError as exc:
                    streamed = f"connection closed by the server before the upload finished ({type(exc).__name__})"
                ev.check(
                    "an_oversize_body_sent_in_chunks_without_a_declared_length_is_refused_with_413_or_the_connection_is_closed",
                    streamed == 413 or isinstance(streamed, str),
                    detail=str(streamed),
                )
                ev.check(
                    "the_413_response_also_carries_the_security_headers",
                    big.headers.get("x-content-type-options") == "nosniff",
                )

                # rate limits: a runaway script by one person is throttled; someone else is unaffected
                async def hammer(
                    who: str, method: str, path: str, count: int
                ) -> list[httpx.Response]:
                    if method == "GET":
                        return await asyncio.gather(
                            *[c.get(path, headers=h(who)) for _ in range(count)]
                        )
                    return await asyncio.gather(
                        *[c.post(path, headers=h(who), json={}) for _ in range(count)]
                    )

                writes = await hammer("field", "POST", "/api/v1/commands", 90)
                codes = sorted({r.status_code for r in writes})
                throttled = [r for r in writes if r.status_code == 429]
                ev.check(
                    "a_flood_of_writes_by_one_person_is_throttled_with_429_and_a_retry_after",
                    len(throttled) >= 30
                    and all(int(r.headers["retry-after"]) >= 1 for r in throttled)
                    and throttled[0].json()["detail"]["error"] == "rate_limited",
                    detail=f"{len(throttled)} of {len(writes)} throttled; codes {codes}",
                )
                reads_ok = await c.get("/api/v1/me", headers=h("approver"))
                ev.check(
                    "someone_else_is_unaffected_while_that_person_is_throttled",
                    reads_ok.status_code == 200,
                )
                same_person_read = await c.get("/api/v1/me", headers=h("field"))
                ev.check(
                    "the_throttled_persons_reads_are_not_starved_by_their_write_flood",
                    same_person_read.status_code == 200,
                    detail=str(same_person_read.status_code),
                )
                burst = await hammer("demo", "GET", "/api/v1/me", 250)
                ev.check(
                    "with_the_default_limits_a_busy_session_reading_250_times_at_once_is_never_throttled",
                    all(r.status_code == 200 for r in burst)
                    and hardening.READS.burst >= 300
                    and hardening.READS.rate >= 100,
                    detail=f"{sum(1 for r in burst if r.status_code == 200)} of 250 served; limit {hardening.READS.rate}/s burst {hardening.READS.burst}",
                )
                saved = (hardening.READS.rate, hardening.READS.burst)
                hardening.READS.rate, hardening.READS.burst = 10.0, 50.0
                try:
                    reads = await hammer("dispatcher", "GET", "/api/v1/me", 300)
                finally:
                    hardening.READS.rate, hardening.READS.burst = saved
                read_throttled = [r for r in reads if r.status_code == 429]
                ev.check(
                    "a_flood_of_reads_is_throttled_when_it_exceeds_the_configured_allowance",
                    len(read_throttled) >= 100
                    and sum(1 for r in reads if r.status_code == 200) >= 50
                    and all(r.headers.get("retry-after") for r in read_throttled),
                    detail=f"{sum(1 for r in reads if r.status_code == 200)} served, {len(read_throttled)} throttled (limit lowered to 10/s, burst 50 for this check)",
                )
                wait = int(throttled[0].headers["retry-after"]) + 1
                await asyncio.sleep(min(wait, 12))
                back = await c.post("/api/v1/commands", headers=h("field"), json={})
                ev.check(
                    "after_the_retry_after_interval_the_person_is_served_again",
                    back.status_code == 403,
                    detail=str(back.status_code),
                )
                # unidentified callers: garbage tokens are throttled per address; signed-in people at the same address are not
                garbage = await asyncio.gather(
                    *[
                        c.get("/api/v1/me", headers={"Authorization": f"Bearer garbage{i}"})
                        for i in range(60)
                    ]
                )
                first_401 = sum(1 for r in garbage if r.status_code == 401)
                limited = [r for r in garbage if r.status_code == 429]
                ev.check(
                    "guessing_tokens_is_answered_401_for_a_burst_then_throttled_with_429",
                    first_401 >= 15
                    and len(limited) >= 20
                    and limited[0].headers.get("retry-after"),
                    detail=f"{first_401} x 401, {len(limited)} x 429",
                )
                signed_in = await c.get("/api/v1/me", headers=h("commander"))
                ev.check(
                    "a_signed_in_person_at_the_same_address_is_not_locked_out_by_someone_elses_failed_guesses",
                    signed_in.status_code == 200,
                    detail=str(signed_in.status_code),
                )
                demo_garbage = await demo.get(
                    "/scenario-control/v1/runs", headers={"Authorization": "Bearer nope"}
                )
                ev.check(
                    "the_scenario_control_api_shares_the_address_throttle",
                    demo_garbage.status_code in (401, 429),
                )

                # -------------------------------------------------------- decision latency
                timings = []
                for _ in range(400):
                    started = time.perf_counter()
                    pdp.api_decision("api", "GET", "/api/v1/map", ["operator"])
                    timings.append((time.perf_counter() - started) * 1000)
                timings.sort()
                p50, p95 = statistics.median(timings), timings[int(len(timings) * 0.95)]
                ev.metrics["api_decision_latency_ms"] = {
                    "p50": round(p50, 2),
                    "p95": round(p95, 2),
                    "n": len(timings),
                }
                ev.check(
                    "an_api_authorization_decision_is_fast_enough_not_to_matter_p95_under_25_ms_on_this_workstation",
                    p95 < 25.0,
                    detail=f"p50 {p50:.1f} ms, p95 {p95:.1f} ms",
                )
        finally:
            server.should_exit = True
            demo_server.should_exit = True
            fake_server.shutdown()
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE commands SET status = 'expired', updated_at = now() WHERE status IN ('approved')"
                )
            db.commit()
    return ev.finish()


def _refuses(db: psycopg.Connection, sql: str) -> bool:
    try:
        with db.cursor() as cur:
            cur.execute(sql)
        db.rollback()
        return False
    except psycopg.Error as exc:
        db.rollback()
        return "append-only" in str(exc)


def _policy_probe(response: httpx.Response, status: str, version: str) -> bool:
    if response.status_code != 200:
        return False
    items = [s for s in response.json().get("services", []) if s.get("id") == "policy-engine"]
    return bool(items) and items[0]["status"] == status and version in items[0]["detail"]


def _audited_outage(db: psycopg.Connection) -> bool:
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM operator_audit WHERE outcome = 'denied' AND detail->>'reason' = 'the policy engine was unavailable' AND at > now() - interval '15 minutes'"
        )
        return cur.fetchone()[0] >= 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
