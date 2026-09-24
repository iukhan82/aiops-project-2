"""P07.01: emergency call and unit-assignment state machines.

Same discipline as P05.08's incidents: an explicit transition table per
entity, a row lock before validating, append-only audit trail. Two state
machines because a call and its assignment(s) are genuinely different
things with different lifecycles (`contracts/emergency-call/v1` /
`emergency-unit-assignment/v1`) - a call can have more than one unit
(fire + ambulance on the same collision), and an assignment can go
`unavailable` (a unit reassigned or cancelled) without the call itself
being cancelled.

Every write takes an explicit `at`: a live dispatch loop passes wall-clock
time, `cad_avl_adapter.py`'s replay passes the CAD record's own timestamp,
so the same code drives both a live feed and a historical/simulated load.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb

CALL_TRANSITIONS: dict[str, set[str]] = {
    "received": {"dispatched", "cancelled"},
    "dispatched": {"unit_assigned", "cancelled"},
    "unit_assigned": {"en_route", "cancelled"},
    "en_route": {"on_scene", "cancelled"},
    "on_scene": {"cleared"},
    "cleared": set(),
    "cancelled": set(),
}
ASSIGNMENT_TRANSITIONS: dict[str, set[str]] = {
    "assigned": {"acknowledged", "unavailable"},
    "acknowledged": {"en_route", "unavailable"},
    "en_route": {"staged", "on_scene", "unavailable"},
    "staged": {"on_scene", "unavailable"},
    "on_scene": {"clear", "unavailable"},
    "clear": set(),
    "unavailable": set(),
}
STATUS_TIMESTAMP_COLUMN = {
    "acknowledged": "acknowledged_at",
    "on_scene": "arrived_at",
    "clear": "cleared_at",
}


class TransitionError(Exception):
    pass


class InvalidTransition(TransitionError):
    pass


class NotFound(TransitionError):
    pass


def _now(at: datetime | None) -> datetime:
    return at or datetime.now(timezone.utc)


def create_call(
    conn: psycopg.Connection,
    call_type: str,
    call_subtype: str,
    priority: str,
    location: dict,
    geometry_version: str,
    source_reliability: str,
    truth_label: str,
    changed_by: str,
    at: datetime | None = None,
    call_id: str | None = None,
) -> str:
    call_id = call_id or str(uuid.uuid4())
    now = _now(at)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO emergency_calls (call_id, call_type, call_subtype, priority, location, geometry_version, reported_at,
                source_reliability, status, truth_label)
            VALUES (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s, %s, 'received', %s)
            """,
            (
                call_id,
                call_type,
                call_subtype,
                priority,
                location["longitude"],
                location["latitude"],
                geometry_version,
                now,
                source_reliability,
                truth_label,
            ),
        )
        cur.execute(
            "INSERT INTO emergency_call_transitions (call_id, from_status, to_status, changed_by, note, changed_at) "
            "VALUES (%s, NULL, 'received', %s, 'call received', %s)",
            (call_id, changed_by, now),
        )
    conn.commit()
    return call_id


