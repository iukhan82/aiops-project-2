"""P08.07: the incident actions an operator takes - move it along its lifecycle, give it an owner, leave a note.

Each action is attributed to the authenticated person and audited. A transition is refused, with what the incident actually
is now, if the caller's view of it is out of date (`expected_status`), so two people working one incident never silently
overwrite each other. Resolving needs a written note. The lifecycle itself is `repositories/incidents.py`'s state machine.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

import psycopg
from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.api.db import get_conn
from backend.api.workflow import audit, principal_of, problem
from backend.repositories import incidents as repo

OWNER_ROLES = ("BACKEND", "OPS", "EMERG", "CONTROL", "SEC")
Status = Literal["open", "acknowledged", "investigating", "escalated", "resolved", "reopened"]


class TransitionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_status: Status
    note: str | None = Field(default=None, max_length=2000)
    expected_status: Status | None = None


class OwnerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner_role: Literal["BACKEND", "OPS", "EMERG", "CONTROL", "SEC"]


class NoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(min_length=1, max_length=2000)


def _uuid_or_404(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        problem(404, "unknown_incident", "There is no such incident.")


def register(app: FastAPI) -> None:
    @app.post("/api/v1/incidents/{incident_id}/transition")
    def transition_incident(
        incident_id: str,
        body: TransitionBody,
        request: Request,
        conn: Annotated[psycopg.Connection, Depends(get_conn)],
    ) -> dict:
        principal = principal_of(request)
        incident_id = _uuid_or_404(incident_id)
        current = repo.get_incident(conn, incident_id)
        if current is None:
            audit(
                conn,
                principal,
                "incident.transition",
                "incident",
                incident_id,
                "failed",
                to_status=body.to_status,
                reason="unknown incident",
            )
            problem(404, "unknown_incident", "There is no such incident.")
        if body.to_status == "resolved" and not (body.note and body.note.strip()):
            audit(
                conn,
                principal,
                "incident.transition",
                "incident",
                incident_id,
                "denied",
                to_status=body.to_status,
                reason="a resolution needs a note",
            )
            problem(
                422,
                "note_required",
                "Resolving an incident needs a note saying why it is resolved.",
            )
        if body.expected_status is not None and body.expected_status != current["status"]:
            audit(
                conn,
                principal,
                "incident.transition",
                "incident",
                incident_id,
                "denied",
                to_status=body.to_status,
                reason="status changed",
                current=current["status"],
                expected=body.expected_status,
            )
            problem(
                409,
                "status_changed",
                f"This incident is now {current['status']}, not {body.expected_status}. Nothing was changed.",
                current_status=current["status"],
                expected_status=body.expected_status,
            )
        try:
            repo.transition_incident(
                conn,
                incident_id,
                body.to_status,
                principal.username,
                body.note.strip() if body.note else None,
            )
        except repo.InvalidTransition as exc:
            audit(
                conn,
                principal,
                "incident.transition",
                "incident",
                incident_id,
                "denied",
                to_status=body.to_status,
                reason=str(exc),
                current=current["status"],
            )
            problem(
                409,
                "invalid_transition",
                str(exc),
                current_status=current["status"],
                allowed=sorted(repo.ALLOWED_TRANSITIONS.get(current["status"], set())),
            )
        audit(
            conn,
            principal,
            "incident.transition",
            "incident",
            incident_id,
            "allowed",
            from_status=current["status"],
            to_status=body.to_status,
            note=body.note,
        )
        updated = repo.get_incident(conn, incident_id)
        return {
            "incident_id": incident_id,
            "status": updated["status"],
            "previous_status": current["status"],
            "updated_at": updated["updated_at"].isoformat(),
        }

    @app.post("/api/v1/incidents/{incident_id}/owner")
    def set_owner(
        incident_id: str,
        body: OwnerBody,
        request: Request,
        conn: Annotated[psycopg.Connection, Depends(get_conn)],
    ) -> dict:
        principal = principal_of(request)
        incident_id = _uuid_or_404(incident_id)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT owner_role FROM incidents WHERE incident_id = %s FOR UPDATE", (incident_id,)
            )
            row = cur.fetchone()
            if row is None:
                conn.rollback()
                audit(
                    conn,
                    principal,
                    "incident.owner",
                    "incident",
                    incident_id,
                    "failed",
                    owner_role=body.owner_role,
                    reason="unknown incident",
                )
                problem(404, "unknown_incident", "There is no such incident.")
            previous = row[0]
            cur.execute(
                "UPDATE incidents SET owner_role = %s, updated_at = now() WHERE incident_id = %s",
                (body.owner_role, incident_id),
            )
            cur.execute(
                "INSERT INTO incident_notes (incident_id, author, author_roles, note) VALUES (%s, %s, %s, %s)",
                (
                    incident_id,
                    principal.username,
                    list(principal.roles),
                    f"Owner changed from {previous or 'unassigned'} to {body.owner_role}.",
                ),
            )
        conn.commit()
        audit(
            conn,
            principal,
            "incident.owner",
            "incident",
            incident_id,
            "allowed",
            from_owner=previous,
            to_owner=body.owner_role,
        )
        return {
            "incident_id": incident_id,
            "owner_role": body.owner_role,
            "previous_owner_role": previous,
        }

    @app.post("/api/v1/incidents/{incident_id}/notes", status_code=201)
    def add_note(
        incident_id: str,
        body: NoteBody,
        request: Request,
        conn: Annotated[psycopg.Connection, Depends(get_conn)],
    ) -> dict:
        principal = principal_of(request)
        incident_id = _uuid_or_404(incident_id)
        text = body.note.strip()
        if not text:
            problem(422, "note_required", "A note cannot be empty.")
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM incidents WHERE incident_id = %s", (incident_id,))
            if cur.fetchone() is None:
                audit(
                    conn,
                    principal,
                    "incident.note",
                    "incident",
                    incident_id,
                    "failed",
                    reason="unknown incident",
                )
                problem(404, "unknown_incident", "There is no such incident.")
            cur.execute(
                "INSERT INTO incident_notes (incident_id, author, author_roles, note) VALUES (%s, %s, %s, %s) RETURNING note_id, created_at",
                (incident_id, principal.username, list(principal.roles), text),
            )
            note_id, created_at = cur.fetchone()
        conn.commit()
        audit(conn, principal, "incident.note", "incident", incident_id, "allowed", note_id=note_id)
        return {
            "note_id": note_id,
            "incident_id": incident_id,
            "author": principal.username,
            "author_roles": list(principal.roles),
            "note": text,
            "created_at": created_at.isoformat(),
        }
