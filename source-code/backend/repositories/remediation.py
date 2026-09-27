"""P10.08: remediation request repository - explicit state machine, row locks, one hash-chained history.

    denied ............ terminal: the policy said no (recorded once per reason, so a worker that asks again every cycle does not
                        flood the table)
    planned ........... terminal: a plan-only action; software never executes it
    awaiting_approval . a person must approve; expires after `APPROVAL_TTL_S`
    approved .......... allowed to run (by policy, or by a person)
    executing ......... the adapter is running
    executed .......... the adapter finished; recovery not yet judged
    verified .......... independently confirmed: the alerts that motivated it cleared and stayed clear
    failed ............ the adapter failed, or recovery did not follow within the verification timeout
    abandoned ......... the worker died mid-action: the outcome is unknown and counts as an attempt
    expired ........... nobody approved it in time

Only `executing`, `executed`, `verified`, `failed` and `abandoned` count as attempts. At most one request is
`approved`/`executing`/`executed` on the whole platform - the database refuses a second (`remediation_one_in_flight`).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.aiops.remediation import APPROVAL_TTL_S, PLATFORM_ACTOR, ActionSpec

COUNTED = ("executing", "executed", "verified", "failed", "abandoned")
IN_FLIGHT = ("approved", "executing", "executed")
TERMINAL = ("denied", "planned", "verified", "failed", "abandoned", "expired")

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "awaiting_approval": {"approved", "expired"},
    "approved": {"executing", "abandoned", "expired"},
    "executing": {"executed", "failed", "abandoned"},
    "executed": {"verified", "failed"},
}
_WRITABLE = {"started_at", "executed_at", "finished_at", "result", "verification"}


class RemediationError(Exception):
    pass


class InvalidTransition(RemediationError):
    pass


class Busy(RemediationError):
    """Another remediation is already approved or running; the database allows one at a time."""


def _event(
    cur: psycopg.Cursor,
    remediation_id: uuid.UUID,
    at: datetime,
    from_status: str | None,
    to_status: str,
    actor: str,
    detail: dict,
) -> None:
    cur.execute(
        "INSERT INTO remediation_transitions (remediation_id, at, from_status, to_status, actor, detail) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (remediation_id, at, from_status, to_status, actor, Jsonb(detail)),
    )


def history_facts(
    conn: psycopg.Connection,
    incident_id: uuid.UUID,
    action_id: str,
    target: str,
    now: datetime,
    window_s: int,
) -> dict:
    """The counts the policy decides over, read from the database - never supplied by the caller."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
              count(*) FILTER (WHERE platform_incident_id = %(i)s AND action_id = %(a)s AND target = %(t)s
                               AND status = ANY(%(counted)s)) AS attempts_for_action,
              count(*) FILTER (WHERE platform_incident_id = %(i)s AND action_id = %(a)s AND target = %(t)s
                               AND attempt_no > 0) AS requests_for_action,
              count(*) FILTER (WHERE platform_incident_id = %(i)s AND status = ANY(%(counted)s)) AS attempts_for_incident,
              count(*) FILTER (WHERE target = %(t)s AND status = ANY(%(counted)s)
                               AND requested_at >= %(since)s) AS attempts_on_target_in_window,
              max(coalesce(finished_at, executed_at, started_at, requested_at))
                  FILTER (WHERE target = %(t)s AND status = ANY(%(counted)s)) AS last_attempt_at,
              count(*) FILTER (WHERE status = ANY(%(inflight)s)) AS in_flight_total
            FROM remediation_requests
            """,
            {
                "i": incident_id,
                "a": action_id,
                "t": target,
                "counted": list(COUNTED),
                "inflight": list(IN_FLIGHT),
                "since": now - timedelta(seconds=window_s),
            },
        )
        row = cur.fetchone()
    last = row["last_attempt_at"]
    return {
        "attempts_for_action": row["attempts_for_action"],
        "requests_for_action": row["requests_for_action"],
        "attempts_for_incident": row["attempts_for_incident"],
        "attempts_on_target_in_window": row["attempts_on_target_in_window"],
        "seconds_since_last_attempt_on_target": None
        if last is None
        else max(0.0, (now - last).total_seconds()),
        "in_flight_total": row["in_flight_total"],
    }


def create_request(
    conn: psycopg.Connection,
    *,
    incident_id: uuid.UUID,
    spec: ActionSpec,
    target: str,
    autonomy: str,
    params: dict,
    status: str,
    attempt_no: int,
    key: str,
    motivating: tuple[str, ...],
    decision: dict,
    now: datetime,
    actor: str = PLATFORM_ACTOR,
) -> dict | None:
    """Insert one request. Returns None when `key` already exists (an idempotent repeat). Raises `Busy` when another
    remediation is already in flight."""
    terminal = status in ("denied", "planned")
    request_id = uuid.uuid4()
    try:
        with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "INSERT INTO remediation_requests (remediation_id, platform_incident_id, action_id, kind, target, params, "
                "attempt_no, idempotency_key, autonomy, status, motivating_signals, requested_by, requested_at, "
                "policy_decision, finished_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (idempotency_key) DO NOTHING RETURNING *",
                (
                    request_id,
                    incident_id,
                    spec.id,
                    spec.kind,
                    target,
                    Jsonb(params),
                    attempt_no,
                    key,
                    autonomy,
                    status,
                    list(motivating),
                    actor,
                    now,
                    Jsonb(decision),
                    now if terminal else None,
                ),
            )
            row = cur.fetchone()
            if row is None:
                return None
            _event(
                cur,
                request_id,
                now,
                None,
                status,
                actor,
                {"decision": decision.get("decision"), "reason": decision.get("reason")},
            )
            return row
    except psycopg.errors.UniqueViolation as exc:
        if "remediation_one_in_flight" in str(exc):
            raise Busy("another remediation is already approved or running") from exc
        raise


def transition(
    conn: psycopg.Connection,
    remediation_id: uuid.UUID,
    to_status: str,
    *,
    actor: str,
    now: datetime,
    detail: dict | None = None,
    **fields,
) -> dict:
    """Move a request along `ALLOWED_TRANSITIONS`, under a row lock, recording the move in the chained history."""
    unknown = set(fields) - _WRITABLE
    if unknown:
        raise RemediationError(f"columns not writable through a transition: {sorted(unknown)}")
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT status FROM remediation_requests WHERE remediation_id = %s FOR UPDATE",
            (remediation_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise RemediationError("no such remediation request")
        if to_status not in ALLOWED_TRANSITIONS.get(row["status"], set()):
            raise InvalidTransition(f"{row['status']} -> {to_status} is not allowed")
        if to_status in TERMINAL:
            fields.setdefault("finished_at", now)
        sets = ["status = %s"] + [f"{name} = %s" for name in fields]
        values = [to_status] + [Jsonb(v) if isinstance(v, dict) else v for v in fields.values()]
        try:
            cur.execute(
                f"UPDATE remediation_requests SET {', '.join(sets)} WHERE remediation_id = %s RETURNING *",  # noqa: S608 - column names are checked against _WRITABLE
                (*values, remediation_id),
            )
        except psycopg.errors.UniqueViolation as exc:
            if "remediation_one_in_flight" in str(exc):
                raise Busy("another remediation is already approved or running") from exc
            raise
        updated = cur.fetchone()
        _event(cur, remediation_id, now, row["status"], to_status, actor, detail or {})
        return updated


def approve(
    conn: psycopg.Connection,
    remediation_id: uuid.UUID,
    approver_id: str,
    approver_role: str,
    now: datetime,
) -> dict:
    """A person approves a request that needs approval. Not available to the worker's database role, which has no
    privilege on the approver columns."""
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM remediation_requests WHERE remediation_id = %s FOR UPDATE",
            (remediation_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise RemediationError("no such remediation request")
        if row["status"] != "awaiting_approval":
            raise InvalidTransition(f"{row['status']} cannot be approved")
        if now - row["requested_at"] > timedelta(seconds=APPROVAL_TTL_S):
            raise RemediationError("the approval window has passed")
        if approver_id == row["requested_by"]:
            raise RemediationError("the requester cannot approve its own request")
        try:
            cur.execute(
                "UPDATE remediation_requests SET status = 'approved', approved_by = %s, approver_role = %s "
                "WHERE remediation_id = %s RETURNING *",
                (approver_id, approver_role, remediation_id),
            )
        except psycopg.errors.UniqueViolation as exc:
            if "remediation_one_in_flight" in str(exc):
                raise Busy("another remediation is already approved or running") from exc
            raise
        updated = cur.fetchone()
        _event(
            cur,
            remediation_id,
            now,
            "awaiting_approval",
            "approved",
            approver_id,
            {"role": approver_role},
        )
        return updated


def expire_stale(conn: psycopg.Connection, now: datetime, actor: str = PLATFORM_ACTOR) -> int:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT remediation_id FROM remediation_requests WHERE status = 'awaiting_approval' AND requested_at < %s",
            (now - timedelta(seconds=APPROVAL_TTL_S),),
        )
        stale = [r["remediation_id"] for r in cur.fetchall()]
    for rid in stale:
        transition(
            conn,
            rid,
            "expired",
            actor=actor,
            now=now,
            detail={"reason": "nobody approved it in time"},
        )
    return len(stale)


def abandon_interrupted(
    conn: psycopg.Connection,
    now: datetime,
    timeouts: dict[str, int],
    grace_s: int = 30,
    actor: str = PLATFORM_ACTOR,
) -> int:
    """A request still `executing`/`approved` long after its adapter's timeout belongs to a worker that died. Its outcome is
    unknown, so it is closed as `abandoned` (and counts as an attempt) rather than left looking alive."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT remediation_id, action_id, status, requested_at, started_at FROM remediation_requests WHERE status IN ('approved', 'executing')"
        )
        rows = cur.fetchall()
    closed = 0
    for row in rows:
        since = row["started_at"] or row["requested_at"]
        if (now - since).total_seconds() > timeouts.get(row["action_id"], 60) + grace_s:
            fields = {} if row["started_at"] else {"started_at": now}
            transition(
                conn, row["remediation_id"], "abandoned", actor=actor, now=now,
                detail={"reason": "the worker stopped before recording an outcome; the result is unknown"}, **fields,
            )  # fmt: skip
            closed += 1
    return closed


def get(conn: psycopg.Connection, remediation_id: uuid.UUID) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM remediation_requests WHERE remediation_id = %s", (remediation_id,)
        )
        return cur.fetchone()


def for_incident(conn: psycopg.Connection, incident_id: uuid.UUID) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM remediation_requests WHERE platform_incident_id = %s ORDER BY requested_at, attempt_no",
            (incident_id,),
        )
        return cur.fetchall()


def in_status(conn: psycopg.Connection, *statuses: str) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM remediation_requests WHERE status = ANY(%s) ORDER BY requested_at",
            (list(statuses),),
        )
        return cur.fetchall()


def timeline(conn: psycopg.Connection, remediation_id: uuid.UUID) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, at, from_status, to_status, actor, detail FROM remediation_transitions "
            "WHERE remediation_id = %s ORDER BY id",
            (remediation_id,),
        )
        return cur.fetchall()
