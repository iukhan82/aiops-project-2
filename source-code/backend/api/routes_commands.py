"""P08.08: the human side of the command path - ask for an action, decide on it.

Three steps, and a person only ever takes the first two:

1. REQUEST  - from a stored recommendation (`POST /recommendations/{id}/request`) or directly (`POST /commands`).
2. APPROVE  - a different person, in a role the safety class allows, decides (`POST /commands/{id}/review`). The
              execution-time policy (`backend/control/policy.py`) runs again at that moment against what is true now.
3. EXECUTE  - not here and not by any person: `system:command-executor` (`backend/control/executor_worker.py`) picks up an
              approved command and drives the adapter.

The endpoints refuse what a role or the four-eyes rule forbids *before* the policy runs, because a policy denial is recorded
against the command: letting anyone who is not entitled to approve reach the policy would let them deny other people's commands.
Every request is idempotent on a client-generated key, so a double click or a retry cannot create two commands.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated, Literal

import psycopg
from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field

from backend import pdp
from backend.api.db import get_conn
from backend.api.workflow import audit, principal_of, problem
from backend.control.command_service import (
    RequestNotPermitted,
    deny_command,
    request_command,
    review_command,
)
from backend.control.policy import ADAPTER_TARGET_KIND, KNOWN_ADAPTERS, authorise
from backend.repositories import commands as command_repo
from backend.repositories import recommendations as reco_repo
from backend.repositories.commands import InvalidTransition

KEY = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9._:-]+$")
ADAPTER_OF_ACTION = {
    "signal_plan_change": "signal_controller_adapter",
    "diversion": "diversion_adapter",
    "variable_message_sign": "vms_adapter",
}
DIRECT_TTL_S = 900.0


class FromRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alternative_id: str = Field(min_length=1, max_length=80)
    idempotency_key: str = KEY


class DirectCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_type: Literal["signal_plan_change", "diversion", "variable_message_sign"]
    target_entity_id: str = Field(min_length=1, max_length=80)
    idempotency_key: str = KEY
    deviation_s: float | None = Field(default=None, ge=0, le=60)
    message: str | None = Field(default=None, min_length=1, max_length=120)


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "deny"]
    reason: str | None = Field(default=None, max_length=500)


class Override(BaseModel):
    model_config = ConfigDict(extra="forbid")
    justification: str = Field(min_length=10, max_length=1000)


def _geometry(conn: psycopg.Connection) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT version FROM geometry_versions WHERE superseded_by IS NULL ORDER BY effective_from DESC LIMIT 1"
        )
        row = cur.fetchone()
    if row is None:
        problem(503, "no_geometry", "No network geometry is loaded.")
    return row[0]


def _metric(items: list[dict], name: str) -> float | None:
    found = next((m["value"] for m in items if m.get("name") == name), None)
    return float(found) if found is not None else None


def _refuse_request(exc: RequestNotPermitted) -> None:
    problem(403, "role_cannot_request", str(exc))


def _authority(
    conn: psycopg.Connection,
    principal,
    kind: str,
    action_type: str,
    adapter: str,
    entity_id: str,
    geometry: str,
    audit_action: str,
    entity_type: str,
    audit_entity_id: str | None,
) -> dict:
    """Ask the policy engine which of the caller's roles (if any) may request or review this action, and what safety class it is. An
    engine that cannot answer is a 503 and a row in the audit trail, never a permit."""
    try:
        return authorise(
            conn,
            kind,
            action_type,
            adapter,
            entity_id,
            geometry,
            list(principal.roles),
            principal.username,
            (entity_type, audit_entity_id)
            if audit_entity_id
            else ("target", f"{adapter}/{entity_id}"),
        )
    except pdp.PolicyUnavailable:
        audit(
            conn,
            principal,
            audit_action,
            entity_type,
            audit_entity_id,
            "denied",
            reason="the policy engine could not be reached",
        )
        problem(
            503,
            "policy_unavailable",
            "The policy engine could not be reached, so nothing was decided and nothing was changed. Try again shortly.",
        )


def register(app: FastAPI) -> None:
    @app.post("/api/v1/recommendations/{recommendation_id}/request", status_code=201)
    def request_from_recommendation(
        recommendation_id: str,
        body: FromRecommendation,
        request: Request,
        conn: Annotated[psycopg.Connection, Depends(get_conn)],
    ) -> dict:
        principal = principal_of(request)
        rec = (
            reco_repo.get(conn, recommendation_id)
            if re.fullmatch(r"[0-9a-fA-F-]{36}", recommendation_id)
            else None
        )
        if rec is None:
            problem(404, "unknown_recommendation", "There is no such recommendation.")
        if rec["status"] not in ("proposed", "requested"):
            audit(
                conn,
                principal,
                "command.request",
                "recommendation",
                recommendation_id,
                "denied",
                reason=f"recommendation is {rec['status']}",
            )
            problem(
                409,
                "recommendation_not_actionable",
                f"This recommendation is {rec['status']}, so it can no longer be acted on.",
                status=rec["status"],
            )
        alternative = next(
            (a for a in rec["alternatives"] if a.get("alternative_id") == body.alternative_id), None
        )
        if alternative is None:
            problem(
                422, "unknown_alternative", "That is not one of this recommendation's alternatives."
            )
        if str(alternative.get("description", "")).lower().startswith("take no action"):
            problem(
                422,
                "nothing_to_request",
                "Taking no action is not a command. Leave the recommendation alone to let it expire.",
            )
        geometry = _geometry(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT network_element_type, network_element_id FROM incidents WHERE incident_id = %s",
                (rec["trigger_incident_id"],),
            )
            incident = cur.fetchone()
        if incident is None:
            problem(
                422,
                "no_target",
                "This recommendation has no incident to act on, so its target cannot be worked out.",
            )
        element_type, element_id = incident
        segment = element_id.rsplit("_", 1)[0] if element_type == "lane" else element_id
        params: dict = {}
        if rec["action_type"] == "diversion":
            adapter, entity = "diversion_adapter", segment
        elif rec["action_type"] == "signal_plan_change":
            adapter = "signal_controller_adapter"
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT to_node FROM network_segments WHERE edge_id = %s AND geometry_version = %s",
                    (segment, geometry),
                )
                row = cur.fetchone()
            if row is None:
                problem(
                    422,
                    "no_target",
                    f"{segment} is not a road segment, so its signal cannot be found.",
                )
            entity = row[0]
            deviation = _metric(alternative.get("predicted_harm", []), "cross_street_added_wait")
            if deviation is None:
                problem(
                    422,
                    "no_parameters",
                    "This alternative does not say how much green time it changes.",
                )
            params = {
                "deviation_s": deviation,
                "max_deviation_s": float(rec["safety_bounds"].get("max_signal_deviation_s", 20.0)),
            }
        else:
            problem(
                422,
                "unsupported_action",
                f"A {rec['action_type']} recommendation cannot be turned into a command here.",
            )
        # What was proposed travels with the command, so whoever reviews it sees the benefit and harm they are being asked to accept.
        params["basis"] = {
            "recommendation_id": recommendation_id,
            "alternative_id": body.alternative_id,
            "description": alternative.get("description"),
            "predicted_benefit": alternative.get("predicted_benefit", []),
            "predicted_harm": alternative.get("predicted_harm", []),
            "confidence": alternative.get("confidence"),
            "constraints": rec.get("constraints", []),
            "safety_bounds": rec.get("safety_bounds", {}),
        }
        authority = _authority(
            conn,
            principal,
            "request",
            rec["action_type"],
            adapter,
            entity,
            geometry,
            "command.request",
            "recommendation",
            recommendation_id,
        )
        safety_class, role = authority["safety_class"], authority["role"]
        if not authority["allow"]:
            audit(
                conn,
                principal,
                "command.request",
                "recommendation",
                recommendation_id,
                "denied",
                reason=f"no role may request {safety_class}",
                safety_class=safety_class,
            )
            _refuse_request(
                RequestNotPermitted(
                    f"your role may not request a {safety_class} action ({rec['action_type']})"
                )
            )
        command_id, created = request_command(
            conn,
            f"ui-{body.idempotency_key}",
            rec["action_type"],
            adapter,
            entity,
            principal.username,
            datetime.now(timezone.utc),
            role,
            recommendation_id=recommendation_id,
            params=params,
            geometry=geometry,
        )
        if created and rec["status"] == "proposed":
            reco_repo.mark_requested(conn, recommendation_id)
        audit(
            conn,
            principal,
            "command.request",
            "command",
            command_id,
            "allowed",
            recommendation_id=recommendation_id,
            alternative_id=body.alternative_id,
            safety_class=safety_class,
            created=created,
        )
        return {
            "command_id": command_id,
            "created": created,
            "status": "requested",
            "safety_class": safety_class,
        }

    @app.post("/api/v1/commands", status_code=201)
    def create_direct(
        body: DirectCommand,
        request: Request,
        conn: Annotated[psycopg.Connection, Depends(get_conn)],
    ) -> dict:
        principal = principal_of(request)
        adapter = ADAPTER_OF_ACTION[body.action_type]
        if adapter not in KNOWN_ADAPTERS:
            problem(422, "unknown_adapter", f"{adapter} is not a registered adapter.")
        params: dict = {}
        if body.action_type == "signal_plan_change":
            if body.deviation_s is None:
                problem(
                    422,
                    "no_parameters",
                    "A signal change needs deviation_s, the seconds of green to move.",
                )
            params = {"deviation_s": body.deviation_s, "max_deviation_s": 20.0}
        elif body.action_type == "variable_message_sign":
            if not body.message or not body.message.strip():
                problem(422, "no_parameters", "A sign message needs the text to show.")
            params = {"message": body.message.strip()}
        geometry = _geometry(conn)
        authority = _authority(
            conn,
            principal,
            "request",
            body.action_type,
            adapter,
            body.target_entity_id,
            geometry,
            "command.request",
            "command",
            None,
        )
        safety_class, role = authority["safety_class"], authority["role"]
        if not authority["allow"]:
            audit(
                conn,
                principal,
                "command.request",
                "command",
                None,
                "denied",
                reason=f"no role may request {safety_class}",
                action_type=body.action_type,
                target=body.target_entity_id,
            )
            _refuse_request(
                RequestNotPermitted(
                    f"your role may not request a {safety_class} action ({body.action_type})"
                )
            )
        command_id, created = request_command(
            conn,
            f"ui-{body.idempotency_key}",
            body.action_type,
            adapter,
            body.target_entity_id,
            principal.username,
            datetime.now(timezone.utc),
            role,
            ttl_s=DIRECT_TTL_S,
            params=params,
            geometry=geometry,
        )
        audit(
            conn,
            principal,
            "command.request",
            "command",
            command_id,
            "allowed",
            action_type=body.action_type,
            target=body.target_entity_id,
            safety_class=safety_class,
            created=created,
            target_kind=ADAPTER_TARGET_KIND[adapter],
        )
        return {
            "command_id": command_id,
            "created": created,
            "status": "requested",
            "safety_class": safety_class,
        }

    @app.post("/api/v1/commands/{command_id}/review")
    def review(
        command_id: str,
        body: Review,
        request: Request,
        conn: Annotated[psycopg.Connection, Depends(get_conn)],
    ) -> dict:
        principal = principal_of(request)
        command = (
            command_repo.get_command(conn, command_id)
            if re.fullmatch(r"[0-9a-fA-F-]{36}", command_id)
            else None
        )
        if command is None:
            problem(404, "unknown_command", "There is no such command.")
        if command["status"] != "requested":
            audit(
                conn,
                principal,
                f"command.{body.decision}",
                "command",
                command_id,
                "denied",
                reason=f"command is {command['status']}",
            )
            problem(
                409,
                "invalid_transition",
                f"This command is {command['status']}; only a requested command can be reviewed.",
                current_status=command["status"],
            )
        if body.decision == "deny" and not (body.reason and body.reason.strip()):
            problem(422, "reason_required", "Denying a command needs a reason.")
        geometry = _geometry(conn)
        authority = _authority(
            conn,
            principal,
            "review",
            command["action_type"],
            command["target_adapter"],
            command["target_entity_id"],
            geometry,
            f"command.{body.decision}",
            "command",
            command_id,
        )
        safety_class, role = authority["safety_class"], authority["role"]
        if not authority["allow"]:
            audit(
                conn,
                principal,
                f"command.{body.decision}",
                "command",
                command_id,
                "denied",
                reason=f"no role may review {safety_class}",
                safety_class=safety_class,
            )
            problem(
                403,
                "role_cannot_review",
                f"Your role may not review a {safety_class} action.",
                safety_class=safety_class,
            )
        if body.decision == "approve" and command["requested_by"] == principal.username:
            audit(
                conn,
                principal,
                "command.approve",
                "command",
                command_id,
                "denied",
                reason="four-eyes: requester cannot approve",
            )
            problem(
                403, "four_eyes", "You requested this command, so someone else has to approve it."
            )
        now = datetime.now(timezone.utc)
        try:
            if body.decision == "approve":
                result = review_command(conn, command_id, principal.username, role, now, geometry)
            else:
                deny_command(
                    conn, command_id, principal.username, role, body.reason.strip(), now, geometry
                )
                result = "denied"
        except InvalidTransition as exc:
            audit(
                conn,
                principal,
                f"command.{body.decision}",
                "command",
                command_id,
                "denied",
                reason=str(exc),
            )
            problem(409, "invalid_transition", str(exc))
        after = command_repo.get_command(conn, command_id)
        audit(
            conn,
            principal,
            f"command.{body.decision}",
            "command",
            command_id,
            "allowed",
            result=result,
            safety_class=safety_class,
            role=role,
        )
        error = None
        if after.get("error_code"):
            error = {
                "error_code": after["error_code"],
                "message": after["error_message"],
                "retryable": after["error_retryable"],
            }
        return {
            "command_id": command_id,
            "status": after["status"],
            "previous_status": "requested",
            "policy_decision": after["policy_decision"],
            "error": error,
            "safety_class": safety_class,
        }

    @app.post("/api/v1/commands/{command_id}/override")
    def override(
        command_id: str,
        body: Override,
        request: Request,
        conn: Annotated[psycopg.Connection, Depends(get_conn)],
    ) -> dict:
        """OVERRIDE (docs/security/ROLES_AND_ACTION_AUTHORITY.md): countermand an executed SC-2 command before
        independent verification completes. Reserved to incident_commander; there is no override for SC-0 or
        SC-1, so those are refused here, not silently allowed. This prototype's adapter is one-shot per command
        and holds no live session to drive a further physical reversal (see docs/security/SECURITY_ARCHITECTURE.md
        "Out of scope"): an override records the countermand and moves the command to `rolled_back`, and says so
        honestly rather than claiming a physical undo that did not happen."""
        principal = principal_of(request)
        command = (
            command_repo.get_command(conn, command_id)
            if re.fullmatch(r"[0-9a-fA-F-]{36}", command_id)
            else None
        )
        if command is None:
            problem(404, "unknown_command", "There is no such command.")
        if command["status"] != "executed":
            audit(
                conn,
                principal,
                "command.override",
                "command",
                command_id,
                "denied",
                reason=f"command is {command['status']}",
            )
            problem(
                409,
                "invalid_transition",
                f"This command is {command['status']}; only an executed command awaiting verification can be overridden.",
                current_status=command["status"],
            )
        geometry = _geometry(conn)
        authority = _authority(
            conn,
            principal,
            "override",
            command["action_type"],
            command["target_adapter"],
            command["target_entity_id"],
            geometry,
            "command.override",
            "command",
            command_id,
        )
        safety_class, role = authority["safety_class"], authority["role"]
        if not authority["allow"]:
            unavailable = safety_class in ("SC-0", "SC-1")
            audit(
                conn,
                principal,
                "command.override",
                "command",
                command_id,
                "denied",
                reason=(
                    f"no override exists for {safety_class}"
                    if unavailable
                    else f"no role may override {safety_class}"
                ),
                safety_class=safety_class,
            )
            if unavailable:
                problem(
                    422,
                    "override_not_available",
                    f"There is no override for a {safety_class} action; cancel and resubmit instead.",
                    safety_class=safety_class,
                )
            problem(
                403,
                "role_cannot_override",
                f"Your role may not override a {safety_class} action.",
                safety_class=safety_class,
            )
        now = datetime.now(timezone.utc)
        justification = body.justification.strip()
        command_repo.transition_command(
            conn,
            command_id,
            "rolled_back",
            principal.username,
            f"OVERRIDE by {principal.username} ({role}): {justification}",
            at=now,
            rolled_back=True,
        )
        after = command_repo.get_command(conn, command_id)
        audit(
            conn,
            principal,
            "command.override",
            "command",
            command_id,
            "allowed",
            safety_class=safety_class,
            role=role,
            justification=justification,
            physical_undo="not attempted: this prototype's adapter holds no live session to drive a further reversal",
        )
        return {
            "command_id": command_id,
            "status": after["status"],
            "safety_class": safety_class,
            "overridden_by": principal.username,
            "overridden_role": role,
        }
