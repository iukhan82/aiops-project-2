"""P08.09: the governance screens' data - the audit trail, platform status, and shift handover.

* `GET /api/v1/audit`        one read-only, newest-first trail over everything a person or a service did: the operator actions the
                             API accepted, refused or failed (`operator_audit`) and every state change of an incident, command, call
                             and assignment with who made it. Filter by actor, entity and time. Nothing here can change a row; the
                             tables refuse UPDATE and DELETE (operator_audit by trigger; the transition tables are only ever inserted).
* `GET /api/v1/ops/status`   health from probes made now, not from a stored "ok": the database, the identity provider, the message
                             brokers (a TCP connection - open port, not message flow), each worker's heartbeat, data freshness per
                             source, and ingestion measured from what actually arrived. A probe that has no answer is `unknown`; nothing
                             is filled in.
* `/api/v1/handovers`        a structured note from the outgoing shift to the incoming one, with the open items it hands over; the
                             incoming person acknowledges it by name (never the author, and only once).
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal

import httpx
import psycopg
from fastapi import Depends, FastAPI, Query, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from backend import pdp
from backend.api.auth import JWKS_URL
from backend.api.cursor import decode_cursor, encode_cursor
from backend.api.db import get_conn
from backend.api.workflow import audit, principal_of, problem
from backend.redaction import redact

EXPORT_MAX_ROWS = 5000
EXPORT_MAX_SPAN_DAYS = 92

POLICY_DATA = Path(__file__).resolve().parents[2] / "policy" / "aiops" / "model" / "data.json"

# After this long without a reading a device is stale. The device-health screen and the map use the same numbers
# (`frontend/src/features/map/model.ts`); `verify_govern.py` fails if the two ever differ.
DEVICE_STALE_AFTER_S = {
    "inductive_loop": 120,
    "traffic_loop": 120,
    "cycle_counter": 120,
    "crossing_detector": 300,
    "signal_controller": 120,
    "edge_camera": 180,
    "weather_station": 600,
    "road_condition_sensor": 600,
    "emergency_cad_avl_adapter": 60,
}
DEFAULT_STALE_AFTER_S = 300
ANALYTICS_STALE_AFTER_S = 900
# A worker is healthy while its last heartbeat is younger than this, late (degraded) up to three times this, and down beyond that.
WORKERS = (
    ("demo-feeder", "Demo feeder (replays the recorded streams, runs the detectors)", 180.0),
    ("command-executor", "Command executor (the only service that executes a command)", 10.0),
    ("outcome-verifier", "Outcome verifier (measures what an executed command did)", 60.0),
)
INGESTION_WINDOW_MIN = 5
LAG_DEGRADED_S = 30.0

AUDIT_SOURCES = """
    SELECT at, 'operator' AS source, audit_id AS row_id, actor, actor_roles, action, entity_type, entity_id, outcome, detail FROM operator_audit
    UNION ALL
    SELECT changed_at, 'command_history', id, changed_by, NULL::text[], 'command moved ' || coalesce('from ' || from_status || ' ', '') || 'to ' || to_status,
           'command', command_id::text, 'allowed', jsonb_strip_nulls(jsonb_build_object('from_status', from_status, 'to_status', to_status, 'note', note)) FROM command_transitions
    UNION ALL
    SELECT changed_at, 'incident_history', id, changed_by, NULL::text[], 'incident moved ' || coalesce('from ' || from_status || ' ', '') || 'to ' || to_status,
           'incident', incident_id::text, 'allowed', jsonb_strip_nulls(jsonb_build_object('from_status', from_status, 'to_status', to_status, 'note', note)) FROM incident_transitions
    UNION ALL
    SELECT changed_at, 'call_history', id, changed_by, NULL::text[], 'call moved ' || coalesce('from ' || from_status || ' ', '') || 'to ' || to_status,
           'emergency_call', call_id::text, 'allowed', jsonb_strip_nulls(jsonb_build_object('from_status', from_status, 'to_status', to_status, 'note', note)) FROM emergency_call_transitions
    UNION ALL
    SELECT changed_at, 'assignment_history', id, changed_by, NULL::text[], 'assignment moved ' || coalesce('from ' || from_status || ' ', '') || 'to ' || to_status,
           'emergency_assignment', assignment_id::text, 'allowed', jsonb_strip_nulls(jsonb_build_object('from_status', from_status, 'to_status', to_status, 'note', note)) FROM emergency_assignment_transitions
