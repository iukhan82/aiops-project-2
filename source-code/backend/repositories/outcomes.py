"""P07.09: persistence for `contracts/outcome/v1` (independent outcome
verification of one executed command).

The contract demands at least one numeric measurement per window. When a
window genuinely has no measurement (the classification is then `unknown`),
the window is recorded with a zero-valued `<metric>_sample_count` measurement
instead of an invented value: it is true (no samples were found), it satisfies
the contract, and it leaves the gap visible in the stored record.

Two guards live here as well as in `backend/control/outcome_verification.py`,
because this is the last place a record can be refused before it is stored:
the verifier must differ from the command's requester, approver and executor
(P07.05's four-eyes reasoning, extended to verification), and an outcome can
only exist for a command that actually reached `executed` or `rolled_back`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb

CLASSIFICATIONS = ("effective", "ineffective", "unsafe", "unknown")
VERIFIABLE_STATUSES = ("executed", "rolled_back")
SCHEMA_VERSION = "1.0.0"


class OutcomeError(Exception):
    pass


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _measurement_records(measurements: list) -> list[dict]:
    """Metric-like objects (`name`, `value`, `unit`) become contract measurements; a missing value becomes an
    honest zero-sample count rather than a made-up number."""
    records = []
    for m in measurements:
        if m.value is None:
            records.append({"name": f"{m.name}_sample_count", "value": 0, "unit": "samples"})
        else:
            records.append({"name": m.name, "value": float(m.value), "unit": m.unit})
    return records


def executors_of(conn: psycopg.Connection, command_id: str) -> set[str]:
    """Everyone who moved the command to `executing` or `executed` - the executor identity the
    verifier must also differ from."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT changed_by FROM command_transitions WHERE command_id = %s AND to_status IN ('executing', 'executed')",
            (command_id,),
        )
        return {r[0] for r in cur.fetchall()}


def record_outcome(
    conn: psycopg.Connection,
    command_id: str,
    pre_window: tuple[datetime, datetime],
    post_window: tuple[datetime, datetime],
    pre_measurements: list,
    post_measurements: list,
    classification: str,
    verifier: str,
    verified_at: datetime,
    rollback_triggered: bool,
    escalation_reason: str | None = None,
    detail: dict | None = None,
    outcome_id: str | None = None,
) -> str:
    if classification not in CLASSIFICATIONS:
        raise OutcomeError(f"unrecognized classification {classification!r}")
    if not pre_measurements or not post_measurements:
        raise OutcomeError("each window needs at least one measurement")
    if pre_window[0] >= pre_window[1] or post_window[0] >= post_window[1]:
        raise OutcomeError("a window must end after it starts")
    if pre_window[1] > post_window[0]:
        raise OutcomeError("the pre window must end before the post window starts")
    if verified_at < post_window[1]:
        raise OutcomeError("an outcome cannot be verified before its post window has closed")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, requested_by, approved_by FROM commands WHERE command_id = %s",
            (command_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise OutcomeError(f"unknown command {command_id}")
    status, requested_by, approved_by = row
    if status not in VERIFIABLE_STATUSES:
        raise OutcomeError(f"command is {status!r}; only {VERIFIABLE_STATUSES} can be verified")
    barred = {requested_by, approved_by} | executors_of(conn, command_id)
    if verifier in barred:
        raise OutcomeError(
            f"verifier {verifier!r} is not independent of the command's requester/approver/executor"
        )

    body = {
        "pre_measurements": _measurement_records(pre_measurements),
        "post_measurements": _measurement_records(post_measurements),
        "escalation_reason": escalation_reason,
        **(detail or {}),
    }
    outcome_id = outcome_id or str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO command_outcomes (outcome_id, command_id, pre_window_start, pre_window_end, post_window_start, post_window_end, "
            "classification, verified_at, verifier, rollback_triggered, detail) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                outcome_id,
                command_id,
                pre_window[0],
                pre_window[1],
                post_window[0],
                post_window[1],
                classification,
                verified_at,
                verifier,
                rollback_triggered,
                Jsonb(body),
            ),
        )
    conn.commit()
    return outcome_id


_COLUMNS = (
    "outcome_id, command_id, pre_window_start, pre_window_end, post_window_start, post_window_end, classification, verified_at, "
    "verifier, rollback_triggered, detail"
)


def to_contract(row: tuple) -> dict:
    """A stored row as `contracts/outcome/v1` (plus nothing else: `detail` stays in the database and is
    returned separately by `get_outcome_detail`)."""
    oid, cid, p0, p1, q0, q1, cls, verified_at, verifier, rolled, detail = row
    detail = detail or {}
    record = {
        "schema_version": SCHEMA_VERSION,
        "outcome_id": str(oid),
        "command_id": str(cid),
        "pre_window": {
            "from": _iso_z(p0),
            "to": _iso_z(p1),
            "measurements": detail.get("pre_measurements", []),
        },
        "post_window": {
            "from": _iso_z(q0),
            "to": _iso_z(q1),
            "measurements": detail.get("post_measurements", []),
        },
        "classification": cls,
        "verified_at": _iso_z(verified_at),
        "verifier": verifier,
        "rollback_triggered": rolled,
    }
    if detail.get("escalation_reason"):
        record["escalation_reason"] = detail["escalation_reason"]
    return record


def get_outcome(conn: psycopg.Connection, outcome_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM command_outcomes WHERE outcome_id = %s", (outcome_id,))
        row = cur.fetchone()
    return to_contract(row) if row else None


def get_outcome_detail(conn: psycopg.Connection, outcome_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT detail FROM command_outcomes WHERE outcome_id = %s", (outcome_id,))
        row = cur.fetchone()
    return row[0] if row else None


def outcomes_for_command(conn: psycopg.Connection, command_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM command_outcomes WHERE command_id = %s ORDER BY verified_at DESC, outcome_id::text DESC",
            (command_id,),
        )
        return [to_contract(r) for r in cur.fetchall()]
