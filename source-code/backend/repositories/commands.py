"""P05.08: the command state machine (`contracts/command/v1`) - row-locked
transitions, an append-only audit trail, the same discipline as `incidents.py`.

`idempotency_key` is `UNIQUE` at the database level (migration 0004):
`create_command` checks first for speed, but a concurrent duplicate that wins
the race is still caught by the constraint and resolved to the existing
command - the actual mechanism behind "a resubmission must not execute
twice," not merely a documented intent.

Two audit references the contract requires are enforced here, not left to a
caller's discipline: approving a command without recording *who* approved it
(`approved_by`) is refused, and so is moving to a status the contract says
must carry a structured `error` (`ERROR_REQUIRED_STATUSES` - 'denied' and
'failed') without one.

P07.05's `backend/control/policy.py` layers *why* a transition is or is not
allowed (role, four-eyes, freshness, expiry) on top of this module, the same
way P07.03's cross-agency staging layers on P07.01's emergency state machine:
this repository only knows a command's own legal shape, nothing about who is
allowed to invoke it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import psycopg.errors
from psycopg.types.json import Jsonb

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "requested": {"approved", "denied", "expired"},
    "approved": {
        "executing",
        "expired",
        "denied",
    },  # denied: the executor's own policy check (P09.03) refused it
    "executing": {"executed", "failed"},
    "executed": {"rolled_back"},
    "denied": set(),
    "failed": set(),
    "rolled_back": set(),
    "expired": set(),
}
ERROR_REQUIRED_STATUSES = {"denied", "failed"}


class TransitionError(Exception):
    pass


class InvalidTransition(TransitionError):
    pass


class CommandNotFound(TransitionError):
    pass


class MissingAuditReference(TransitionError):
    """Raised when a transition that the contract requires an audit reference for
    ('approved' needs `approved_by`; a status in ERROR_REQUIRED_STATUSES needs the
    full `error` triple) is attempted without one - refused, not silently accepted
    with a null reference."""


def _now(at: datetime | None) -> datetime:
    return at or datetime.now(timezone.utc)


def create_command(
    conn: psycopg.Connection,
    idempotency_key: str,
    action_type: str,
    target_adapter: str,
    target_entity_id: str,
    requested_by: str,
    expires_in_seconds: float = 3600.0,
    policy_decision: str = "pending",
    recommendation_id: str | None = None,
    at: datetime | None = None,
    command_id: str | None = None,
    requested_by_role: str | None = None,
    params: dict | None = None,
) -> tuple[str, bool]:
    """Returns (command_id, created). `created` is False when `idempotency_key` already existed -
    the existing command_id is returned, nothing new is inserted."""
    now = _now(at)
    expires_at = now + timedelta(seconds=expires_in_seconds)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT command_id FROM commands WHERE idempotency_key = %s", (idempotency_key,)
        )
        existing = cur.fetchone()
        if existing is not None:
            return str(existing[0]), False
        command_id = command_id or str(uuid.uuid4())
        try:
            cur.execute(
                """
                INSERT INTO commands (command_id, idempotency_key, recommendation_id, action_type, target_adapter, target_entity_id,
                    requested_by, requested_by_role, requested_at, expires_at, policy_decision, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'requested')
                """,
                (
                    command_id,
                    idempotency_key,
                    recommendation_id,
                    action_type,
                    target_adapter,
                    target_entity_id,
                    requested_by,
                    requested_by_role,
                    now,
                    expires_at,
                    policy_decision,
                ),
            )
        except psycopg.errors.UniqueViolation:
            # a concurrent submission with the same key won the race between our SELECT and INSERT;
            # the UNIQUE constraint is the real guarantee, this SELECT-then-INSERT is only the fast path
            conn.rollback()
            with conn.cursor() as cur2:
                cur2.execute(
                    "SELECT command_id FROM commands WHERE idempotency_key = %s", (idempotency_key,)
                )
                return str(cur2.fetchone()[0]), False
        cur.execute(
            "INSERT INTO command_transitions (command_id, from_status, to_status, changed_by, note, changed_at) "
            "VALUES (%s, NULL, 'requested', %s, 'command requested', %s)",
            (command_id, requested_by, now),
        )
        if params is not None:
            cur.execute(
                "INSERT INTO command_params (command_id, params) VALUES (%s, %s)",
                (command_id, Jsonb(params)),
            )
    conn.commit()
    return command_id, True


def transition_command(
    conn: psycopg.Connection,
    command_id: str,
    to_status: str,
    changed_by: str,
    note: str | None = None,
    at: datetime | None = None,
    approved_by: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    error_retryable: bool | None = None,
    acknowledged: bool = False,
    rolled_back: bool = False,
    policy_decision: str | None = None,
    approved_by_role: str | None = None,
) -> None:
    """Raises CommandNotFound / InvalidTransition / MissingAuditReference and leaves the row untouched
    (transaction rolled back) rather than applying a partial or under-documented change. Legality of
    the transition itself is checked before completeness of its audit references, so an outright
    illegal transition is always reported as InvalidTransition, never masked by a missing reference."""
    now = _now(at)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM commands WHERE command_id = %s FOR UPDATE", (command_id,))
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            raise CommandNotFound(command_id)
        (from_status,) = row
        if to_status not in ALLOWED_TRANSITIONS.get(from_status, set()):
            conn.rollback()
            raise InvalidTransition(f"command {from_status} -> {to_status} is not allowed")
        if to_status == "approved" and approved_by is None:
            conn.rollback()
            raise MissingAuditReference("'approved' requires approved_by")
        if to_status in ERROR_REQUIRED_STATUSES and (
            error_code is None or error_message is None or error_retryable is None
        ):
            conn.rollback()
            raise MissingAuditReference(
                f"{to_status!r} requires error_code, error_message and error_retryable"
            )
        sets, params = ["status = %s", "updated_at = %s"], [to_status, now]
        if approved_by is not None:
            sets += ["approved_by = %s", "approved_at = %s", "approved_by_role = %s"]
            params += [approved_by, now, approved_by_role]
        if policy_decision is not None:
            sets.append("policy_decision = %s")
            params.append(policy_decision)
        if acknowledged:
            sets.append("acknowledged_at = %s")
            params.append(now)
        if rolled_back:
            sets.append("rolled_back_at = %s")
            params.append(now)
        if error_code is not None:
            sets += ["error_code = %s", "error_message = %s", "error_retryable = %s"]
            params += [error_code, error_message, error_retryable]
        elif to_status == "approved":
            # An approval that follows a policy outage must not keep showing the outage as this command's error.
            sets += ["error_code = NULL", "error_message = NULL", "error_retryable = NULL"]
        params.append(command_id)
        cur.execute(f"UPDATE commands SET {', '.join(sets)} WHERE command_id = %s", params)
        cur.execute(
            "INSERT INTO command_transitions (command_id, from_status, to_status, changed_by, note, changed_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (command_id, from_status, to_status, changed_by, note, now),
        )
    conn.commit()


def apply_policy_unavailable(
    conn: psycopg.Connection,
    command_id: str,
    error_code: str | None,
    error_message: str | None,
    at: datetime | None = None,
) -> None:
    """Records a policy-evaluation outage without changing `status` - the command stays 'requested',
    never silently approved (SAFE-02). Not a state transition, so it is not in `ALLOWED_TRANSITIONS`
    and carries no audit-trail row of its own; `error_code`/`error_message` are still recorded on the
    row so an operator can see why review has not completed."""
    now = _now(at)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM commands WHERE command_id = %s FOR UPDATE", (command_id,))
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            raise CommandNotFound(command_id)
        if row[0] != "requested":
            conn.rollback()
            raise InvalidTransition(
                f"cannot record a policy outage on a command already {row[0]!r}"
            )
        cur.execute(
            "UPDATE commands SET policy_decision = 'policy_unavailable', error_code = %s, error_message = %s, "
            "error_retryable = TRUE, updated_at = %s WHERE command_id = %s",
            (error_code, error_message, now, command_id),
        )
    conn.commit()


def expire_stale(conn: psycopg.Connection, now: datetime | None = None) -> int:
    now = _now(now)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE commands SET status = 'expired', updated_at = %s WHERE status IN ('requested', 'approved') AND expires_at <= %s",
            (now, now),
        )
        return cur.rowcount


def get_command(conn: psycopg.Connection, command_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT command_id, idempotency_key, recommendation_id, action_type, target_adapter, target_entity_id, requested_by, "
            "requested_at, approved_by, approved_at, expires_at, policy_decision, status, acknowledged_at, rolled_back_at, "
            "error_code, error_message, error_retryable, requested_by_role, approved_by_role FROM commands WHERE command_id = %s",
            (command_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    cols = [
        "command_id",
        "idempotency_key",
        "recommendation_id",
        "action_type",
        "target_adapter",
        "target_entity_id",
        "requested_by",
        "requested_at",
        "approved_by",
        "approved_at",
        "expires_at",
        "policy_decision",
        "status",
        "acknowledged_at",
        "rolled_back_at",
        "error_code",
        "error_message",
        "error_retryable",
        "requested_by_role",
        "approved_by_role",
    ]
    return dict(zip(cols, row, strict=True))


def get_params(conn: psycopg.Connection, command_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT params FROM command_params WHERE command_id = %s", (command_id,))
        row = cur.fetchone()
    return row[0] if row else None


def transition_history(conn: psycopg.Connection, command_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT from_status, to_status, changed_by, note, changed_at FROM command_transitions "
            "WHERE command_id = %s ORDER BY changed_at, id",
            (command_id,),
        )
        rows = cur.fetchall()
    cols = ["from_status", "to_status", "changed_by", "note", "changed_at"]
    return [dict(zip(cols, r, strict=True)) for r in rows]
