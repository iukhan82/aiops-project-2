"""P08.07 - P08.09: helpers shared by the endpoints that change something.

Every change an operator makes through the API is attributed to the authenticated person (never to a name the client sent)
and leaves a row in the append-only `operator_audit` table, whether it was accepted, refused by a business rule, or failed.
"""

from __future__ import annotations

from typing import NoReturn

import psycopg
from fastapi import HTTPException, Request
from psycopg.types.json import Jsonb

from backend.api.auth import Principal
from backend.redaction import redact


def principal_of(request: Request) -> Principal:
    """The identity `enforce` verified for this request."""
    return request.state.principal


def problem(status: int, code: str, message: str, **extra: object) -> NoReturn:
    """A refusal in the same shape the authorisation layer uses: `{"detail": {"error", "message", ...}}`."""
    raise HTTPException(status, detail={"error": code, "message": message, **extra})


def audit(
    conn: psycopg.Connection,
    principal: Principal,
    action: str,
    entity_type: str,
    entity_id: str | None,
    outcome: str,
    **detail: object,
) -> None:
    """Append one audit row. Committed on its own so a refused or failed action is still recorded. `detail` is redacted
    (CTL-16) before it is written - a caller cannot put a secret into the trail by passing one through here, and the
    database refuses one that gets past this anyway (migration 0026)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_audit (actor, actor_roles, action, entity_type, entity_id, outcome, detail) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                principal.username,
                list(principal.roles),
                action,
                entity_type,
                entity_id,
                outcome,
                Jsonb(redact(detail, mode="audit")),
            ),
        )
    conn.commit()
