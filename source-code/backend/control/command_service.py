"""P07.05: request-then-approve entry points, tying `policy.py`'s pure
decision to `repositories/commands.py`'s (P05.08's) state machine.

REQUEST authority is checked here, at the door: the requester's role must be
allowed to request the command's safety class (`backend/roles.py`, P02.08),
and the role is stored on the command so the approval-time policy can
re-check it. Execution is not here at all: EXECUTE belongs to
`system:command-executor` alone (the adapter services check that identity).
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.control.policy import derive_safety_class, evaluate_command  # noqa: E402
from backend.observability import traced  # noqa: E402
from backend.repositories.commands import (  # noqa: E402
    apply_policy_unavailable,
    create_command,
    get_command,
    transition_command,
)  # noqa: E402
from backend.roles import APPROVE_ROLES, REQUEST_ROLES, SAFETY_CLASS_OF_ACTION  # noqa: E402

DEFAULT_TTL_S = 300.0


class RequestNotPermitted(PermissionError):
    pass


def request_command(
    conn: psycopg.Connection,
    idempotency_key: str,
    action_type: str,
    target_adapter: str,
    target_entity_id: str,
    requested_by: str,
    now: datetime,
    requester_role: str,
    recommendation_id: str | None = None,
    ttl_s: float = DEFAULT_TTL_S,
    params: dict | None = None,
    geometry: str | None = None,
) -> tuple[str, bool]:
    """`geometry` lets the safety class account for an active critical incident on the target; without it the
    class is the action type's own (the approval-time policy always re-derives it with the geometry)."""
    with traced("control.request_command", correlation_id=idempotency_key, action_type=action_type):
        return _request_command(
            conn, idempotency_key, action_type, target_adapter, target_entity_id,
            requested_by, now, requester_role, recommendation_id, ttl_s, params, geometry,
        )  # fmt: skip


def _request_command(
    conn: psycopg.Connection,
    idempotency_key: str,
    action_type: str,
    target_adapter: str,
    target_entity_id: str,
    requested_by: str,
    now: datetime,
    requester_role: str,
    recommendation_id: str | None = None,
    ttl_s: float = DEFAULT_TTL_S,
    params: dict | None = None,
    geometry: str | None = None,
) -> tuple[str, bool]:
    sc = (
        derive_safety_class(conn, action_type, target_adapter, target_entity_id, geometry)
        if geometry
        else SAFETY_CLASS_OF_ACTION.get(action_type, "SC-1")
    )
    if requester_role not in REQUEST_ROLES[sc]:
        raise RequestNotPermitted(
            f"role {requester_role!r} may not request a {sc} action ({action_type!r})"
        )
    return create_command(
        conn,
        idempotency_key,
        action_type,
        target_adapter,
        target_entity_id,
        requested_by,
        ttl_s,
        "pending",
        recommendation_id,
        at=now,
        requested_by_role=requester_role,
        params=params,
    )


def review_command(
    conn: psycopg.Connection,
    command_id: str,
    approver: str,
    approver_role: str,
    now: datetime,
    geometry: str,
) -> str:
    """Evaluates policy and applies the decision. Returns the resulting status
    ('approved', 'denied', or 'requested' when the policy itself is unavailable)."""
    with traced("control.review_command", correlation_id=command_id):
        return _review_command(conn, command_id, approver, approver_role, now, geometry)


def _review_command(
    conn: psycopg.Connection,
    command_id: str,
    approver: str,
    approver_role: str,
    now: datetime,
    geometry: str,
) -> str:
    command = get_command(conn, command_id)
    if command is None:
        raise ValueError(f"unknown command {command_id}")
    result = evaluate_command(conn, command, approver, approver_role, now, geometry)
    if result.decision == "policy_unavailable":
        apply_policy_unavailable(conn, command_id, result.error_code, result.message, at=now)
        return "requested"
    if result.decision == "approved":
        transition_command(
            conn,
            command_id,
            "approved",
            approver,
            f"policy approved (policy {result.policy_version})"
            if result.policy_version
            else "policy approved",
            at=now,
            approved_by=approver,
            policy_decision="approved",
            approved_by_role=approver_role,
        )
        return "approved"
    # 'expired' is a command status the contract defines separately from 'denied', but policy_decision's
    # own enum has no 'expired' value (migration 0004) - an expiry is still recorded as a policy denial,
    # just one that moves the command to the 'expired' status rather than 'denied'.
    to_status = "expired" if result.decision == "expired" else "denied"
    error = result.as_error_record()
    transition_command(
        conn,
        command_id,
        to_status,
        approver,
        error["message"],
        at=now,
        error_code=error["error_code"],
        error_message=error["message"],
        error_retryable=error["retryable"],
        policy_decision="denied",
    )
    return to_status


def deny_command(
    conn: psycopg.Connection,
    command_id: str,
    approver: str,
    approver_role: str,
    reason: str,
    now: datetime,
    geometry: str,
) -> None:
    """A human reviewer's own decision not to approve. It needs the same authority as an approval (a role that
    may approve this safety class) but not four-eyes: refusing your own request is always allowed to whoever
    could have approved it."""
    with traced("control.deny_command", correlation_id=command_id):
        return _deny_command(conn, command_id, approver, approver_role, reason, now, geometry)


def _deny_command(
    conn: psycopg.Connection,
    command_id: str,
    approver: str,
    approver_role: str,
    reason: str,
    now: datetime,
    geometry: str,
) -> None:
    command = get_command(conn, command_id)
    if command is None:
        raise ValueError(f"unknown command {command_id}")
    sc = derive_safety_class(
        conn,
        command["action_type"],
        command["target_adapter"],
        command["target_entity_id"],
        geometry,
    )
    if approver_role not in APPROVE_ROLES[sc]:
        raise RequestNotPermitted(f"role {approver_role!r} may not review a {sc} action")
    transition_command(
        conn,
        command_id,
        "denied",
        approver,
        f"denied by {approver}: {reason}",
        at=now,
        error_code="policy_denied",
        error_message=f"denied by reviewer: {reason}",
        error_retryable=False,
        policy_decision="denied",
    )
