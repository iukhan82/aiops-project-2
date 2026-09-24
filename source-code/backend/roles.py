"""Operational roles, safety classes and action authority - the one place
code reads `docs/security/ROLES_AND_ACTION_AUTHORITY.md` (P02.08) from.

These are the *operating* roles (who may recommend/request/approve/execute/
override/demo/audit), granted by Keycloak (P09.02) and carried in tokens.
They are not the `owner_role` desk codes (`OPS`, `EMERG`, `CONTROL`, ...) the
incident and SLO contracts use for which *function* owns something.

`safety_class` is derived, never stored: the action type sets the base class,
and an active critical incident on the same target raises any action to SC-2.
"""

from __future__ import annotations

HUMAN_ROLES = (
    "operator",
    "supervisor",
    "dispatcher",
    "incident_commander",
    "field_responder",
    "auditor",
    "demo_operator",
)
EXECUTOR = "system:command-executor"
OUTCOME_VERIFIER = "system:outcome-verifier"
SERVICE_IDENTITIES = (
    "system:optimization-engine",
    "system:emergency-engine",
    EXECUTOR,
    "system:aiops-remediation",
    OUTCOME_VERIFIER,
)

SAFETY_CLASS_OF_ACTION = {
    "variable_message_sign": "SC-0",
    "signal_plan_change": "SC-1",
    "diversion": "SC-1",
    "transit_priority": "SC-1",
    "emergency_preemption": "SC-2",
    "other": "SC-1",
}
REQUEST_ROLES = {"SC-0": {"operator"}, "SC-1": {"operator"}, "SC-2": {"operator", "dispatcher"}}
APPROVE_ROLES = {
    "SC-0": {"operator", "supervisor"},
    "SC-1": {"supervisor"},
    "SC-2": {"supervisor", "incident_commander"},
}
FOUR_EYES_CLASSES = {"SC-1", "SC-2"}
# OVERRIDE (docs/security/ROLES_AND_ACTION_AUTHORITY.md): countermand an executed command before independent
# verification completes. Reserved to incident_commander, SC-2 only; SC-0 and SC-1 have no override - cancel and resubmit.
OVERRIDE_ROLES = {"SC-0": set(), "SC-1": set(), "SC-2": {"incident_commander"}}


def safety_class(action_type: str, critical_incident_on_target: bool = False) -> str:
    return (
        "SC-2" if critical_incident_on_target else SAFETY_CLASS_OF_ACTION.get(action_type, "SC-1")
    )


class NotPermitted(PermissionError):
    pass


def require_executor(actor: str) -> None:
    """EXECUTE authority: only `system:command-executor` may call an infrastructure adapter - no human role,
    not the requester, not the approver."""
    if actor != EXECUTOR:
        raise NotPermitted(
            f"{actor!r} may not execute a command: only {EXECUTOR!r} holds EXECUTE authority"
        )


def require_verifier(actor: str) -> None:
    if actor != OUTCOME_VERIFIER:
        raise NotPermitted(
            f"{actor!r} may not record an outcome: only {OUTCOME_VERIFIER!r} verifies outcomes"
        )