"""
AUDIT_COLUMNS = (
    "at",
    "source",
    "row_id",
    "actor",
    "actor_roles",
    "action",
    "entity_type",
    "entity_id",
    "outcome",
    "detail",
)


def _iso(moment: datetime | None) -> str | None:
    return (
        moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        if moment
        else None
    )


def _moment(value: str | None, name: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        problem(422, "bad_time", f"{name} must be a date and time such as 2026-09-21T08:00:00Z.")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# ---------------------------------------------------------------- platform status
def _explain(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "it did not answer in the time allowed"
    if isinstance(exc, (ConnectionRefusedError, httpx.ConnectError)):
        return "the connection was refused"
    if isinstance(exc, psycopg.OperationalError):
        return "the database could not be reached"
    return f"the probe failed ({type(exc).__name__})"


def _probe(check_id: str, label: str, kind: str, run) -> dict:
    started = time.perf_counter()
    try:
        status, detail = run()
    except Exception as exc:  # noqa: BLE001 - a probe that fails is a finding, not a crash
        status, detail = "down", _explain(exc)
    return {
        "id": check_id,
        "label": label,
        "kind": kind,
        "status": status,
        "detail": detail,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "checked_at": _iso(datetime.now(timezone.utc)),
    }


def _identity_provider() -> tuple[str, str]:
    response = httpx.get(JWKS_URL, timeout=2.0)
    if response.status_code != 200 or not response.json().get("keys"):
        return "down", f"the realm's signing keys could not be read (HTTP {response.status_code})"
    return "healthy", "the realm's published signing keys were read"


def _policy_engine() -> tuple[str, str]:
    served = pdp.loaded_version()
    if served is None:
        return (
            "down",
            "the policy engine did not answer, or has no policy loaded; every protected request is refused meanwhile",
        )
    try:
        expected = json.loads(POLICY_DATA.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        expected = None
    if expected is not None and expected != served:
        return (
            "degraded",
            f"it serves policy version {served}, but the policy on disk is version {expected}; restart the policy engine to load it",
        )
    return "healthy", f"it is serving policy version {served}"


def _broker(env_name: str, default: str) -> tuple[str, str]:
    address = os.environ.get(env_name, default)
    host, _, port = address.rpartition(":")
    with socket.create_connection((host, int(port)), timeout=1.5):
        pass
    return (
        "healthy",
        f"{address} accepts connections. This shows the port is open, not that messages are flowing; see Ingestion.",
    )


def _database(conn: psycopg.Connection) -> tuple[str, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
    return "healthy", "answered a query"


def _workers(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT service, extract(epoch FROM now() - last_seen), last_seen, detail FROM service_heartbeats"
        )
        seen = {row[0]: row[1:] for row in cur.fetchall()}
    out = []
    for service, label, expected in WORKERS:
        checked = _iso(datetime.now(timezone.utc))
        if service not in seen:
            out.append(
                {
                    "id": service,
                    "label": label,
                    "kind": "worker",
                    "status": "unknown",
                    "detail": "it has never reported a heartbeat, so its state is not known",
                    "latency_ms": None,
                    "checked_at": checked,
                    "last_seen": None,
                    "age_s": None,
                }
            )
            continue
        age, last_seen, detail = seen[service]
        age = float(age)
        if age <= expected:
            status, note = "healthy", f"last heartbeat {age:.0f} s ago"
        elif age <= 3 * expected:
            status, note = (
                "degraded",
                f"last heartbeat {age:.0f} s ago; it reports at least every {expected:.0f} s",
            )
        else:
            status, note = (
                "down",
                f"no heartbeat for {age:.0f} s; it reports at least every {expected:.0f} s",
            )
        out.append(
            {
                "id": service,
                "label": label,
                "kind": "worker",
                "status": status,
                "detail": note,
                "latency_ms": None,
                "checked_at": checked,
                "last_seen": _iso(last_seen),
                "age_s": round(age, 1),
                "reported": detail,
            }
        )
    return out


def _source_freshness(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT d.device_type, extract(epoch FROM now() - last.observation_time), last.observation_time FROM devices d "
            "LEFT JOIN LATERAL (SELECT observation_time FROM observation_events e WHERE e.device_id = d.device_id ORDER BY observation_time DESC LIMIT 1) last ON TRUE "
            "WHERE d.status = 'active'"
        )
        rows = cur.fetchall()
    groups: dict[str, list[tuple]] = {}
    for device_type, age, observed in rows:
        groups.setdefault(device_type, []).append((None if age is None else float(age), observed))
    out = []
    for device_type in sorted(groups):
        budget = DEVICE_STALE_AFTER_S.get(device_type, DEFAULT_STALE_AFTER_S)
        ages = [a for a, _ in groups[device_type] if a is not None]
        newest = min(ages) if ages else None
        fresh = sum(1 for a in ages if a <= budget)
        newest_time = max((o for _, o in groups[device_type] if o is not None), default=None)
        out.append(
            {
                "id": device_type,
                "label": device_type,
                "devices": len(groups[device_type]),
                "reporting": fresh,
                "stale": len(ages) - fresh,
                "never": len(groups[device_type]) - len(ages),
                "newest_observation_time": _iso(newest_time),
                "age_s": None if newest is None else round(newest, 1),
                "budget_s": budget,
                "status": "unknown" if newest is None else "fresh" if newest <= budget else "stale",
            }
        )
    return out


def _analytics_freshness(conn: psycopg.Connection) -> list[dict]:
    queries = (
        (
            "kpis",
            "Corridor KPIs (newest closed window)",
            "SELECT max(window_start + make_interval(secs => window_seconds)) FROM corridor_kpis",
        ),
        ("forecasts", "Forecasts (newest prediction)", "SELECT max(predicted_at) FROM forecasts"),
        ("incidents", "Incidents (newest update)", "SELECT max(updated_at) FROM incidents"),
    )
    out = []
    for check_id, label, sql in queries:
        with conn.cursor() as cur:
            cur.execute(sql)
            (newest,) = cur.fetchone()
            age = None
            if newest is not None:
                cur.execute("SELECT extract(epoch FROM now() - %s::timestamptz)", (newest,))
                age = float(cur.fetchone()[0])
        # An incident can be quiet for hours without anything being wrong, so its age is shown but never called stale.
        status = (
            "unknown"
            if age is None
            else "fresh"
            if check_id == "incidents" or age <= ANALYTICS_STALE_AFTER_S
            else "stale"
        )
        out.append(
            {
                "id": check_id,
                "label": label,
                "newest_observation_time": _iso(newest),
                "age_s": None if age is None else round(age, 1),
                "budget_s": None if check_id == "incidents" else ANALYTICS_STALE_AFTER_S,
                "status": status,
            }
        )
    return out


def _ingestion(conn: psycopg.Connection) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM received_at - observation_time)), "
            "percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM received_at - observation_time)), max(received_at), extract(epoch FROM now() - max(received_at)) "
            "FROM observation_events WHERE received_at > now() - make_interval(mins => %s)",
            (INGESTION_WINDOW_MIN,),
        )
        events, p50, p95, newest, since = cur.fetchone()
        cur.execute(
            "SELECT reason, count(*) FROM ingestion_rejections WHERE rejected_at > now() - make_interval(mins => %s) GROUP BY reason ORDER BY reason",
            (INGESTION_WINDOW_MIN,),
        )
        rejected = {reason: count for reason, count in cur.fetchall()}
    if not events:
        status, note = (
            "unknown",
            f"no event arrived in the last {INGESTION_WINDOW_MIN} minutes, so no rate or lag can be measured",
        )
    elif p95 is not None and float(p95) > LAG_DEGRADED_S:
        status, note = (
            "degraded",
            f"95% of events took under {float(p95):.1f} s from observation to storage, which is over the {LAG_DEGRADED_S:.0f} s allowance",
        )
    else:
        status, note = (
            "healthy",
            f"{events} events stored in the last {INGESTION_WINDOW_MIN} minutes",
        )
    return {
        "window_minutes": INGESTION_WINDOW_MIN,
        "events": events,
        "lag_p50_s": None if p50 is None else round(float(p50), 2),
        "lag_p95_s": None if p95 is None else round(float(p95), 2),
        "newest_received_at": _iso(newest),
        "since_newest_s": None if since is None else round(float(since), 1),
        "rejected": rejected,
        "status": status,
        "detail": note,
    }


def platform_status(conn: psycopg.Connection) -> dict:
    with ThreadPoolExecutor(max_workers=4) as pool:
        policy = pool.submit(
            _probe, "policy-engine", "Policy engine (OPA)", "dependency", _policy_engine
        )
        identity = pool.submit(
            _probe,
            "identity-provider",
            "Identity provider (Keycloak)",
            "dependency",
            _identity_provider,
        )
        kafka = pool.submit(
            _probe,
            "kafka",
            "Event broker (Kafka)",
            "dependency",
            lambda: _broker("AIOPS_KAFKA_ADDR", "127.0.0.1:19092"),
        )
        mqtt = pool.submit(
            _probe,
            "mqtt",
            "Device broker (MQTT)",
            "dependency",
            lambda: _broker("AIOPS_MQTT_ADDR", "127.0.0.1:1883"),
        )
        database = _probe(
            "database", "Database (PostgreSQL)", "dependency", lambda: _database(conn)
        )
        services = [
            {
                "id": "api",
                "label": "Operator API",
                "kind": "service",
                "status": "healthy",
                "detail": "it is answering this request",
                "latency_ms": None,
                "checked_at": _iso(datetime.now(timezone.utc)),
            },
            database,
            identity.result(),
            policy.result(),
            kafka.result(),
            mqtt.result(),
            *_workers(conn),
        ]
    sources, analytics, ingestion = (
        _source_freshness(conn),
        _analytics_freshness(conn),
        _ingestion(conn),
    )
    counts = {"healthy": 0, "degraded": 0, "down": 0, "unknown": 0}
    for item in services:
        counts[item["status"]] += 1
    problems = (
        counts["degraded"]
        + counts["down"]
        + sum(1 for s in sources + analytics if s["status"] == "stale")
        + (1 if ingestion["status"] == "degraded" else 0)
    )
    overall = "degraded" if problems else "unknown" if counts["healthy"] == 0 else "healthy"
    return {
        "generated_at": _iso(datetime.now(timezone.utc)),
        "overall": overall,
        "counts": counts,
        "services": services,
        "sources": sources,
        "analytics": analytics,
        "ingestion": ingestion,
    }


# ---------------------------------------------------------------- handovers
class OpenItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["incident", "command", "call", "other"]
    ref: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=300)


class HandoverBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outgoing_shift: str = Field(min_length=1, max_length=60)
    incoming_shift: str = Field(min_length=1, max_length=60)
    summary: str = Field(min_length=1, max_length=4000)
    open_items: list[OpenItem] = Field(default_factory=list, max_length=20)


def _describe_item(conn: psycopg.Connection, item: OpenItem) -> dict:
    """What the item is, from the database as of now - a person hands over a reference, the server says what it refers to."""
    if item.kind == "other":
        if not (item.note and item.note.strip()):
            problem(
                422,
                "note_required",
                "An item that is not an incident, command or call needs a note saying what it is.",
            )
        return {
            "kind": "other",
            "ref": None,
            "label": item.note.strip(),
            "status": None,
            "note": None,
        }
    if not item.ref:
        problem(422, "ref_required", f"A {item.kind} item needs the {item.kind} it refers to.")
    queries = {
        "incident": "SELECT replace(incident_type, '_', ' ') || ' on ' || network_element_id, status FROM incidents WHERE incident_id = %s",
        "command": "SELECT replace(action_type, '_', ' ') || ' on ' || target_entity_id, status FROM commands WHERE command_id = %s",
        "call": "SELECT call_type || ' call (' || replace(call_subtype, '_', ' ') || '), ' || priority || ' priority', status FROM emergency_calls WHERE call_id = %s",
    }
    try:
        with conn.cursor() as cur:
            cur.execute(queries[item.kind], (item.ref,))
            row = cur.fetchone()
    except psycopg.errors.InvalidTextRepresentation:
        conn.rollback()
        row = None
    if row is None:
        problem(422, "unknown_item", f"There is no {item.kind} {item.ref!r}.")
    return {
        "kind": item.kind,
        "ref": item.ref,
        "label": row[0],
        "status": row[1],
        "note": item.note.strip() if item.note and item.note.strip() else None,
    }


HANDOVER_COLUMNS = "handover_id, created_at, author, author_roles, outgoing_shift, incoming_shift, summary, open_items, acknowledged_by, acknowledged_at"


def _handover(row: tuple) -> dict:
    out = dict(zip(HANDOVER_COLUMNS.split(", "), row, strict=True))
    out["handover_id"] = str(out["handover_id"])
    out["created_at"], out["acknowledged_at"] = (
        _iso(out["created_at"]),
        _iso(out["acknowledged_at"]),
    )
    out["author_roles"] = list(out["author_roles"])
    out["status"] = "acknowledged" if out["acknowledged_by"] else "awaiting_acknowledgement"
    return out


def register(app: FastAPI) -> None:
    @app.get("/api/v1/audit")
    def audit_trail(
        conn: Annotated[psycopg.Connection, Depends(get_conn)],
        actor: str | None = Query(default=None, max_length=80),
        entity_type: str | None = Query(default=None, max_length=40),
        entity_id: str | None = Query(default=None, max_length=80),
        outcome: Literal["allowed", "denied", "failed"] | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        cursor: str | None = None,
    ) -> dict:
        after, start, end = decode_cursor(cursor), _moment(since, "since"), _moment(until, "until")
        if start and end and start > end:
            problem(422, "bad_range", "The start of the time range is after its end.")
        where, params = [], []
        if actor and actor.strip():
            where.append("actor ILIKE %s ESCAPE '\\'")
            params.append(_like(actor.strip()))
        if entity_type:
            where.append("entity_type = %s")
            params.append(entity_type)
        if entity_id and entity_id.strip():
            where.append("entity_id = %s")
            params.append(entity_id.strip())
        if outcome:
            where.append("outcome = %s")
            params.append(outcome)
        if start:
            where.append("at >= %s")
            params.append(start)
        if end:
            where.append("at <= %s")
            params.append(end)
        if after:
            where.append("(at, source, row_id) < (%s, %s, %s)")
            params += [datetime.fromisoformat(after["at"]), after["source"], after["row_id"]]
        sql = f"SELECT {', '.join(AUDIT_COLUMNS)} FROM ({AUDIT_SOURCES}) trail {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY at DESC, source DESC, row_id DESC LIMIT %s"
        with conn.cursor() as cur:
            cur.execute(sql, [*params, limit + 1])
            rows = cur.fetchall()
        page = rows[:limit]
        items = []
        for row in page:
            record = dict(zip(AUDIT_COLUMNS, row, strict=True))
            items.append(
                {
                    "audit_id": f"{record['source']}:{record['row_id']}",
                    "at": _iso(record["at"]),
                    "source": record["source"],
                    "actor": record["actor"],
                    "actor_roles": list(record["actor_roles"]) if record["actor_roles"] else [],
                    "action": record["action"],
                    "entity_type": record["entity_type"],
                    "entity_id": record["entity_id"],
                    "outcome": record["outcome"],
                    "detail": record["detail"] or {},
                }
            )
        next_cursor = None
        if len(rows) > limit:
            last = page[-1]
            next_cursor = encode_cursor(
                {"at": last[0].isoformat(), "source": last[1], "row_id": last[2]}
            )
        return {"items": items, "next_cursor": next_cursor}

    @app.get("/api/v1/audit/export")
    def audit_export(
        request: Request,
        conn: Annotated[psycopg.Connection, Depends(get_conn)],
        since: str = Query(...),
        until: str = Query(...),
        actor: str | None = Query(default=None, max_length=80),
        entity_type: str | None = Query(default=None, max_length=40),
        entity_id: str | None = Query(default=None, max_length=80),
        outcome: Literal["allowed", "denied", "failed"] | None = None,
    ) -> dict:
        """CTL-17: a redacted, attributed evidence bundle over a bounded time range - `audit.export`
        (`auditor` only). `since`/`until` are required (an export with no bound is not an export, it is
        the whole trail) and bounded to `EXPORT_MAX_SPAN_DAYS`; the export itself is capped at
        `EXPORT_MAX_ROWS` and says so rather than silently truncating without comment. Locations, not just
        secrets, are redacted here (`mode="export"`) - the audit screen itself keeps them, because an
        auditor working a live incident needs to see what was acted on, but this bundle is meant to leave
        the platform. Every export is itself an audited, attributed action."""
        principal = principal_of(request)
        start, end = _moment(since, "since"), _moment(until, "until")
        if start > end:
            problem(422, "bad_range", "The start of the time range is after its end.")
        if end - start > timedelta(days=EXPORT_MAX_SPAN_DAYS):
            problem(
                422,
                "range_too_wide",
                f"An export covers at most {EXPORT_MAX_SPAN_DAYS} days; narrow the range and export more than once.",
            )
        where, params = ["at >= %s", "at <= %s"], [start, end]
        if actor and actor.strip():
            where.append("actor ILIKE %s ESCAPE '\\'")
            params.append(_like(actor.strip()))
        if entity_type:
            where.append("entity_type = %s")
            params.append(entity_type)
        if entity_id and entity_id.strip():
            where.append("entity_id = %s")
            params.append(entity_id.strip())
        if outcome:
            where.append("outcome = %s")
            params.append(outcome)
        sql = f"SELECT {', '.join(AUDIT_COLUMNS)} FROM ({AUDIT_SOURCES}) trail WHERE {' AND '.join(where)} ORDER BY at ASC, source ASC, row_id ASC LIMIT %s"
        with conn.cursor() as cur:
            cur.execute(sql, [*params, EXPORT_MAX_ROWS + 1])
            rows = cur.fetchall()
        truncated = len(rows) > EXPORT_MAX_ROWS
        page = rows[:EXPORT_MAX_ROWS]
        items = []
        for row in page:
            record = dict(zip(AUDIT_COLUMNS, row, strict=True))
            items.append(
                redact(
                    {
                        "audit_id": f"{record['source']}:{record['row_id']}",
                        "at": _iso(record["at"]),
                        "source": record["source"],
                        "actor": record["actor"],
                        "actor_roles": list(record["actor_roles"]) if record["actor_roles"] else [],
                        "action": record["action"],
                        "entity_type": record["entity_type"],
                        "entity_id": record["entity_id"],
                        "outcome": record["outcome"],
                        "detail": record["detail"] or {},
                    },
                    mode="export",
                )
            )
        exported_at = datetime.now(timezone.utc)
        audit(
            conn,
            principal,
            "audit.export",
            "audit",
            None,
            "allowed",
            since=since,
            until=until,
            actor_filter=actor,
            entity_type_filter=entity_type,
            entity_id_filter=entity_id,
            outcome_filter=outcome,
            row_count=len(items),
            truncated=truncated,
        )
        return {
            "exported_by": principal.username,
            "exported_at": _iso(exported_at),
            "filters": {
                "since": _iso(start),
                "until": _iso(end),
                "actor": actor,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "outcome": outcome,
            },
            "redaction": "export",
            "row_count": len(items),
            "truncated": truncated,
            "items": items,
        }

    @app.get("/api/v1/ops/status")
    def ops_status(conn: Annotated[psycopg.Connection, Depends(get_conn)]) -> dict:
        return platform_status(conn)

    @app.get("/api/v1/handovers")
    def list_handovers(
        conn: Annotated[psycopg.Connection, Depends(get_conn)],
        status: Literal["all", "awaiting_acknowledgement", "acknowledged"] = "all",
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = None,
    ) -> dict:
        after = decode_cursor(cursor)
        where, params = [], []
        if status != "all":
            where.append(
                "acknowledged_by IS NULL"
                if status == "awaiting_acknowledgement"
                else "acknowledged_by IS NOT NULL"
            )
        if after:
            where.append("(created_at, handover_id) < (%s, %s)")
            params += [datetime.fromisoformat(after["created_at"]), after["handover_id"]]
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {HANDOVER_COLUMNS} FROM shift_handovers {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY created_at DESC, handover_id DESC LIMIT %s",
                [*params, limit + 1],
            )
            rows = cur.fetchall()
        page = rows[:limit]
        next_cursor = (
            encode_cursor({"created_at": page[-1][1].isoformat(), "handover_id": str(page[-1][0])})
            if len(rows) > limit
            else None
        )
        return {"items": [_handover(row) for row in page], "next_cursor": next_cursor}

    @app.post("/api/v1/handovers", status_code=201)
    def create_handover(
        body: HandoverBody, request: Request, conn: Annotated[psycopg.Connection, Depends(get_conn)]
    ) -> dict:
        principal = principal_of(request)
        if body.outgoing_shift.strip().lower() == body.incoming_shift.strip().lower():
            problem(422, "same_shift", "A handover goes from one shift to a different one.")
        if not body.summary.strip():
            problem(422, "summary_required", "A handover needs a summary.")
        items = [_describe_item(conn, item) for item in body.open_items]
        handover_id = str(uuid.uuid4())
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO shift_handovers (handover_id, author, author_roles, outgoing_shift, incoming_shift, summary, open_items) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING {HANDOVER_COLUMNS}",
                (
                    handover_id,
                    principal.username,
                    list(principal.roles),
                    body.outgoing_shift.strip(),
                    body.incoming_shift.strip(),
                    body.summary.strip(),
                    Jsonb(items),
                ),
            )
            row = cur.fetchone()
        conn.commit()
        audit(
            conn,
            principal,
            "handover.create",
            "handover",
            handover_id,
            "allowed",
            outgoing_shift=body.outgoing_shift.strip(),
            incoming_shift=body.incoming_shift.strip(),
            open_items=len(items),
        )
        return _handover(row)

    @app.post("/api/v1/handovers/{handover_id}/acknowledge")
    def acknowledge_handover(
        handover_id: str, request: Request, conn: Annotated[psycopg.Connection, Depends(get_conn)]
    ) -> dict:
        principal = principal_of(request)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {HANDOVER_COLUMNS} FROM shift_handovers WHERE handover_id = %s FOR UPDATE",
                    (handover_id,),
                )
                row = cur.fetchone()
        except psycopg.errors.InvalidTextRepresentation:
            conn.rollback()
            row = None
        if row is None:
            conn.rollback()
            problem(404, "unknown_handover", "There is no such handover.")
        current = _handover(row)
        if current["acknowledged_by"]:
            conn.rollback()
            audit(
                conn,
                principal,
                "handover.acknowledge",
                "handover",
                handover_id,
                "denied",
                reason="already acknowledged",
                acknowledged_by=current["acknowledged_by"],
            )
            problem(
                409,
                "already_acknowledged",
                f"{current['acknowledged_by']} already acknowledged this handover.",
                acknowledged_by=current["acknowledged_by"],
                acknowledged_at=current["acknowledged_at"],
            )
        if current["author"] == principal.username:
            conn.rollback()
            audit(
                conn,
                principal,
                "handover.acknowledge",
                "handover",
                handover_id,
                "denied",
                reason="author cannot acknowledge their own handover",
            )
            problem(
                403,
                "own_handover",
                "You wrote this handover. The incoming person has to acknowledge it.",
            )
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE shift_handovers SET acknowledged_by = %s, acknowledged_at = now() WHERE handover_id = %s RETURNING {HANDOVER_COLUMNS}",
                (principal.username, handover_id),
            )
            row = cur.fetchone()
        conn.commit()
        audit(
            conn,
            principal,
            "handover.acknowledge",
            "handover",
            handover_id,
            "allowed",
            author=current["author"],
        )
        return _handover(row)
