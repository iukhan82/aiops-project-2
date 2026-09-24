"""P05.09 / P08.09: the separate, authorized scenario-control API.

Deliberately its own FastAPI app (not a router mounted on the operational API): a demo/test control surface stays visibly and
structurally separate from the real device, traffic, incident and command APIs - its own process, its own routes, its own audit trail
(`scenario_control_audit`) and its own identity, the `demo_operator` role, which holds no operational capability at all. The reverse
holds too: none of the operating roles holds `demo.control`.

Wraps P03.07's already-proven ordered/idempotent/duplicate-safe `simulator/manifest/replay.py` against the real manifest event
streams - "replay" here re-runs the *same* mechanism, not a reimplementation. It reads the recorded streams and reports what the
replay accepted; it does not write to the operational tables, so it cannot change the live map or any incident, command or outcome.

Authentication is the operator API's: a Keycloak access token (`Authorization: Bearer`) validated strictly by
`backend/api/auth.py` (RS256/JWKS, issuer, audience, expiry, authorised party, operating role), then the policy engine decides whether the
principal holds the capability the UX inventory gives each endpoint (`demo.control`; P09.03, fail closed with 503 when the engine cannot answer). The P05.09 placeholder that trusted an `X-Demo-Role` header the caller wrote is gone: the header
is ignored. A request without a valid identity, or from a role without `demo.control`, is refused and the refusal is audited.
There is deliberately no "authentication off" mode here.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.api.auth import (  # noqa: E402
    Denied,
    Principal,
    authorize,
    principal_from_claims,
    refuse_unidentified,
    verify_token,
)
from backend.api.authz import policy_for  # noqa: E402
from backend.api.hardening import (  # noqa: E402
    BodyLimit,
    SecurityHeaders,
    client_address,
)
from backend.observability import HTTPTracingMiddleware, configure  # noqa: E402
from backend.redaction import redact_text  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "manifest_replay", SOURCE_ROOT / "simulator" / "manifest" / "replay.py"
)
manifest_replay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(manifest_replay)

MANIFEST_PATH = SOURCE_ROOT / "simulator" / "manifest" / "output" / "manifest.json"
MAX_CONCURRENT_RUNS = 2
SERVICE = "scenario-control"
# What each endpoint is, by its route template, for the audit trail.
ACTION_OF = {
    ("POST", "/scenario-control/v1/runs"): "start",
    ("GET", "/scenario-control/v1/runs"): "list",
    ("GET", "/scenario-control/v1/runs/{run_id}"): "status",
    ("POST", "/scenario-control/v1/runs/{run_id}/reset"): "reset",
    ("POST", "/scenario-control/v1/runs/{run_id}/replay"): "replay",
    ("GET", "/scenario-control/v1/audit"): "audit",
}


def get_conn() -> psycopg.Connection:
    return psycopg.connect(dsn_from_env(role="svc_scenario_control"))


def problem(status: int, code: str, message: str, **extra: object) -> NoReturn:
    raise HTTPException(status, detail={"error": code, "message": message, **extra})


def _uuid_or_none(value: object) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError):
        return None


def audit(
    conn: psycopg.Connection,
    run_id: str | None,
    action: str,
    principal: Principal | None,
    outcome: str,
    detail: str = "",
) -> None:
    actor, role = (
        (principal.username, ",".join(principal.roles)) if principal else ("<anonymous>", "<none>")
    )
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO scenario_control_audit (run_id, action, actor, role, outcome, detail) VALUES (%s, %s, %s, %s, %s, %s)",
            (run_id, action, actor, role, outcome, redact_text(detail)),
        )
    conn.commit()


def _record_refusal(
    action: str, run_id: str | None, principal: Principal | None, outcome: str, detail: str
) -> None:
    """Best effort: a database that cannot be reached must not turn a refusal into a different error."""
    try:
        with psycopg.connect(dsn_from_env(role="svc_scenario_control"), connect_timeout=2) as conn:
            audit(conn, run_id, action, principal, outcome, detail)
    except Exception:  # noqa: BLE001, S110
        pass


def _bearer(conn: HTTPConnection) -> str | None:
    header = conn.headers.get("authorization", "")
    return header[7:].strip() or None if header.lower().startswith("bearer ") else None


async def authenticate(conn: HTTPConnection) -> None:
    """Global dependency: a verified identity holding `demo.control`, or a refusal that is audited. Default deny."""
    route = conn.scope.get("route")
    path = getattr(route, "path", conn.scope["path"])
    method = conn.scope["method"]
    action = ACTION_OF.get((method, path), "status")
    run_id = _uuid_or_none(conn.scope.get("path_params", {}).get("run_id"))
    policy = policy_for(method, path, SERVICE)
    if policy is None:
        problem(
            403,
            "unlisted_endpoint",
            "this endpoint is not in the access inventory, so it is denied by default",
        )
    if policy.public:
        return
    principal: Principal | None = None
    address = client_address(conn.scope.get("client"))
    try:
        token = _bearer(conn)
        if token is None:
            raise Denied(
                401,
                "unauthenticated",
                "a Bearer access token is required; the X-Demo-Role header is not an identity",
            )
        principal = principal_from_claims(await run_in_threadpool(verify_token, token))
        await authorize(principal, SERVICE, method, path)
    except Denied as denied:
        if denied.status == 401:
            denied = refuse_unidentified(address, denied)
        outcome = {403: "denied_role", 503: "denied_policy_unavailable"}.get(
            denied.status, "denied_identity"
        )
        if (
            denied.status != 429
        ):  # a throttled request is not audited: a flood must not become a flood of audit rows
            await run_in_threadpool(
                _record_refusal,
                action,
                run_id,
                principal,
                outcome,
                f"{denied.code}: {denied.message}"[:300],
            )
        headers = (
            {"WWW-Authenticate": f'Bearer error="{denied.code}"'} if denied.status == 401 else None
        )
        if denied.status == 429:
            headers = {"Retry-After": str(denied.extra.get("retry_after_s", 1))}
        raise HTTPException(
            denied.status,
            detail={"error": denied.code, "message": denied.message, **denied.extra},
            headers=headers,
        ) from denied
    conn.state.principal = principal


app = FastAPI(
    title="AIOPS Scenario Control API",
    version="2.0.0",
    dependencies=[Depends(authenticate)],
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
configure("scenario-control")
app.add_middleware(BodyLimit)
app.add_middleware(SecurityHeaders)
app.add_middleware(HTTPTracingMiddleware, service_name="scenario-control")

RUN_COLUMNS = "run_id, scenario, status, started_by, started_at, completed_at, result"


def run_scenario() -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    events = manifest_replay.load_event_stream_files(manifest)
    return manifest_replay.replay(events)


def _run_record(row: tuple) -> dict:
    out = dict(zip(RUN_COLUMNS.split(", "), row, strict=True))
    out["run_id"] = str(out["run_id"])
    out["started_at"] = out["started_at"].isoformat()
    out["completed_at"] = out["completed_at"].isoformat() if out["completed_at"] else None
    return out


def _get_run(run_id: str) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {RUN_COLUMNS} FROM scenario_runs WHERE run_id = %s", (run_id,))
        row = cur.fetchone()
    return _run_record(row) if row else None


@app.get("/scenario-control/v1/health")
def health() -> dict:
    return {"status": "ok", "service": SERVICE, "time": datetime.now(timezone.utc).isoformat()}


@app.post("/scenario-control/v1/runs")
def start_run(request: Request) -> dict:
    principal: Principal = request.state.principal
    with get_conn() as conn:
        with conn.cursor() as cur:
            # A COUNT(*) query's FOR UPDATE only locks rows that already exist; it cannot stop a *new* concurrent INSERT from being the
            # (MAX_CONCURRENT_RUNS+1)th running row (the classic phantom-read gap). An advisory lock serializes the whole
            # check-then-insert sequence across concurrent start requests instead.
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('scenario_control_start'))")
            cur.execute("SELECT count(*) FROM scenario_runs WHERE status = 'running'")
            (running_count,) = cur.fetchone()
            if running_count >= MAX_CONCURRENT_RUNS:
                audit(
                    conn,
                    None,
                    "start",
                    principal,
                    "denied_bound",
                    f"{running_count} runs already in progress",
                )
                problem(
                    429,
                    "run_bound_reached",
                    f"At most {MAX_CONCURRENT_RUNS} scenario runs can be in progress at once; {running_count} are.",
                    running=running_count,
                    max_concurrent=MAX_CONCURRENT_RUNS,
                )

            run_id = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO scenario_runs (run_id, scenario, status, started_by, started_at) VALUES (%s, 'manifest', 'running', %s, %s)",
                (run_id, principal.username, datetime.now(timezone.utc)),
            )
        conn.commit()
        audit(conn, run_id, "start", principal, "allowed")

        try:
            result = run_scenario()
            status = "completed"
        except Exception as exc:  # noqa: BLE001
            result, status = {"error": str(exc)}, "failed"
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE scenario_runs SET status = %s, completed_at = %s, result = %s WHERE run_id = %s",
                (status, datetime.now(timezone.utc), json.dumps(result), run_id),
            )
        conn.commit()
        if status == "failed":
            problem(
                500,
                "run_failed",
                "The scenario run failed while replaying the recorded streams.",
                run_id=run_id,
                reason=result["error"],
            )
    return _get_run(run_id)


@app.get("/scenario-control/v1/runs")
def list_runs(request: Request, limit: int = Query(default=20, ge=1, le=50)) -> dict:
    principal: Principal = request.state.principal
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {RUN_COLUMNS} FROM scenario_runs ORDER BY started_at DESC LIMIT %s",
                (limit,),
            )
            items = [_run_record(row) for row in cur.fetchall()]
            cur.execute("SELECT count(*) FROM scenario_runs WHERE status = 'running'")
            (running,) = cur.fetchone()
        audit(conn, None, "list", principal, "allowed")
    return {"items": items, "bound": {"max_concurrent": MAX_CONCURRENT_RUNS, "running": running}}


@app.get("/scenario-control/v1/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict:
    principal: Principal = request.state.principal
    known = _uuid_or_none(run_id)
    run = _get_run(known) if known else None
    with get_conn() as conn:
        audit(conn, known, "status", principal, "allowed" if run else "not_found")
    if run is None:
        problem(404, "unknown_run", f"There is no run {run_id!r}.")
    return run


@app.post("/scenario-control/v1/runs/{run_id}/reset")
def reset_run(run_id: str, request: Request) -> dict:
    principal: Principal = request.state.principal
    known = _uuid_or_none(run_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM scenario_runs WHERE run_id = %s FOR UPDATE", (known,))
            row = cur.fetchone() if known else None
            if row is None:
                audit(conn, known, "reset", principal, "not_found")
                problem(404, "unknown_run", f"There is no run {run_id!r}.")
            cur.execute(
                "UPDATE scenario_runs SET status = 'reset', completed_at = %s WHERE run_id = %s",
                (datetime.now(timezone.utc), known),
            )
        conn.commit()
        audit(conn, known, "reset", principal, "allowed")
    return _get_run(known)


@app.post("/scenario-control/v1/runs/{run_id}/replay")
def replay_run(run_id: str, request: Request) -> dict:
    principal: Principal = request.state.principal
    known = _uuid_or_none(run_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM scenario_runs WHERE run_id = %s FOR UPDATE", (known,))
            row = cur.fetchone() if known else None
            if row is None:
                audit(conn, known, "replay", principal, "not_found")
                problem(404, "unknown_run", f"There is no run {run_id!r}.")
        result = run_scenario()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE scenario_runs SET status = 'completed', completed_at = %s, result = %s WHERE run_id = %s",
                (datetime.now(timezone.utc), json.dumps(result), known),
            )
        conn.commit()
        audit(conn, known, "replay", principal, "allowed")
    return _get_run(known)


@app.get("/scenario-control/v1/audit")
def get_audit(request: Request, limit: int = Query(default=50, ge=1, le=200)) -> dict:
    """This service's own trail, newest first. It is not the operator audit trail and holds nothing from it."""
    principal: Principal = request.state.principal
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, at, actor, role, action, outcome, run_id, detail FROM scenario_control_audit ORDER BY at DESC, id DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
        audit(conn, None, "audit", principal, "allowed")
    keys = ("audit_id", "at", "actor", "role", "action", "outcome", "run_id", "detail")
    items = []
    for row in rows:
        item = dict(zip(keys, row, strict=True))
        item["at"] = item["at"].isoformat()
        item["run_id"] = str(item["run_id"]) if item["run_id"] else None
        items.append(item)
    return {"items": items}
