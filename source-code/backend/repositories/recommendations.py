"""P07.04: `contracts/recommendation/v1` persistence. A recommendation has no
approval/execution state machine of its own here - P07.05 owns that, on the
separate `commands` table its policy creates from an approved recommendation.
This repository only tracks a recommendation's own short life: `proposed` ->
`superseded` (a fresher one replaces it for the same trigger) or `expired`
(nobody acted before `expires_at`) - `requested` is set by P07.05 when an
operator asks to execute one of its alternatives.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb


def create_recommendation(
    conn: psycopg.Connection,
    action_type: str,
    alternatives: list[dict],
    safety_bounds: dict,
    constraints: list[str],
    generated_at: datetime,
    expires_at: datetime,
    trigger_incident_id: str | None = None,
    trigger_emergency_call_id: str | None = None,
    recommendation_id: str | None = None,
) -> str:
    if not alternatives:
        raise ValueError("alternatives must be non-empty")
    if expires_at <= generated_at:
        raise ValueError("expires_at must be after generated_at")
    recommendation_id = recommendation_id or str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO recommendations (recommendation_id, trigger_incident_id, trigger_emergency_call_id, action_type, generated_at,
                expires_at, status, alternatives, safety_bounds, constraints)
            VALUES (%s, %s, %s, %s, %s, %s, 'proposed', %s::jsonb, %s::jsonb, %s)
            """,
            (
                recommendation_id,
                trigger_incident_id,
                trigger_emergency_call_id,
                action_type,
                generated_at,
                expires_at,
                Jsonb(alternatives),
                Jsonb(safety_bounds),
                constraints,
            ),
        )
    conn.commit()
    return recommendation_id


def supersede(conn: psycopg.Connection, recommendation_id: str, superseded_by: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE recommendations SET status = 'superseded', superseded_by = %s WHERE recommendation_id = %s AND status = 'proposed'",
            (superseded_by, recommendation_id),
        )
    conn.commit()


def expire_stale(conn: psycopg.Connection, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE recommendations SET status = 'expired' WHERE status = 'proposed' AND expires_at <= %s",
            (now,),
        )
        return cur.rowcount


def mark_requested(conn: psycopg.Connection, recommendation_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE recommendations SET status = 'requested' WHERE recommendation_id = %s AND status = 'proposed'",
            (recommendation_id,),
        )
        if cur.rowcount == 0:
            raise ValueError(f"{recommendation_id} is not in 'proposed' status")
    conn.commit()


def get(conn: psycopg.Connection, recommendation_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT recommendation_id, trigger_incident_id, trigger_emergency_call_id, action_type, generated_at, expires_at, status, "
            "alternatives, safety_bounds, constraints, superseded_by FROM recommendations WHERE recommendation_id = %s",
            (recommendation_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    cols = [
        "recommendation_id",
        "trigger_incident_id",
        "trigger_emergency_call_id",
        "action_type",
        "generated_at",
        "expires_at",
        "status",
        "alternatives",
        "safety_bounds",
        "constraints",
        "superseded_by",
    ]
    return dict(zip(cols, row, strict=True))


def active_for_trigger(conn: psycopg.Connection, trigger_incident_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT recommendation_id FROM recommendations WHERE trigger_incident_id = %s AND status = 'proposed'",
            (trigger_incident_id,),
        )
        ids = [str(r[0]) for r in cur.fetchall()]
    return [get(conn, i) for i in ids]
