"""P10.08 acceptance evidence: registered remediation actions are bounded and policy-controlled.

    python source-code/backend/aiops/verify_remediation.py

Needs the running platform (PostgreSQL, the policy engine `aiops-opa` serving the current policy), migration 0030 and Docker (the
adapter check restarts the real edge runtime container).

A. Registry and policy data. Every action of the six kinds is registered and bounded; the policy data OPA serves is exactly
   what the registry generates.
B. The policy itself. `opa check --strict` and `opa test` pass, and a mutation run proves the tests bite: a dozen deliberate
   breakages of the rules (an off-by-one cooldown, a removed approver check, an unanchored target pattern ...) must each make
   the tests fail.
C. The serving engine answers a decision matrix, and when it is unreachable the worker does nothing (fail closed).
D. Structure, in a scratch database. A plan-only request cannot reach `executing`; an approval-required request cannot run
   without a named approver and the worker's own role cannot approve or edit what was asked; a second in-flight request is
   refused by the database; a repeat of one attempt is refused by its idempotency key; the history is a hash chain.
E. Worker behaviour on hand-built (SYNTHETIC) incidents created through the real correlator, with the real policy engine
   and the real repository, and recording stand-ins for the adapters and for Prometheus: an auto action runs once, recovery is
   verified from the alerts and not from the adapter, failures retry within cooldown and budget and then ESCALATE, an action
   needing approval waits for a person, a plan-only action is never executed, one action at a time, a dead worker's request
   is closed as abandoned, an unreadable Prometheus never yields "recovered".
F. The adapters, for real: the docker adapter restarts the real edge runtime container; the model registry adapter rolls a copy
   of the real model registry back and refuses a corrupted previous version; the sampling adapter writes and clears a bounded
   override.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg import errors

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend import audit_chain, pdp  # noqa: E402
from backend.aiops import correlation, remediation, remediation_adapters  # noqa: E402
from backend.aiops.remediation import ACTIONS, PLATFORM_ACTOR  # noqa: E402
from backend.aiops.remediation_adapters import Outcome, PlanOnlyAdapter  # noqa: E402
from backend.aiops.remediation_worker import Worker  # noqa: E402
from backend.demo import world  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.repositories import platform_incidents as incidents  # noqa: E402
from backend.repositories import remediation as repo  # noqa: E402
from database import rotate_service_secrets  # noqa: E402
from database.migrate import dsn_from_env, migrate  # noqa: E402
from policy import build_data  # noqa: E402

ev = Evidence("P10.08", "p10_08_remediation", docs_name="p10_08_remediation")
DB = "aiops_p1008"
T0 = datetime(2026, 6, 1, 8, 0, 0, tzinfo=UTC)
POLICY_DIR = SOURCE_ROOT / "policy"
COMPOSE = SOURCE_ROOT / "infra" / "platform" / "docker-compose.yml"


# ------------------------------------------------------------------------------------------------ helpers
def wsl_path(path: Path) -> str:
    text = str(path.resolve())
    return f"/mnt/{text[0].lower()}{text[2:].replace(chr(92), '/')}" if os.name == "nt" else text


def opa_image() -> str:
    match = re.search(
        r"image:\s*(openpolicyagent/opa:\S+@sha256:[0-9a-f]{64})",
        COMPOSE.read_text(encoding="utf-8"),
    )
    return match.group(1)


def opa_cli(directory: Path, *args: str) -> subprocess.CompletedProcess:
    argv = ["docker"] if shutil.which("docker") else ["wsl", "-e", "docker"]
    return subprocess.run(  # noqa: S603
        [*argv, "run", "--rm", "-v", f"{wsl_path(directory)}:/policy:ro", opa_image(), *args, "/policy"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, check=False,
    )  # fmt: skip


def fresh_database() -> None:
    os.environ["POSTGRES_DB"] = "aiops"
    world.use_database(DB)
    if world.database_exists(DB):
        world.drop_database(DB)
    with psycopg.connect(world._maintenance_dsn(), autocommit=True) as conn:  # noqa: SLF001
        conn.execute(f'CREATE DATABASE "{DB}"')
    with psycopg.connect(dsn_from_env()) as conn:
        migrate(conn)


def connect(role: str | None = None) -> psycopg.Connection:
    os.environ["POSTGRES_DB"] = DB
    return psycopg.connect(dsn_from_env(role=role), autocommit=True)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def refused(
    conn: psycopg.Connection, statement: str, params: tuple = (), expect=psycopg.Error
) -> bool:
    try:
        with conn.transaction():
            conn.execute(statement, params)
    except expect:
        return True
    return False


# ------------------------------------------------------------------------------------------ A. registry, data
def registry_checks() -> None:
    ev.check(
        "all_six_kinds_are_registered_and_every_action_is_bounded_by_target_parameter_cooldown_window_budget_and_timeout",
        {a.kind for a in ACTIONS.values()} == set(remediation.KINDS)
        and all(
            a.targets and a.cooldown_s and a.max_per_window and a.execute_timeout_s
            for a in ACTIONS.values()
        ),
        ", ".join(f"{a.kind}:{a.id}" for a in ACTIONS.values()),
    )
    autonomy = {a.id: {t.id: t.autonomy for t in a.targets} for a in ACTIONS.values()}
    ev.metrics["registry"] = {
        "autonomy_by_target": autonomy,
        "max_in_flight": remediation.MAX_IN_FLIGHT,
        "max_attempts_per_incident": remediation.MAX_ATTEMPTS_PER_INCIDENT,
        "playbook_lines": len(remediation.PLAYBOOK),
    }
    ev.check(
        "failover_scale_and_quarantine_are_registered_but_plan_only_and_no_stateful_service_restarts_without_a_person",
        all(
            t.autonomy == "plan_only"
            for k in ("failover", "scale", "quarantine")
            for a in ACTIONS.values()
            if a.kind == k
            for t in a.targets
        )
        and all(
            t.autonomy == "approval"
            for t in ACTIONS["restart_container"].targets
            if t.id
            in {"aiops-postgres", "aiops-kafka-broker", "aiops-keycloak", "aiops-mqtt-broker"}
        ),
    )
    try:
        current = build_data.render(build_data.build()) == (
            POLICY_DIR / "aiops" / "model" / "data.json"
        ).read_text(encoding="utf-8").replace("\r\n", "\n")
    except (OSError, AssertionError):
        current = False
    ev.check("the_policy_data_document_is_exactly_what_the_registry_generates", current)
    served = pdp.loaded_version()
    wanted = build_data.build()
    ev.check(
        "the_running_policy_engine_serves_the_current_policy_version_and_the_registry_data",
        served == wanted["version"] and _served_remediation_data() == wanted["remediation"],
        f"served {served}, wanted {wanted['version']}",
    )


def _served_remediation_data() -> dict | None:
    try:
        response = pdp._http().get(f"{pdp.OPA_URL}/v1/data/aiops/model/remediation")  # noqa: SLF001
        return response.json().get("result")
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------------------------------------ B. policy
MUTATIONS: list[tuple[str, str, str]] = [
    (
        "cooldown boundary off by one",
        "input.history.seconds_since_last_attempt_on_target < action.cooldown_s",
        "input.history.seconds_since_last_attempt_on_target <= action.cooldown_s",
    ),
    (
        "rate window off by one",
        "input.history.attempts_on_target_in_window >= action.max_per_window",
        "input.history.attempts_on_target_in_window > action.max_per_window",
    ),
    (
        "per-action attempts off by one",
        "input.history.attempts_for_action >= action.max_attempts_per_incident",
        "input.history.attempts_for_action > action.max_attempts_per_incident",
    ),
    (
        "per-incident budget off by one",
        "input.history.attempts_for_incident >= reg.max_attempts_per_incident",
        "input.history.attempts_for_incident > reg.max_attempts_per_incident",
    ),
    (
        "in-flight limit off by one",
        "input.history.in_flight_total >= reg.max_in_flight",
        "input.history.in_flight_total > reg.max_in_flight",
    ),
    (
        "a person's ownership ignored",
        "input.incident.status in reg.held_by_person_statuses",
        "false",
    ),
    ("lower parameter bound off by one", "value < bounds.min", "value <= bounds.min"),
    ("upper parameter bound removed", "value > bounds.max", "false"),
    ("plan-only treated as auto", 'autonomy == "plan_only"\n}', 'autonomy == "never"\n}'),
    ("approver role not checked", "not input.approval.role in reg.approver_roles", "false"),
    ("self-approval allowed", "input.approval.by == reg.actor", "false"),
    ("wrong actor ranked below the rest", '"rank": 10,', '"rank": 200,'),
    ("target pattern not anchored", '"^(?:%s)$"', '"(?:%s)"'),
    ("malformed input tolerated", "approval_ok\n}", "true\n}"),
]


def policy_checks() -> None:
    check = opa_cli(POLICY_DIR, "check", "--strict")
    tests = opa_cli(POLICY_DIR, "test")
    passed = re.search(r"PASS: (\d+)/(\d+)", tests.stdout + tests.stderr)
    ev.check(
        "opa_check_strict_accepts_every_policy",
        check.returncode == 0,
        (check.stdout + check.stderr).strip()[-200:],
    )
    remediation_tests = opa_cli(POLICY_DIR, "test", "-v", "--run", "remediation_test")
    count = len(
        re.findall(
            r"^data\.aiops\.remediation_test\.test_\w+: PASS",
            remediation_tests.stdout,
            re.MULTILINE,
        )
    )
    ev.check(
        "opa_test_passes_every_policy_test_including_the_remediation_ones",
        tests.returncode == 0
        and bool(passed)
        and passed.group(1) == passed.group(2)
        and count >= 25,
        f"{passed.group(0) if passed else tests.stdout[-200:]}; {count} remediation tests",
    )
    rego = (POLICY_DIR / "aiops" / "remediation" / "remediation.rego").read_text(encoding="utf-8")
    survivors, applied = [], 0
    for name, old, new in MUTATIONS:
        if old not in rego:
            survivors.append(f"{name} (mutation target not found)")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "aiops" / "remediation"
            target.mkdir(parents=True)
            (target / "remediation.rego").write_text(rego.replace(old, new, 1), encoding="utf-8")
            shutil.copy(POLICY_DIR / "aiops" / "remediation" / "remediation_test.rego", target)
            result = opa_cli(Path(tmp), "test")
        applied += 1
        if result.returncode == 0:
            survivors.append(name)
    ev.check(
        "every_deliberate_breakage_of_a_remediation_rule_is_caught_by_the_policy_tests",
        not survivors and applied == len(MUTATIONS),
        f"{applied - len(survivors)} of {len(MUTATIONS)} caught; survivors {survivors}",
    )


# ------------------------------------------------------------------------------------- C. serving engine
def base_facts(**over) -> dict:
    facts = {
        "phase": "request",
        "actor": PLATFORM_ACTOR,
        "action": {"id": "restart_container", "target": "aiops-edge-runtime", "params": {}},
        "incident": {"status": "open"},
        "history": {
            "attempts_for_action": 0,
            "attempts_for_incident": 0,
            "attempts_on_target_in_window": 0,
            "seconds_since_last_attempt_on_target": None,
            "in_flight_total": 0,
        },
        "approval": None,
    }
    facts.update(over)
    return facts


def hist(**over) -> dict:
    return {**base_facts()["history"], **over}


def live_policy_checks() -> None:
    cases = [
        ("auto_target_is_approved", base_facts(), "approved", "permit"),
        (
            "stateful_target_needs_a_person",
            base_facts(
                action={"id": "restart_container", "target": "aiops-postgres", "params": {}}
            ),
            "needs_approval",
            "needs_approval",
        ),
        (
            "plan_only_is_never_approved",
            base_facts(action={"id": "scale_service", "target": "api", "params": {"replicas": 2}}),
            "plan_only",
            "plan_only",
        ),
        (
            "unregistered_action_is_denied",
            base_facts(action={"id": "wipe_disk", "target": "aiops-edge-runtime", "params": {}}),
            "denied",
            "unknown_action",
        ),
        (
            "unregistered_target_is_denied",
            base_facts(action={"id": "restart_container", "target": "aiops-grafana", "params": {}}),
            "denied",
            "target_not_registered",
        ),
        (
            "out_of_bounds_parameter_is_denied",
            base_facts(
                action={
                    "id": "set_trace_sampling",
                    "target": "otel-traces",
                    "params": {"ratio": 0.001, "ttl_s": 600},
                }
            ),
            "denied",
            "parameter_out_of_bounds",
        ),
        (
            "cooldown_is_enforced",
            base_facts(history=hist(seconds_since_last_attempt_on_target=10)),
            "denied",
            "cooldown",
        ),
        (
            "a_second_action_in_flight_is_refused",
            base_facts(history=hist(in_flight_total=1)),
            "denied",
            "busy",
        ),
        (
            "a_person_owning_the_incident_stops_it",
            base_facts(incident={"status": "acknowledged"}),
            "denied",
            "incident_held_by_a_person",
        ),
        (
            "another_identity_is_refused",
            base_facts(actor="system:command-executor"),
            "denied",
            "wrong_actor",
        ),
        ("malformed_input_is_denied", {"phase": "request"}, "denied", "malformed_input"),
    ]
    failures = []
    for name, facts, decision, reason in cases:
        got = pdp.remediation_decision(facts)
        if (got["decision"], got["reason"]) != (decision, reason):
            failures.append((name, got["decision"], got["reason"]))
    ev.check(
        "the_serving_policy_engine_answers_the_decision_matrix_correctly",
        not failures,
        f"{len(cases)} cases; wrong: {failures}",
    )
    served = pdp.remediation_decision(base_facts())
    ev.check(
        "every_decision_names_the_policy_version_that_made_it",
        served.get("policy_version") == pdp.loaded_version(),
        str(served.get("policy_version")),
    )

    saved_url, saved_client = pdp.OPA_URL, pdp._client  # noqa: SLF001
    pdp.OPA_URL, pdp._client = "http://127.0.0.1:1", None  # noqa: SLF001
    try:
        try:
            pdp.remediation_decision(base_facts())
            unavailable = False
        except pdp.PolicyUnavailable:
            unavailable = True
    finally:
        pdp.OPA_URL, pdp._client = saved_url, saved_client  # noqa: SLF001
    ev.check("an_unreachable_policy_engine_raises_policy_unavailable_never_a_permit", unavailable)


# ------------------------------------------------------------------------------------------------ D. structure
def new_incident(admin: psycopg.Connection, key: str) -> uuid.UUID:
    incident_id = uuid.uuid4()
    admin.execute(
        "INSERT INTO platform_incidents (platform_incident_id, incident_key, status, severity, title, components, opened_at, "
        "updated_at, hypothesis, signal_count) VALUES (%s, %s, 'open', 'high', 't', ARRAY[%s], now(), now(), "
        "'{\"verified\": false}', 1)",
        (incident_id, key, key),
    )
    return incident_id


def structure_checks() -> None:  # noqa: PLR0915
    with connect() as admin, connect("svc_remediation_worker") as svc:
        role = admin.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication FROM pg_roles WHERE rolname = 'svc_remediation_worker'"
        ).fetchone()
        ev.check(
            "the_worker_has_its_own_login_role_that_is_no_superuser_and_cannot_create_databases_or_roles",
            role is not None and not any(role),
            str(role),
        )
        ev.check(
            "the_history_is_wired_into_the_audit_chain_verifier_and_the_role_into_secret_rotation",
            "remediation_transitions" in audit_chain.CHAINED_TABLES
            and "svc_remediation_worker" in rotate_service_secrets.ROLES,
        )

        i1 = new_incident(admin, "structure-a")
        base = {
            "incident_id": i1,
            "target": "x",
            "autonomy": "auto",
            "params": {},
            "motivating": (),
            "decision": {"decision": "approved"},
            "now": at(0),
        }
        sample = ACTIONS["restart_container"]
        row = repo.create_request(
            svc, spec=sample, status="approved", attempt_no=1, key="k-1", **base
        )
        again = repo.create_request(
            svc, spec=sample, status="approved", attempt_no=1, key="k-1", **base
        )
        ev.check(
            "a_repeat_of_the_same_attempt_is_refused_by_its_idempotency_key_and_creates_nothing",
            row is not None and again is None,
        )

        try:
            repo.create_request(
                svc,
                spec=sample,
                status="approved",
                attempt_no=1,
                key="k-2",
                **{**base, "target": "y"},
            )
            busy = False
        except repo.Busy:
            busy = True
        ev.check(
            "the_database_refuses_a_second_approved_or_running_remediation_anywhere_on_the_platform",
            busy,
        )

        repo.transition(
            svc,
            row["remediation_id"],
            "executing",
            actor=PLATFORM_ACTOR,
            now=at(1),
            started_at=at(1),
        )
        repo.transition(svc, row["remediation_id"], "failed", actor=PLATFORM_ACTOR, now=at(2))
        try:
            repo.transition(
                svc,
                row["remediation_id"],
                "executing",
                actor=PLATFORM_ACTOR,
                now=at(3),
                started_at=at(3),
            )
            illegal = False
        except repo.InvalidTransition:
            illegal = True
        ev.check("a_finished_request_cannot_be_moved_again", illegal)

        plan = ACTIONS["scale_service"]
        ev.check(
            "a_plan_only_request_can_never_be_marked_executing_executed_or_verified_not_even_by_direct_sql",
            all(
                refused(
                    admin,
                    "INSERT INTO remediation_requests (remediation_id, platform_incident_id, action_id, kind, target, attempt_no, "
                    "idempotency_key, autonomy, status, requested_by, requested_at, policy_decision, started_at, executed_at, finished_at) "
                    "VALUES (gen_random_uuid(), %s, 'scale_service', 'scale', 'api', 1, %s, 'plan_only', %s, 'x', now(), '{}', now(), now(), now())",
                    (i1, f"plan-{status}", status),
                    errors.CheckViolation,
                )
                for status in ("executing", "executed", "verified", "approved")
            ),
        )
        _ = plan

        i2 = new_incident(admin, "structure-b")
        waiting = repo.create_request(
            svc, spec=ACTIONS["restart_container"], status="awaiting_approval", attempt_no=1, key="k-approval",
            **{**base, "incident_id": i2, "target": "aiops-postgres", "autonomy": "approval"},
        )  # fmt: skip
        ev.check(
            "the_worker_cannot_approve_its_own_request_neither_by_setting_the_approver_nor_by_setting_the_status",
            refused(
                svc,
                "UPDATE remediation_requests SET approved_by = 'system:aiops-remediation' WHERE remediation_id = %s",
                (waiting["remediation_id"],),
                errors.InsufficientPrivilege,
            )
            and refused(
                svc,
                "UPDATE remediation_requests SET status = 'approved' WHERE remediation_id = %s",
                (waiting["remediation_id"],),
                errors.CheckViolation,
            ),
        )
        ev.check(
            "the_worker_cannot_rewrite_what_was_asked_or_what_the_policy_decided",
            all(
                refused(
                    svc,
                    f"UPDATE remediation_requests SET {col} = {value} WHERE remediation_id = %s",
                    (waiting["remediation_id"],),
                    errors.InsufficientPrivilege,
                )
                for col, value in (
                    ("target", "'other'"),
                    ("action_id", "'scale_service'"),
                    ("policy_decision", "'{}'::jsonb"),
                    ("params", "'{}'::jsonb"),
                    ("autonomy", "'auto'"),
                )
            ),
        )
        ev.check(
            "the_worker_cannot_delete_or_truncate_requests_or_rewrite_or_wipe_the_history_and_reaches_no_traffic_or_audit_table",
            refused(svc, "DELETE FROM remediation_requests", (), errors.InsufficientPrivilege)
            and refused(svc, "TRUNCATE remediation_transitions", (), errors.InsufficientPrivilege)
            and refused(
                svc,
                "UPDATE remediation_transitions SET actor = 'x'",
                (),
                errors.InsufficientPrivilege,
            )
            and all(
                refused(svc, f"SELECT 1 FROM {t} LIMIT 1", (), errors.InsufficientPrivilege)
                for t in ("commands", "operator_audit", "incidents", "devices")
            ),
        )
        ev.check(
            "the_worker_may_escalate_an_incident_but_cannot_touch_its_verified_cause_or_hypothesis",
            not refused(
                svc,
                "UPDATE platform_incidents SET status = 'escalated' WHERE platform_incident_id = %s",
                (i2,),
            )
            and refused(
                svc,
                "UPDATE platform_incidents SET verified_cause = 'x' WHERE platform_incident_id = %s",
                (i2,),
                errors.InsufficientPrivilege,
            )
            and refused(
                svc,
                "UPDATE platform_incidents SET hypothesis = '{}'::jsonb WHERE platform_incident_id = %s",
                (i2,),
                errors.InsufficientPrivilege,
            ),
        )
        leaked = admin.execute(
            "SELECT grantee, table_name FROM information_schema.role_table_grants WHERE grantee IN ('svc_command_executor', 'svc_outcome_verifier', 'svc_scenario_control', 'svc_platform_correlator') AND table_name LIKE 'remediation%%'"
        ).fetchall()
        ev.check(
            "no_other_workload_role_has_any_access_to_the_remediation_tables",
            not leaked,
            str(leaked),
        )

        approved = repo.approve(
            admin, waiting["remediation_id"], "supervisor:bob", "supervisor", at(30)
        )
        try:
            repo.approve(admin, waiting["remediation_id"], "supervisor:carol", "supervisor", at(31))
            twice = False
        except repo.InvalidTransition:
            twice = True
        ev.check(
            "a_person_approves_a_waiting_request_once_and_the_approval_is_recorded",
            approved["status"] == "approved"
            and approved["approved_by"] == "supervisor:bob"
            and twice,
        )
        chain = audit_chain.verify_table(admin, "remediation_transitions", "id", True)
        ev.check(
            "the_remediation_history_is_an_intact_hash_chain",
            chain["intact"] and chain["rows"] >= 5,
            f"{chain['rows']} transitions",
        )
        ev.check(
            "even_the_database_owner_cannot_rewrite_a_recorded_transition",
            refused(admin, "UPDATE remediation_transitions SET actor = 'x'")
            and audit_chain.verify_table(admin, "remediation_transitions", "id", True)["intact"],
        )


# ---------------------------------------------------------------------------------- E. worker on synthetic incidents
class FakeProm:
    """Prometheus stand-in: which alerts were active when. `active[name] = [(from_s, to_s or None)]`, in seconds since T0."""

    def __init__(self, base: float) -> None:
        self.base, self.active, self.readable = base, {}, True

    def set(self, name: str, start: float, end: float | None = None) -> None:
        self.active.setdefault(name, []).append((start, end))

    def clear(self, name: str, at_s: float) -> None:
        self.active[name] = [(a, at_s if b is None else b) for a, b in self.active.get(name, [])]

    def fetch(self, start: datetime, end: datetime, *_a, **_k) -> list[dict]:
        if not self.readable:
            raise ConnectionError("prometheus is down")
        series = []
        for name, spans in self.active.items():
            values = []
            ts = (start.timestamp() // 15) * 15
            while ts <= end.timestamp():
                rel = ts - T0.timestamp()
                if any(a <= rel and (b is None or rel < b) for a, b in spans):
                    values.append([ts, "1"])
                ts += 15
            series.append({"metric": {"alertname": name, "alertstate": "firing"}, "values": values})
        return series


class Recorder:
    def __init__(self, ok: bool = True, crash: bool = False) -> None:
        self.ok, self.crash, self.calls, self.undone = ok, crash, [], []

    def plan(self, target: str, params: dict) -> dict:
        return {"do": f"(stand-in) {target}"}

    def execute(self, target: str, params: dict, timeout_s: float = 0) -> Outcome:
        self.calls.append((target, dict(params)))
        if self.crash:
            raise RuntimeError("adapter blew up")
        return Outcome(self.ok, {"stand_in": True} if self.ok else {"error": "stand-in failure"})

    def undo(self, target: str, detail: dict) -> Outcome:
        self.undone.append(target)
        return Outcome(True)


class CountingPlanOnly(PlanOnlyAdapter):
    executed = 0

    def execute(self, target: str, params: dict, timeout_s: float = 0) -> Outcome:
        type(self).executed += 1
        return super().execute(target, params, timeout_s)


def signal(name: str, base: float, active: bool = True, severity: str = "critical", **labels):
    return correlation.alert_signal(
        name, labels, severity, at(base), at(base), at(base + 5), 1, active
    )


def open_incident(corr: psycopg.Connection, signals: list, now: float) -> None:
    incidents.sync(corr, correlation.correlate(signals, at(now)), at(now))


def incident_of(admin: psycopg.Connection, key: str) -> dict:
    return [i for i in incidents.list_incidents(admin) if i["incident_key"] == key][-1]


def make_worker(svc, adapters: dict, prom: FakeProm, decide=None) -> Worker:
    return Worker(
        svc,
        adapters=adapters,
        decide=decide or pdp.remediation_decision,
        fetch_alerts=prom.fetch,
        log=lambda *_: None,
    )


def worker_checks() -> None:  # noqa: PLR0915
    with (
        connect() as admin,
        connect("svc_remediation_worker") as svc,
        connect("svc_platform_correlator") as corr,
    ):
        docker, model, sampling = Recorder(), Recorder(), Recorder()
        plan_only = CountingPlanOnly()
        adapters = {
            "docker": docker,
            "model_registry": model,
            "trace_sampling": sampling,
            "plan_only": plan_only,
        }

        # ---- 1. an auto action runs once; recovery is verified from the alerts, not from the adapter
        base = 0
        prom = FakeProm(base)
        prom.set("EdgeRuntimeDown", base)
        worker = make_worker(svc, adapters, prom)
        open_incident(corr, [signal("EdgeRuntimeDown", base)], base + 5)
        inc = incident_of(admin, "edge-runtime")
        worker.cycle(at(base + 10))
        worker.cycle(at(base + 10))  # the same moment again: idempotent
        worker.cycle(at(base + 25))
        first = repo.for_incident(admin, inc["platform_incident_id"])
        ev.check(
            "an_auto_action_runs_exactly_once_however_often_the_cycle_repeats",
            docker.calls == [("aiops-edge-runtime", {})]
            and len(first) == 1
            and first[0]["status"] == "executed",
            f"{len(docker.calls)} adapter call(s), status {first[0]['status']}",
        )
        prom.clear("EdgeRuntimeDown", base + 40)
        worker.cycle(at(base + 100))
        still = repo.get(admin, first[0]["remediation_id"])["status"]
        worker.cycle(at(base + 240))
        done = repo.get(admin, first[0]["remediation_id"])
        ev.check(
            "recovery_is_verified_only_after_the_alerts_have_cleared_and_stayed_clear_for_the_sustain_time",
            still == "executed"
            and done["status"] == "verified"
            and done["verification"]["verdict"] == "recovered"
            and done["verification"]["healthy_for_s"] >= 120,
            f"at +100 s {still}; at +240 s {done['status']} ({done['verification']['healthy_for_s']} s healthy)",
        )
        ev.check(
            "the_request_history_shows_approved_executing_executed_verified_in_order_with_the_policy_version",
            [t["to_status"] for t in repo.timeline(admin, first[0]["remediation_id"])]
            == ["approved", "executing", "executed", "verified"]
            and bool(first[0]["policy_decision"].get("policy_version")),
        )

        # ---- 2. the action runs but the alert never clears: retries within cooldown and budget, then ESCALATES
        base = 86_400
        docker.calls.clear()
        prom = FakeProm(base)
        prom.set("EdgeRuntimeDown", base)
        worker = make_worker(svc, adapters, prom)
        incidents.sync(corr, [], at(base - 2000))
        open_incident(corr, [signal("EdgeRuntimeDown", base)], base + 5)
        inc = incident_of(admin, "edge-runtime")
        worker.cycle(at(base + 10))  # attempt 1
        worker.cycle(at(base + 700))  # verification times out -> failed
        worker.cycle(at(base + 720))  # inside the cooldown: nothing
        inside = len(docker.calls)
        worker.cycle(at(base + 850))  # cooldown over: attempt 2
        worker.cycle(at(base + 1500))  # failed again
        worker.cycle(at(base + 1700))  # both attempts spent: escalate
        worker.cycle(at(base + 1800))  # escalated incidents are left to a person
        after = incident_of(admin, "edge-runtime")
        reqs = [
            r for r in repo.for_incident(admin, inc["platform_incident_id"]) if r["attempt_no"] > 0
        ]
        events = [
            e
            for e in incidents.timeline(admin, inc["platform_incident_id"])
            if e["event"] == "escalated"
        ]
        ev.check(
            "a_failed_attempt_waits_out_its_cooldown_then_retries_and_never_exceeds_its_attempt_limit",
            inside == 1
            and len(docker.calls) == 2
            and [r["status"] for r in reqs] == ["failed", "failed"],
            f"{len(docker.calls)} runs; statuses {[r['status'] for r in reqs]}",
        )
        ev.check(
            "when_every_registered_remediation_is_spent_and_the_incident_persists_it_is_escalated_to_a_person_not_left_silent",
            after["status"] == "escalated"
            and len(events) == 1
            and events[0]["actor"] == PLATFORM_ACTOR
            and bool(events[0]["detail"]["reason"]),
            f"status {after['status']}: {events[0]['detail']['reason'] if events else None}",
        )
        ev.check("an_escalated_incident_gets_no_further_automated_action", len(docker.calls) == 2)

        # ---- 3. a fallback playbook line: rollback first, then a restart once the rollback is spent
        base = 172_800
        docker.calls.clear()
        model.calls.clear()
        prom = FakeProm(base)
        prom.set("EdgeModelErrors", base)
        worker = make_worker(svc, adapters, prom)
        incidents.sync(corr, [], at(base - 2000))
        open_incident(
            corr,
            [signal("EdgeModelErrors", base, severity="warning", code="integrity_mismatch")],
            base + 5,
        )
        inc = incident_of(admin, "edge-runtime")
        worker.cycle(at(base + 10))  # rollback
        worker.cycle(at(base + 700))  # rollback failed to clear the alert
        worker.cycle(at(base + 710))  # the rollback is spent; the restart is next
        prom.clear("EdgeModelErrors", base + 730)
        worker.cycle(at(base + 900))
        worker.cycle(at(base + 1000))
        reqs = [
            (r["action_id"], r["status"])
            for r in repo.for_incident(admin, inc["platform_incident_id"])
            if r["attempt_no"] > 0
        ]
        ev.check(
            "the_playbook_falls_back_to_its_next_line_when_the_first_did_not_recover_the_platform",
            reqs == [("rollback_model", "failed"), ("restart_container", "verified")]
            and model.calls
            and docker.calls,
            str(reqs),
        )

        # ---- 4. an action needing approval waits for a person, then runs; the wrong role does not run it
        base = 259_200
        docker.calls.clear()
        prom = FakeProm(base)
        prom.set("PlatformTargetDown", base)
        worker = make_worker(svc, adapters, prom)
        open_incident(corr, [signal("PlatformTargetDown", base, service_name="postgres")], base + 5)
        inc = incident_of(admin, "postgres")
        worker.cycle(at(base + 10))
        worker.cycle(at(base + 40))
        waiting = repo.for_incident(admin, inc["platform_incident_id"])
        ev.check(
            "a_restart_of_a_stateful_service_waits_for_a_person_and_nothing_runs_meanwhile",
            len(waiting) == 1 and waiting[0]["status"] == "awaiting_approval" and not docker.calls,
        )
        repo.approve(
            admin, waiting[0]["remediation_id"], "supervisor:bob", "supervisor", at(base + 60)
        )
        worker.cycle(at(base + 70))
        ran = repo.get(admin, waiting[0]["remediation_id"])
        ev.check(
            "once_a_permitted_role_approves_it_runs_and_the_approver_is_on_the_record",
            docker.calls == [("aiops-postgres", {})]
            and ran["status"] == "executed"
            and ran["approved_by"] == "supervisor:bob",
        )
        prom.clear("PlatformTargetDown", base + 90)
        worker.cycle(at(base + 300))

        base = 345_600
        docker.calls.clear()
        prom = FakeProm(base)
        prom.set("PlatformTargetDown", base)
        worker = make_worker(svc, adapters, prom)
        incidents.sync(corr, [], at(base - 2000))
        open_incident(corr, [signal("PlatformTargetDown", base, service_name="postgres")], base + 5)
        inc = incident_of(admin, "postgres")
        worker.cycle(at(base + 10))
        (req,) = [
            r
            for r in repo.for_incident(admin, inc["platform_incident_id"])
            if r["status"] == "awaiting_approval"
        ]
        repo.approve(admin, req["remediation_id"], "dispatcher:dan", "dispatcher", at(base + 60))
        worker.cycle(at(base + 70))
        withdrawn = repo.get(admin, req["remediation_id"])
        ev.check(
            "an_approval_from_a_role_the_policy_does_not_accept_is_not_run",
            withdrawn["status"] == "expired"
            and not docker.calls
            and "no longer permits"
            in repo.timeline(admin, req["remediation_id"])[-1]["detail"]["reason"],
            withdrawn["status"],
        )

        base = 432_000
        prom = FakeProm(base)
        prom.set("PlatformTargetDown", base)
        worker = make_worker(svc, adapters, prom)
        incidents.sync(corr, [], at(base - 2000))
        open_incident(corr, [signal("PlatformTargetDown", base, service_name="postgres")], base + 5)
        inc = incident_of(admin, "postgres")
        worker.cycle(at(base + 10))
        worker.cycle(at(base + remediation.APPROVAL_TTL_S + 60))
        expired = [r["status"] for r in repo.for_incident(admin, inc["platform_incident_id"])]
        ev.check(
            "a_request_nobody_approves_in_time_expires_and_is_never_run",
            expired[0] == "expired" and not docker.calls,
            str(expired),
        )

        # ---- 5. a plan-only action is recorded, the incident escalated, and software never executes it
        base = 518_400
        prom = FakeProm(base)
        prom.set("ApiLatencyP95High", base)
        worker = make_worker(svc, adapters, prom)
        open_incident(corr, [signal("ApiLatencyP95High", base, severity="warning")], base + 5)
        inc = incident_of(admin, "api")
        worker.cycle(at(base + 10))
        worker.cycle(at(base + 30))
        (planned,) = repo.for_incident(admin, inc["platform_incident_id"])
        ev.check(
            "a_plan_only_action_is_recorded_with_its_plan_the_incident_is_escalated_and_no_adapter_runs",
            planned["status"] == "planned"
            and "plan" in planned["policy_decision"]
            and CountingPlanOnly.executed == 0
            and incident_of(admin, "api")["status"] == "escalated",
            planned["policy_decision"].get("plan", {}).get("do", ""),
        )

        # ---- 6. a person who acknowledges an incident stops the automation on it
        base = 604_800
        docker.calls.clear()
        prom = FakeProm(base)
        prom.set("EdgeRuntimeDown", base)
        worker = make_worker(svc, adapters, prom)
        incidents.sync(corr, [], at(base - 2000))
        open_incident(corr, [signal("EdgeRuntimeDown", base)], base + 5)
        inc = incident_of(admin, "edge-runtime")
        incidents.transition(
            admin,
            inc["platform_incident_id"],
            "acknowledged",
            "operator:alice",
            "on it",
            at(base + 6),
        )
        worker.cycle(at(base + 10))
        ev.check(
            "a_person_acknowledging_the_incident_stops_automation_on_it",
            not docker.calls and not repo.for_incident(admin, inc["platform_incident_id"]),
        )

        # ---- 7. one at a time across incidents
        base = 691_200
        docker.calls.clear()
        sampling.calls.clear()
        prom = FakeProm(base)
        prom.set("EdgeRuntimeDown", base)
        prom.set("IngestionLatencyP95High", base)
        worker = make_worker(svc, adapters, prom)
        incidents.sync(corr, [], at(base - 2000))
        open_incident(
            corr,
            [
                signal("EdgeRuntimeDown", base),
                signal("IngestionLatencyP95High", base + 1, severity="warning"),
            ],
            base + 5,
        )
        worker.cycle(at(base + 10))
        running = [t for t, _ in docker.calls] + [t for t, _ in sampling.calls]
        prom.clear("EdgeRuntimeDown", base + 20)
        prom.clear("IngestionLatencyP95High", base + 20)
        worker.cycle(at(base + 200))  # the first is verified, so the second may start
        worker.cycle(at(base + 400))  # and is judged in turn
        ev.check(
            "with_two_incidents_needing_action_only_one_remediation_runs_at_a_time",
            running == ["aiops-edge-runtime"]
            and [t for t, _ in sampling.calls] == ["otel-traces"]
            and len(docker.calls) == 1,
            f"first cycle ran {running}; the sampling change followed once the restart was judged",
        )

        # ---- 7b. an incident nothing is registered for is handed to a person, not left silent
        base = 734_400
        prom = FakeProm(base)
        prom.set("PlatformTargetDown", base)
        prom.set("CertificateExpiringSoon", base)
        worker = make_worker(svc, adapters, prom)
        incidents.sync(corr, [], at(base - 2000))
        open_incident(
            corr,
            [
                signal("PlatformTargetDown", base, service_name="opa"),
                signal(
                    "CertificateExpiringSoon", base, severity="warning", service_name="mqtt-broker"
                ),
            ],
            base + 5,
        )
        early = worker.cycle(at(base + 60))
        late = worker.cycle(at(base + remediation.UNREMEDIABLE_ESCALATION_S + 60))
        opa = incident_of(admin, "opa")
        cert = incident_of(admin, "mosquitto")
        ev.check(
            "a_serious_incident_with_no_registered_remediation_is_escalated_after_a_wait_and_a_minor_one_is_left_open",
            early["escalated"] == 0
            and late["escalated"] == 1
            and opa["status"] == "escalated"
            and cert["status"] == "open",
            f"opa {opa['status']}, certificate {cert['status']}",
        )

        # ---- 7a. every registered remediation spent quickly (the adapter itself fails): escalated because nothing is left to try
        base = 738_000
        failing = Recorder(ok=False)
        prom = FakeProm(base)
        prom.set("EdgeRuntimeDown", base)
        worker = make_worker(svc, {**adapters, "docker": failing}, prom)
        incidents.sync(corr, [], at(base - 2000))
        open_incident(corr, [signal("EdgeRuntimeDown", base)], base + 5)
        inc = incident_of(admin, "edge-runtime")
        worker.cycle(at(base + 10))  # attempt 1 fails at once
        worker.cycle(at(base + 140))  # cooldown over: attempt 2 fails at once
        worker.cycle(at(base + 200))  # both spent, the incident is only minutes old
        gone = incident_of(admin, "edge-runtime")
        reason = [
            e
            for e in incidents.timeline(admin, inc["platform_incident_id"])
            if e["event"] == "escalated"
        ]
        ev.check(
            "when_every_registered_remediation_is_spent_within_minutes_the_incident_is_escalated_as_exhausted",
            len(failing.calls) == 2 and gone["status"] == "escalated" and len(reason) == 1 and "exhausted" in reason[0]["detail"]["reason"],
            str(reason[0]["detail"]) if reason else "not escalated",
        )  # fmt: skip

        # ---- 7b'. recovery is judged on the whole component: a later alert about the same fault keeps it from being accepted
        base = 741_000
        docker.calls.clear()
        prom = FakeProm(base)
        prom.set("EdgeRuntimeDown", base)
        prom.set(
            "EdgeModelInactive", base + 40
        )  # needs longer to fire; still the same component, never clears
        worker = make_worker(svc, adapters, prom)
        incidents.sync(corr, [], at(base - 2000))
        open_incident(corr, [signal("EdgeRuntimeDown", base)], base + 5)
        inc = incident_of(admin, "edge-runtime")
        worker.cycle(at(base + 10))
        prom.clear("EdgeRuntimeDown", base + 20)
        worker.cycle(at(base + 300))  # EdgeRuntimeDown has been clear for a long time
        mid = repo.for_incident(admin, inc["platform_incident_id"])[0]["status"]
        worker.cycle(at(base + 700))
        end = repo.for_incident(admin, inc["platform_incident_id"])[0]
        ev.check(
            "a_restart_is_not_declared_recovered_while_another_alert_about_the_same_component_is_still_active",
            mid == "executed" and end["status"] == "failed" and "EdgeModelInactive" in end["verification"]["alerts"],
            f"{mid} -> {end['status']}",
        )  # fmt: skip

        # ---- 7c. a target held back by its rate limit does not leave the incident silent: it is escalated after a while
        base = 777_000
        docker.calls.clear()
        prom = FakeProm(base)
        prom.set("EdgeRuntimeDown", base)
        worker = make_worker(svc, adapters, prom)
        incidents.sync(corr, [], at(base - 2000))
        earlier = new_incident(admin, "rate-history")
        for k in range(remediation.ACTIONS["restart_container"].max_per_window):
            admin.execute(
                "INSERT INTO remediation_requests (remediation_id, platform_incident_id, action_id, kind, target, attempt_no, "
                "idempotency_key, autonomy, status, requested_by, requested_at, policy_decision, started_at, executed_at, finished_at) "
                "VALUES (gen_random_uuid(), %s, 'restart_container', 'restart', 'aiops-edge-runtime', 1, %s, 'auto', 'verified', "
                "'system:aiops-remediation', %s, '{}', %s, %s, %s)",
                (
                    earlier,
                    f"rate-{k}",
                    at(base - 3000 + k * 200),
                    at(base - 3000 + k * 200),
                    at(base - 2990 + k * 200),
                    at(base - 2900 + k * 200),
                ),
            )
        open_incident(corr, [signal("EdgeRuntimeDown", base)], base + 5)
        inc = incident_of(admin, "edge-runtime")
        worker.cycle(at(base + 10))
        held = repo.for_incident(admin, inc["platform_incident_id"])
        still_open = incident_of(admin, "edge-runtime")["status"]
        worker.cycle(at(base + remediation.ESCALATE_AFTER_S + 60))
        after = incident_of(admin, "edge-runtime")
        ev.check(
            "an_action_held_back_by_its_rate_limit_runs_nothing_and_the_incident_is_escalated_not_left_silent",
            [r["policy_decision"]["reason"] for r in held] == ["rate_limited"]
            and not docker.calls
            and still_open == "open"
            and after["status"] == "escalated",
            f"{[r['policy_decision']['reason'] for r in held]}; {still_open} -> {after['status']}",
        )

        # ---- 8. failures of the machinery itself never become "recovered"
        base = 820_000
        docker.calls.clear()
        crashing = Recorder(crash=True)
        prom = FakeProm(base)
        prom.set("EdgeRuntimeDown", base)
        incidents.sync(corr, [], at(base - 2000))
        open_incident(corr, [signal("EdgeRuntimeDown", base)], base + 5)
        inc = incident_of(admin, "edge-runtime")
        make_worker(svc, {**adapters, "docker": crashing}, prom).cycle(at(base + 10))
        crashed = repo.for_incident(admin, inc["platform_incident_id"])[-1]
        ev.check(
            "an_adapter_that_crashes_is_a_failed_attempt_with_the_error_recorded_not_a_dead_worker",
            crashed["status"] == "failed" and "blew up" in str(crashed["result"]),
            str(crashed["result"]),
        )

        base = 864_000
        prom = FakeProm(base)
        prom.set("IngestionLatencyP95High", base)
        sampling.calls.clear()
        sampling.undone.clear()
        worker = make_worker(svc, adapters, prom)
        incidents.sync(corr, [], at(base - 2000))
        open_incident(corr, [signal("IngestionLatencyP95High", base, severity="warning")], base + 5)
        worker.cycle(at(base + 10))
        worker.cycle(at(base + 1000))
        ev.check(
            "a_bounded_action_that_did_not_help_is_undone_and_its_parameters_were_the_registered_ones",
            sampling.calls == [("otel-traces", {"ratio": 0.1, "ttl_s": 600})]
            and sampling.undone == ["otel-traces"],
            str(sampling.calls),
        )

        base = 950_400
        docker.calls.clear()
        prom = FakeProm(base)
        prom.set("EdgeRuntimeDown", base)
        worker = make_worker(svc, adapters, prom)
        incidents.sync(corr, [], at(base - 2000))
        open_incident(corr, [signal("EdgeRuntimeDown", base)], base + 5)
        inc = incident_of(admin, "edge-runtime")
        worker.cycle(at(base + 10))
        prom.clear("EdgeRuntimeDown", base + 20)
        prom.readable = False
        worker.cycle(at(base + 300))

        def attempts() -> list[dict]:
            return [
                r
                for r in repo.for_incident(admin, inc["platform_incident_id"])
                if r["attempt_no"] > 0
            ]

        unreadable_mid = attempts()[-1]["status"]
        worker.cycle(at(base + 700))
        final = attempts()[-1]
        ev.check(
            "when_prometheus_cannot_be_read_recovery_is_never_assumed_and_the_attempt_fails_at_the_timeout",
            unreadable_mid == "executed"
            and final["status"] == "failed"
            and "never confirmed" in final["verification"]["reason"],
            f"{unreadable_mid} -> {final['status']}",
        )

        # ---- 9. a worker that died mid-action
        base = 1_036_800
        incidents.sync(corr, [], at(base - 2000))
        open_incident(corr, [signal("EdgeRuntimeDown", base)], base + 5)
        inc = incident_of(admin, "edge-runtime")
        prom = FakeProm(base)
        prom.set("EdgeRuntimeDown", base)
        ghost = repo.create_request(
            svc, incident_id=inc["platform_incident_id"], spec=ACTIONS["restart_container"], target="aiops-edge-runtime",
            autonomy="auto", params={}, status="approved", attempt_no=1, key=f"ghost-{uuid.uuid4()}", motivating=("EdgeRuntimeDown",),
            decision={"decision": "approved"}, now=at(base + 6),
        )  # fmt: skip
        repo.transition(
            svc,
            ghost["remediation_id"],
            "executing",
            actor=PLATFORM_ACTOR,
            now=at(base + 7),
            started_at=at(base + 7),
        )
        docker.calls.clear()
        make_worker(svc, adapters, prom).cycle(at(base + 900))
        closed = repo.get(admin, ghost["remediation_id"])
        facts = repo.history_facts(
            svc,
            inc["platform_incident_id"],
            "restart_container",
            "aiops-edge-runtime",
            at(base + 900),
            3600,
        )
        ev.check(
            "a_request_left_executing_by_a_dead_worker_is_closed_as_abandoned_and_counts_as_an_attempt",
            closed["status"] == "abandoned"
            and facts["attempts_for_action"] >= 1
            and "unknown" in repo.timeline(admin, ghost["remediation_id"])[-1]["detail"]["reason"],
            closed["status"],
        )

        # ---- 10. the policy engine is down: the cycle does nothing
        base = 1_123_200
        docker.calls.clear()
        prom = FakeProm(base)
        prom.set("EdgeRuntimeDown", base)
        incidents.sync(corr, [], at(base - 2000))
        open_incident(corr, [signal("EdgeRuntimeDown", base)], base + 5)
        inc = incident_of(admin, "edge-runtime")

        def down(_facts: dict) -> dict:
            raise pdp.PolicyUnavailable("engine down")

        counts = make_worker(svc, adapters, prom, decide=down).cycle(at(base + 10))
        ev.check(
            "with_the_policy_engine_down_the_worker_does_nothing_and_says_so",
            counts["policy_unavailable"] == 1
            and not docker.calls
            and not repo.for_incident(admin, inc["platform_incident_id"]),
            str(dict(counts)),
        )
        chain = audit_chain.verify_table(admin, "remediation_transitions", "id", True)
        ev.metrics["worker_scenarios"] = {
            "kind": "synthetic incidents created through the real correlator; real policy engine and repository; stand-in adapters and Prometheus",
            "transitions_recorded": chain["rows"],
            "history_intact": chain["intact"],
        }


# ------------------------------------------------------------------------------------------- F. adapters, for real
def stage_into(root: Path, real: Path, version: str, break_it: bool = False) -> None:
    """A real, working model package copied under another version label (the same trick the activation tests use)."""
    import json

    dest = root / version
    shutil.copytree(real, dest)
    manifest = json.loads((dest / "artifact_manifest.json").read_text(encoding="utf-8"))
    manifest["identity"]["model_version"] = version
    (dest / "artifact_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if break_it:
        data = bytearray((dest / "model.onnx").read_bytes())
        data[5] ^= 0xFF
        (dest / "model.onnx").write_bytes(bytes(data))


def adapter_checks() -> None:
    docker = remediation_adapters.DockerAdapter()
    refused_outcome = docker.execute("definitely-not-registered", {})
    ev.check(
        "the_docker_adapter_refuses_a_container_the_registry_does_not_list",
        not refused_outcome.ok and "not registered" in refused_outcome.detail["error"],
    )

    from backend.aiops import verify_alerts

    with tempfile.TemporaryDirectory() as tmp:
        edge = verify_alerts.EdgeLab(Path(tmp))
        try:
            edge.ensure_image()
            edge.start(edge.registry_ok)
            time.sleep(5)
            before = docker._state("aiops-edge-runtime")  # noqa: SLF001
            outcome = docker.execute("aiops-edge-runtime", {}, timeout_s=90)
        finally:
            edge.stop()
    ev.check(
        "the_docker_adapter_really_restarts_the_real_edge_runtime_container_and_waits_for_it_to_run_again",
        bool(before)
        and outcome.ok
        and outcome.detail["after"]["started_at"] != before["started_at"]
        and outcome.detail["after"]["status"] == "running",
        f"{outcome.detail.get('seconds')} s; started_at {before and before['started_at']} -> {outcome.detail.get('after', {}).get('started_at')}",
    )

    from edge.activation import ModelActivator
    from edge.features import FEATURE_VERSION

    real = SOURCE_ROOT / "models" / "registry" / "traffic-safety-blockage" / "1.0.0"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "traffic-safety-blockage"
        root.mkdir()

        def stage(version: str, break_it: bool = False) -> None:
            stage_into(root, real, version, break_it)

        stage("1.0.0")
        stage("1.0.1")
        activator = ModelActivator(root, FEATURE_VERSION)
        activator.activate("1.0.0", "seed")
        activator.activate("1.0.1", "release")
        restarted = []

        class FakeRestart:
            def execute(self, target: str, params: dict, timeout_s: float = 0) -> Outcome:
                restarted.append(target)
                return Outcome(True, {"stand_in": True})

        adapter = remediation_adapters.ModelRegistryAdapter(Path(tmp), FakeRestart())
        result = adapter.execute("edge-runtime:traffic-safety-blockage", {})
        ev.check(
            "the_model_registry_adapter_rolls_the_real_registry_format_back_one_verified_version_and_restarts_the_runtime",
            result.ok
            and activator.active_version() == "1.0.0"
            and restarted == ["aiops-edge-runtime"]
            and result.detail["from"] == "1.0.1"
            and result.detail["to"] == "1.0.0",
            str(result.detail),
        )
        again = adapter.execute("edge-runtime:traffic-safety-blockage", {})
        ev.check(
            "a_second_rollback_that_would_only_return_to_the_version_just_left_is_refused",
            not again.ok and activator.active_version() == "1.0.0" and len(restarted) == 1,
            str(again.detail),
        )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "traffic-safety-blockage"
        root.mkdir()
        stage_into(root, real, "1.0.0")
        stage_into(root, real, "1.0.1")
        activator = ModelActivator(root, FEATURE_VERSION)
        activator.activate("1.0.0", "seed")
        activator.activate("1.0.1", "release")
        weights = (
            root / "1.0.0" / "model.onnx"
        )  # the version a rollback would return to rots on disk
        damaged = bytearray(weights.read_bytes())
        damaged[5] ^= 0xFF
        weights.write_bytes(bytes(damaged))
        broken = remediation_adapters.ModelRegistryAdapter(Path(tmp), FakeRestart()).execute(
            "edge-runtime:traffic-safety-blockage", {}
        )
        ev.check(
            "a_previous_version_that_no_longer_verifies_is_never_rolled_back_to_and_nothing_changes",
            not broken.ok and activator.active_version() == "1.0.1" and len(restarted) == 1,
            str(broken.detail),
        )

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "s.json"
        sampler = remediation_adapters.TraceSamplingAdapter(path)
        ok = sampler.execute("otel-traces", {"ratio": 0.1, "ttl_s": 600})
        from backend import trace_sampling

        in_force = trace_sampling.read_override(path)
        sampler.undo("otel-traces", {})
        ev.check(
            "the_sampling_adapter_writes_a_bounded_override_that_the_sampler_reads_and_undo_removes_it",
            ok.ok
            and in_force == 0.1
            and trace_sampling.read_override(path) == 1.0
            and not path.exists(),
        )


def main() -> int:
    world.load_platform_env()
    started = time.time()
    try:
        registry_checks()
        policy_checks()
        live_policy_checks()
        fresh_database()
        structure_checks()
        worker_checks()
        adapter_checks()
    finally:
        os.environ["POSTGRES_DB"] = "aiops"
        try:
            world.use_database(DB)
            world.drop_database(DB)
        except psycopg.Error as exc:
            print(f"cleanup {DB}: {exc}", flush=True)
        os.environ["POSTGRES_DB"] = "aiops"
    ev.metrics["seconds"] = round(time.time() - started)
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