def transition_call(
    conn: psycopg.Connection,
    call_id: str,
    to_status: str,
    changed_by: str,
    note: str | None = None,
    at: datetime | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM emergency_calls WHERE call_id = %s FOR UPDATE", (call_id,))
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            raise NotFound(call_id)
        (from_status,) = row
        if to_status not in CALL_TRANSITIONS.get(from_status, set()):
            conn.rollback()
            raise InvalidTransition(f"call {from_status} -> {to_status} is not allowed")
        now = _now(at)
        cur.execute(
            "UPDATE emergency_calls SET status = %s WHERE call_id = %s", (to_status, call_id)
        )
        cur.execute(
            "INSERT INTO emergency_call_transitions (call_id, from_status, to_status, changed_by, note, changed_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (call_id, from_status, to_status, changed_by, note, now),
        )
    conn.commit()


def create_assignment(
    conn: psycopg.Connection,
    call_id: str,
    unit_id: str,
    agency: str,
    capability: list[str],
    route_alternatives: list[dict],
    truth_label: str,
    changed_by: str,
    at: datetime | None = None,
    assignment_id: str | None = None,
) -> str:
    if not route_alternatives:
        raise ValueError("route_alternatives must be non-empty")
    assignment_id = assignment_id or str(uuid.uuid4())
    now = _now(at)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO emergency_unit_assignments (assignment_id, call_id, unit_id, agency, capability, status, assigned_at,
                route_alternatives, truth_label)
            VALUES (%s, %s, %s, %s, %s, 'assigned', %s, %s::jsonb, %s)
            """,
            (
                assignment_id,
                call_id,
                unit_id,
                agency,
                capability,
                now,
                Jsonb(route_alternatives),
                truth_label,
            ),
        )
        cur.execute(
            "INSERT INTO emergency_assignment_transitions (assignment_id, from_status, to_status, changed_by, note, changed_at) "
            "VALUES (%s, NULL, 'assigned', %s, %s, %s)",
            (assignment_id, changed_by, f"unit {unit_id} assigned", now),
        )
    conn.commit()
    return assignment_id


def transition_assignment(
    conn: psycopg.Connection,
    assignment_id: str,
    to_status: str,
    changed_by: str,
    note: str | None = None,
    at: datetime | None = None,
    handover: dict | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM emergency_unit_assignments WHERE assignment_id = %s FOR UPDATE",
            (assignment_id,),
        )
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            raise NotFound(assignment_id)
        (from_status,) = row
        if to_status not in ASSIGNMENT_TRANSITIONS.get(from_status, set()):
            conn.rollback()
            raise InvalidTransition(f"assignment {from_status} -> {to_status} is not allowed")
        now = _now(at)
        column = STATUS_TIMESTAMP_COLUMN.get(to_status)
        sets, params = ["status = %s"], [to_status]
        if column:
            sets.append(f"{column} = %s")
            params.append(now)
        if handover is not None:
            sets.append("handover = %s::jsonb")
            params.append(Jsonb(handover))
        params.append(assignment_id)
        cur.execute(
            f"UPDATE emergency_unit_assignments SET {', '.join(sets)} WHERE assignment_id = %s",
            params,
        )
        cur.execute(
            "INSERT INTO emergency_assignment_transitions (assignment_id, from_status, to_status, changed_by, note, changed_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (assignment_id, from_status, to_status, changed_by, note, now),
        )
    conn.commit()


def get_call(conn: psycopg.Connection, call_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT call_id, call_type, call_subtype, priority, ST_Y(location::geometry), ST_X(location::geometry), geometry_version, "
            "reported_at, source_reliability, status, truth_label FROM emergency_calls WHERE call_id = %s",
            (call_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    cols = [
        "call_id",
        "call_type",
        "call_subtype",
        "priority",
        "latitude",
        "longitude",
        "geometry_version",
        "reported_at",
        "source_reliability",
        "status",
        "truth_label",
    ]
    return dict(zip(cols, row, strict=True))


def assignments_for_call(conn: psycopg.Connection, call_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT assignment_id, unit_id, agency, capability, status, assigned_at, acknowledged_at, arrived_at, cleared_at, "
            "route_alternatives, handover, truth_label FROM emergency_unit_assignments WHERE call_id = %s ORDER BY assigned_at",
            (call_id,),
        )
        rows = cur.fetchall()
    cols = [
        "assignment_id",
        "unit_id",
        "agency",
        "capability",
        "status",
        "assigned_at",
        "acknowledged_at",
        "arrived_at",
        "cleared_at",
        "route_alternatives",
        "handover",
        "truth_label",
    ]
    return [dict(zip(cols, r, strict=True)) for r in rows]


def call_transition_history(conn: psycopg.Connection, call_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT from_status, to_status, changed_by, note, changed_at FROM emergency_call_transitions "
            "WHERE call_id = %s ORDER BY changed_at, id",
            (call_id,),
        )
        rows = cur.fetchall()
    cols = ["from_status", "to_status", "changed_by", "note", "changed_at"]
    return [dict(zip(cols, r, strict=True)) for r in rows]


def assignment_transition_history(conn: psycopg.Connection, assignment_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT from_status, to_status, changed_by, note, changed_at FROM emergency_assignment_transitions "
            "WHERE assignment_id = %s ORDER BY changed_at, id",
            (assignment_id,),
        )
        rows = cur.fetchall()
    cols = ["from_status", "to_status", "changed_by", "note", "changed_at"]
    return [dict(zip(cols, r, strict=True)) for r in rows]
