"""P08.07: the dispatcher's actions - take a call, assign a unit with its route, move the unit along, choose a route.

Attributed and audited like every operator action. The state machines and route search are the platform's own
(`repositories/emergency.py`, `emergency/dispatch_service.py`); this layer only checks input and translates refusals.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

import psycopg
from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.api.db import get_conn
from backend.api.workflow import audit, principal_of, problem
from backend.emergency import dispatch_service as dispatch
from backend.repositories import emergency as repo

GEOMETRY_QUERY = "SELECT version FROM geometry_versions WHERE superseded_by IS NULL ORDER BY effective_from DESC LIMIT 1"


class CallBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_type: Literal["ambulance", "fire", "police"]
    call_subtype: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z][A-Za-z0-9_ ]*$")
    priority: Literal["low", "medium", "high", "critical"]
    source_reliability: Literal["verified_dispatch", "unverified_report", "automated_detection"] = (
        "verified_dispatch"
    )
    intersection_id: str | None = Field(default=None, max_length=40)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class AssignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9-]*$")
    origin_intersection_id: str | None = Field(default=None, max_length=40)


class CallTransitionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_status: Literal["cancelled"]
    note: str = Field(min_length=1, max_length=500)


class AssignmentTransitionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_status: Literal["acknowledged", "en_route", "staged", "on_scene", "clear", "unavailable"]
    note: str | None = Field(default=None, max_length=500)


class RouteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route_id: str = Field(min_length=1, max_length=80)


def _uuid_or(value: str, code: str, message: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        problem(404, code, message)


def register(app: FastAPI) -> None:
    @app.post("/api/v1/emergency/calls", status_code=201)
    def create_call(
        body: CallBody, request: Request, conn: Annotated[psycopg.Connection, Depends(get_conn)]
    ) -> dict:
        principal = principal_of(request)
        with conn.cursor() as cur:
            cur.execute(GEOMETRY_QUERY)
            geometry = cur.fetchone()[0]
        try:
            call_id = dispatch.create_call(
                conn,
                body.call_type,
                body.call_subtype.strip(),
                body.priority,
                body.source_reliability,
                geometry,
                principal.username,
                body.intersection_id,
                body.latitude,
                body.longitude,
            )
        except dispatch.DispatchError as exc:
            audit(
                conn,
                principal,
                "call.create",
                "emergency_call",
                None,
                "denied",
                reason=exc.message,
                **body.model_dump(exclude_none=True),
            )
            problem(422, exc.code, exc.message)
        audit(
            conn,
            principal,
            "call.create",
            "emergency_call",
            call_id,
            "allowed",
            **body.model_dump(exclude_none=True),
        )
        return {"call_id": call_id, "status": "received", "truth_label": "operator_entered"}

    @app.post("/api/v1/emergency/calls/{call_id}/assignments", status_code=201)
    def assign(
        call_id: str,
        body: AssignBody,
        request: Request,
        conn: Annotated[psycopg.Connection, Depends(get_conn)],
    ) -> dict:
        principal = principal_of(request)
        call_id = _uuid_or(call_id, "unknown_call", "There is no such call.")
        try:
            assignment_id = dispatch.assign_unit(
                conn, call_id, body.unit_id, principal.username, body.origin_intersection_id
            )
        except repo.NotFound:
            audit(
                conn,
                principal,
                "assignment.create",
                "emergency_call",
                call_id,
                "failed",
                unit_id=body.unit_id,
                reason="unknown call",
            )
            problem(404, "unknown_call", "There is no such call.")
        except dispatch.DispatchError as exc:
            audit(
                conn,
                principal,
                "assignment.create",
                "emergency_call",
                call_id,
                "denied",
                unit_id=body.unit_id,
                reason=exc.message,
                code=exc.code,
            )
            problem(
                409 if exc.code in ("already_assigned", "call_closed") else 422,
                exc.code,
                exc.message,
            )
        except repo.InvalidTransition as exc:
            conn.rollback()
            audit(
                conn,
                principal,
                "assignment.create",
                "emergency_call",
                call_id,
                "denied",
                unit_id=body.unit_id,
                reason=str(exc),
            )
            problem(409, "invalid_transition", str(exc))
        audit(
            conn,
            principal,
            "assignment.create",
            "emergency_call",
            call_id,
            "allowed",
            unit_id=body.unit_id,
            assignment_id=assignment_id,
        )
        return {
            "assignment_id": assignment_id,
            "call_id": call_id,
            "unit_id": body.unit_id,
            "status": "assigned",
        }

    @app.post("/api/v1/emergency/calls/{call_id}/transition")
    def transition_call(
        call_id: str,
        body: CallTransitionBody,
        request: Request,
        conn: Annotated[psycopg.Connection, Depends(get_conn)],
    ) -> dict:
        principal = principal_of(request)
        call_id = _uuid_or(call_id, "unknown_call", "There is no such call.")
        try:
            repo.transition_call(
                conn, call_id, body.to_status, principal.username, body.note.strip()
            )
        except repo.NotFound:
            audit(
                conn,
                principal,
                "call.transition",
                "emergency_call",
                call_id,
                "failed",
                to_status=body.to_status,
                reason="unknown call",
            )
            problem(404, "unknown_call", "There is no such call.")
        except repo.InvalidTransition as exc:
            audit(
                conn,
                principal,
                "call.transition",
                "emergency_call",
                call_id,
                "denied",
                to_status=body.to_status,
                reason=str(exc),
            )
            problem(409, "invalid_transition", str(exc))
        audit(
            conn,
            principal,
            "call.transition",
            "emergency_call",
            call_id,
            "allowed",
            to_status=body.to_status,
            note=body.note,
        )
        return {"call_id": call_id, "status": body.to_status}

    @app.post("/api/v1/emergency/assignments/{assignment_id}/transition")
    def transition_assignment(
        assignment_id: str,
        body: AssignmentTransitionBody,
        request: Request,
        conn: Annotated[psycopg.Connection, Depends(get_conn)],
    ) -> dict:
        principal = principal_of(request)
        assignment_id = _uuid_or(
            assignment_id, "unknown_assignment", "There is no such assignment."
        )
        try:
            dispatch.advance_assignment(
                conn, assignment_id, body.to_status, principal.username, body.note
            )
        except repo.NotFound:
            audit(
                conn,
                principal,
                "assignment.transition",
                "emergency_assignment",
                assignment_id,
                "failed",
                to_status=body.to_status,
                reason="unknown assignment",
            )
            problem(404, "unknown_assignment", "There is no such assignment.")
        except repo.InvalidTransition as exc:
            audit(
                conn,
                principal,
                "assignment.transition",
                "emergency_assignment",
                assignment_id,
                "denied",
                to_status=body.to_status,
                reason=str(exc),
            )
            problem(409, "invalid_transition", str(exc))
        audit(
            conn,
            principal,
            "assignment.transition",
            "emergency_assignment",
            assignment_id,
            "allowed",
            to_status=body.to_status,
            note=body.note,
        )
        return {"assignment_id": assignment_id, "status": body.to_status}

    @app.post("/api/v1/emergency/assignments/{assignment_id}/route")
    def choose_route(
        assignment_id: str,
        body: RouteBody,
        request: Request,
        conn: Annotated[psycopg.Connection, Depends(get_conn)],
    ) -> dict:
        principal = principal_of(request)
        assignment_id = _uuid_or(
            assignment_id, "unknown_assignment", "There is no such assignment."
        )
        try:
            result = dispatch.select_route(conn, assignment_id, body.route_id, principal.username)
        except repo.NotFound:
            audit(
                conn,
                principal,
                "assignment.route",
                "emergency_assignment",
                assignment_id,
                "failed",
                route_id=body.route_id,
                reason="unknown assignment",
            )
            problem(404, "unknown_assignment", "There is no such assignment.")
        except dispatch.DispatchError as exc:
            audit(
                conn,
                principal,
                "assignment.route",
                "emergency_assignment",
                assignment_id,
                "denied",
                route_id=body.route_id,
                reason=exc.message,
            )
            problem(409 if exc.code == "assignment_closed" else 422, exc.code, exc.message)
        audit(
            conn,
            principal,
            "assignment.route",
            "emergency_assignment",
            assignment_id,
            "allowed",
            route_id=body.route_id,
        )
        return result
