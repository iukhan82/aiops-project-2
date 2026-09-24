"""P05.08: incident repository - explicit state machine, row-locked
concurrency safety, append-only audit trail.

Every transition takes a row lock (`SELECT ... FOR UPDATE`) before checking
whether it is legal, so two concurrent transition attempts on the same
incident serialize rather than racing: the second transaction sees the
*post*-first-transition status once it acquires the lock, and is validated
(and possibly rejected) against that current status, not a stale read taken
before the first transition committed. This is what prevents a lost update,
not merely "the database enforces referential integrity."
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import psycopg

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "open": {"acknowledged", "investigating", "escalated", "resolved"},
    "acknowledged": {"investigating", "escalated", "resolved"},
    "investigating": {"escalated", "resolved"},
    "escalated": {"investigating", "resolved"},
    "resolved": {"reopened"},
    "reopened": {"acknowledged", "investigating", "escalated", "resolved"},
}


class TransitionError(Exception):
    pass


class InvalidTransition(TransitionError):
    pass


class IncidentNotFound(TransitionError):
    pass


def create_incident(
    conn: psycopg.Connection,
    incident_type: str,
    severity: str,
    network_element_type: str,
    network_element_id: str,
    geometry_version: str,
    evidence_event_ids: list[str],
    owner_role: str,
    confidence: float,
    changed_by: str,
    at: datetime | None = None,
) -> str:
    """`at` is the time the incident is considered opened (defaults to now); a
    replayed or simulated timeline passes the evidence's own time."""
    incident_id = str(uuid.uuid4())
    now = at or datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO incidents (
                incident_id, incident_type, severity, status, network_element_type,
                network_element_id, geometry_version, opened_at, updated_at,
                evidence_event_ids, owner_role, confidence
            ) VALUES (%s, %s, %s, 'open', %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                incident_id,
                incident_type,
                severity,
                network_element_type,
                network_element_id,
                geometry_version,
                now,
                now,
                evidence_event_ids,
                owner_role,
                confidence,
            ),
        )
        cur.execute(
            "INSERT INTO incident_transitions (incident_id, from_status, to_status, changed_by, note, changed_at) "
            "VALUES (%s, NULL, 'open', %s, 'created', %s)",
            (incident_id, changed_by, now),
        )
    conn.commit()
    return incident_id


def transition_incident(
    conn: psycopg.Connection,
    incident_id: str,
    to_status: str,
    changed_by: str,
    note: str | None = None,
    at: datetime | None = None,
) -> None:
    """Raises IncidentNotFound / InvalidTransition and leaves the row
    untouched (caller's transaction is rolled back) rather than applying a
    partial or illegal change. `at` is the transition time (defaults to now)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM incidents WHERE incident_id = %s FOR UPDATE", (incident_id,)
        )
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            raise IncidentNotFound(incident_id)
        (from_status,) = row

        if to_status not in ALLOWED_TRANSITIONS.get(from_status, set()):
            conn.rollback()
            raise InvalidTransition(f"{from_status} -> {to_status} is not allowed")

        now = at or datetime.now(timezone.utc)
        if to_status == "resolved":
            cur.execute(
                "UPDATE incidents SET status = %s, updated_at = GREATEST(updated_at, %s), resolved_at = %s WHERE incident_id = %s",
                (to_status, now, now, incident_id),
            )
        else:
            cur.execute(
                "UPDATE incidents SET status = %s, updated_at = GREATEST(updated_at, %s) WHERE incident_id = %s",
                (to_status, now, incident_id),
            )
        cur.execute(
            "INSERT INTO incident_transitions (incident_id, from_status, to_status, changed_by, note, changed_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (incident_id, from_status, to_status, changed_by, note, now),
        )
    conn.commit()


def get_incident(conn: psycopg.Connection, incident_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT incident_id, status, severity, incident_type, owner_role, updated_at, resolved_at "
            "FROM incidents WHERE incident_id = %s",
            (incident_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    cols = [
        "incident_id",
        "status",
        "severity",
        "incident_type",
        "owner_role",
        "updated_at",
        "resolved_at",
    ]
    return dict(zip(cols, row, strict=True))


def transition_history(conn: psycopg.Connection, incident_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT from_status, to_status, changed_by, note, changed_at FROM incident_transitions "
            "WHERE incident_id = %s ORDER BY changed_at ASC, id ASC",
            (incident_id,),
        )
        rows = cur.fetchall()
    cols = ["from_status", "to_status", "changed_by", "note", "changed_at"]
    return [dict(zip(cols, r, strict=True)) for r in rows]
