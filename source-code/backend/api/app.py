"""P05.07: versioned, paginated device/traffic/history APIs plus a live
feed with reconnect (WebSocket, `/api/v1/live`).

All routes read real persisted data (P05.04's tables via P05.05's writes
and P05.06's aggregation) - nothing here invents or caches a shadow copy.
Pagination is cursor-based (opaque, base64-encoded), not offset-based, so
results stay correct under concurrent inserts. "Live reconnect" means a
client that disconnects and reconnects with `?since=<iso8601>` receives
every event it missed, in order, with no gap and no duplicate - proven in
verify_api.py against a real disconnect/reconnect, not simulated.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.kpis import iso_z, to_network_state_record  # noqa: E402
from backend.api.auth import auth_mode, enforce  # noqa: E402
from backend.api.hardening import BodyLimit, SecurityHeaders  # noqa: E402
from backend.api.authz import inventory  # noqa: E402
from backend.api.cursor import decode_cursor, encode_cursor  # noqa: E402
from backend.api.db import get_conn  # noqa: E402
from backend.observability import HTTPTracingMiddleware, configure  # noqa: E402
from backend.api import (  # noqa: E402
    routes_commands,
    routes_emergency,
    routes_govern,
    routes_incidents,
    routes_map,
)  # noqa: E402
from backend.state.network_state import compute_state  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

app = FastAPI(
    title="AIOPS Traffic Platform API",
    version="1.0.0",
    dependencies=[Depends(enforce)],
    docs_url=None,  # the interactive docs and the schema are not served: the route table is not published to anyone who asks
    redoc_url=None,
    openapi_url=None,
)
configure("api")
app.add_middleware(BodyLimit)
app.add_middleware(SecurityHeaders)
app.add_middleware(HTTPTracingMiddleware, service_name="api")
routes_map.register(app)
routes_incidents.register(app)
routes_emergency.register(app)
routes_commands.register(app)
routes_govern.register(app)

DEFAULT_LIMIT = 50
MAX_LIMIT = 500
LIVE_POLL_INTERVAL_S = 0.5


class Page(BaseModel):
    items: list[dict]
    next_cursor: str | None = None


# ---- identity and health (P08.04) --------------------------------------------


@app.get("/api/v1/health")
def health() -> dict:
    """Public liveness. Reports the auth mode so a browser stack can assert authentication is really on."""
    try:
        with psycopg.connect(dsn_from_env(), connect_timeout=2) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        database = "ok"
    except psycopg.Error:
        database = "unavailable"
    return {
        "status": "ok" if database == "ok" else "degraded",
        "time": iso_z(datetime.now(timezone.utc)),
        "auth_mode": auth_mode(),
        "database": database,
    }


@app.get("/api/v1/me")
def me(request: Request) -> dict:
    """Who the token says you are, and what that adds up to - the UI builds navigation from this, never from a guess."""
    principal = request.state.principal
    home = next(
        (spec["home"] for role, spec in inventory()["roles"].items() if role in principal.roles),
        None,
    )
    return {
        "sub": principal.sub,
        "username": principal.username,
        "name": principal.name,
        "roles": list(principal.roles),
        "capabilities": sorted(principal.capabilities),
        "home": home,
        "expires_at": principal.expires_at,
        "client": principal.client,
        "auth_mode": auth_mode(),
    }


# ---- devices ----------------------------------------------------------------


_DEVICE_SELECT = """
    SELECT d.device_id, d.device_type, d.deployment_type, d.agency_scope, d.status, d.corridor_id, d.intersection_id, d.lane_id, d.registered_at,
           d.certificate_not_after, last.observation_time
    FROM devices d
    LEFT JOIN LATERAL (SELECT observation_time FROM observation_events e WHERE e.device_id = d.device_id ORDER BY observation_time DESC LIMIT 1) last ON TRUE
"""
_DEVICE_COLUMNS = [
    "device_id",
    "device_type",
    "deployment_type",
    "agency_scope",
    "status",
    "corridor_id",
    "intersection_id",
    "lane_id",
    "registered_at",
    "certificate_not_after",
    "last_observation_time",
]


def _device_record(row: tuple) -> dict:
    record = dict(zip(_DEVICE_COLUMNS, row, strict=True))
    for key in ("registered_at", "certificate_not_after", "last_observation_time"):
        record[key] = iso_z(record[key]) if record[key] else None
    return record


@app.get("/api/v1/devices")
def list_devices(
    conn: Annotated[psycopg.Connection, Depends(get_conn)],
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    device_type: str | None = None,
    status: str | None = None,
) -> Page:
    """The device registry with each device's newest observation time, so a screen can say whether it is still reporting."""
    limit = min(max(limit, 1), MAX_LIMIT)
    after = decode_cursor(cursor)
    clauses, params = ["d.device_id > %s"], [after["device_id"] if after else ""]
    if device_type:
        clauses.append("d.device_type = %s")
        params.append(device_type)
    if status:
        clauses.append("d.status = %s")
        params.append(status)
    with conn.cursor() as cur:
        cur.execute(
            f"{_DEVICE_SELECT} WHERE {' AND '.join(clauses)} ORDER BY d.device_id ASC LIMIT %s",
            (*params, limit + 1),
        )
        rows = cur.fetchall()
    items = [_device_record(r) for r in rows[:limit]]
    next_cursor = (
        encode_cursor({"device_id": items[-1]["device_id"]}) if len(rows) > limit else None
    )
    return Page(items=items, next_cursor=next_cursor)


@app.get("/api/v1/devices/{device_id}")
def get_device(device_id: str, conn: Annotated[psycopg.Connection, Depends(get_conn)]) -> dict:
    with conn.cursor() as cur:
        cur.execute(f"{_DEVICE_SELECT} WHERE d.device_id = %s", (device_id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(404, f"device {device_id!r} not found")
    return _device_record(row)


# ---- network state (P05.06) --------------------------------------------------


@app.get("/api/v1/network-state/{network_element_type}/{network_element_id}")
def get_network_state(
    network_element_type: str,
    network_element_id: str,
    conn: Annotated[psycopg.Connection, Depends(get_conn)],
    window_seconds: float = 300,
    max_staleness_seconds: float = 120,
) -> dict:
    try:
        state = compute_state(
            conn, network_element_type, network_element_id, window_seconds, max_staleness_seconds
        )
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc)) from exc
    if state is None:
        raise HTTPException(
            404, f"no observations ever recorded for {network_element_type}/{network_element_id}"
        )
    return state


# ---- corridor KPIs (P06.01) ---------------------------------------------------


@app.get("/api/v1/kpis/corridors")
def list_corridor_kpis(
    conn: Annotated[psycopg.Connection, Depends(get_conn)],
    corridor_id: str | None = None,
    direction: str | None = None,
    window_seconds: int = 300,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page:
    """Stored corridor KPIs, newest window first, as `network-state/v1`
    records. Freshness is computed against *now* on every read, so a KPI
    window that has aged past its staleness budget is served as `stale`,
    never as fresh (SAFE-03)."""
    limit = min(max(limit, 1), MAX_LIMIT)
    after = decode_cursor(cursor)
    clauses, params = ["window_seconds = %s"], [window_seconds]
    if corridor_id:
        clauses.append("corridor_id = %s")
        params.append(corridor_id)
    if direction:
        clauses.append("direction = %s")
        params.append(direction)
    if after:
        clauses.append("(window_start, corridor_id, direction) < (%s, %s, %s)")
        params += [after["window_start"], after["corridor_id"], after["direction"]]
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT corridor_id, direction, window_start, window_seconds, geometry_version, kpis, quality,
                   coverage, sample_count
            FROM corridor_kpis WHERE {" AND ".join(clauses)}
            ORDER BY window_start DESC, corridor_id DESC, direction DESC LIMIT %s
            """,
            (*params, limit + 1),
        )
        rows = cur.fetchall()
    now = datetime.now(timezone.utc)
    items = [
        to_network_state_record(
            {
                "corridor_id": c,
                "direction": d,
                "window_start": iso_z(w),
                "window_seconds": ws,
                "geometry_version": g,
                "kpis": k,
                "quality": q,
                "coverage": cov,
                "sample_count": sc,
            },
            as_of=now,
        )
        for c, d, w, ws, g, k, q, cov, sc in rows[:limit]
    ]
    next_cursor = None
    if len(rows) > limit:
        c, d, w = rows[limit - 1][0], rows[limit - 1][1], rows[limit - 1][2]
        next_cursor = encode_cursor({"window_start": iso_z(w), "corridor_id": c, "direction": d})
    return Page(items=items, next_cursor=next_cursor)


# ---- forecasts (P06.03) -------------------------------------------------------


@app.get("/api/v1/forecasts/corridors")
def list_corridor_forecasts(
    conn: Annotated[psycopg.Connection, Depends(get_conn)],
    corridor_id: str | None = None,
    direction: str | None = None,
    horizon_seconds: int | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page:
    """Stored forecasts as `forecast/v1` records, newest valid window first.
    Always labelled `predicted`; a forecast is never served as an observation."""
    limit = min(max(limit, 1), MAX_LIMIT)
    after = decode_cursor(cursor)
    clauses, params = ["network_element_type = 'corridor'"], []
    if corridor_id and direction:
        clauses.append("network_element_id = %s")
        params.append(f"{corridor_id}/{direction}")
    elif corridor_id:
        clauses.append("network_element_id LIKE %s")
        params.append(f"{corridor_id}/%")
    if horizon_seconds:
        clauses.append("horizon_seconds = %s")
        params.append(horizon_seconds)
    if after:
        clauses.append(
            "(valid_from, network_element_id, horizon_seconds, forecast_id::text) < (%s, %s, %s, %s)"
        )
        params += [after["valid_from"], after["element"], after["horizon"], after["forecast_id"]]
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT forecast_id, network_element_type, network_element_id, geometry_version, predicted_at, horizon_seconds,
                   valid_from, valid_until, model_id, model_version, baseline_id, measurements
            FROM forecasts WHERE {" AND ".join(clauses)}
            ORDER BY valid_from DESC, network_element_id DESC, horizon_seconds DESC, forecast_id::text DESC LIMIT %s
            """,
            (*params, limit + 1),
        )
        rows = cur.fetchall()
    items = []
    for fid, etype, eid, geom, pred, hor, vf, vu, mid, mver, bid, meas in rows[:limit]:
        rec = {
            "schema_version": "1.0.0",
            "forecast_id": str(fid),
            "network_element_type": etype,
            "network_element_id": eid,
            "geometry_version": geom,
            "predicted_at": iso_z(pred),
            "horizon_seconds": hor,
            "valid_from": iso_z(vf),
            "valid_until": iso_z(vu),
            "model_id": mid,
            "model_version": mver,
            "measurements": meas,
            "truth_label": "predicted",
        }
        if bid:
            rec["baseline_id"] = bid
        items.append(rec)
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = encode_cursor(
            {
                "valid_from": last["valid_from"],
                "element": last["network_element_id"],
                "horizon": last["horizon_seconds"],
                "forecast_id": last["forecast_id"],
            }
        )
    return Page(items=items, next_cursor=next_cursor)


# ---- detection candidates (P06.04-P06.06) ---------------------------------------


@app.get("/api/v1/candidates")
def list_candidates(
    conn: Annotated[psycopg.Connection, Depends(get_conn)],
    kind: str | None = None,
    network_element_id: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page:
    """Evidence-backed detector output, newest onset first. A candidate is
    `inferred` and is not an incident: promotion is P06.07's correlation."""
    limit = min(max(limit, 1), MAX_LIMIT)
    after = decode_cursor(cursor)
    clauses, params = ["TRUE"], []
    if kind:
        clauses.append("kind = %s")
        params.append(kind)
    if network_element_id:
        clauses.append("network_element_id = %s")
        params.append(network_element_id)
    if after:
        clauses.append("(onset_time, candidate_id::text) < (%s, %s)")
        params += [after["onset_time"], after["candidate_id"]]
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT candidate_id, kind, network_element_type, network_element_id, geometry_version, onset_time, clear_time,
                   detected_at, severity, confidence, evidence_event_ids, source, attributes
            FROM detection_candidates WHERE {" AND ".join(clauses)}
            ORDER BY onset_time DESC, candidate_id::text DESC LIMIT %s
            """,
            (*params, limit + 1),
        )
        rows = cur.fetchall()
    items = [
        {
            "candidate_id": str(cid),
            "kind": k,
            "network_element_type": et,
            "network_element_id": eid,
            "geometry_version": g,
            "onset_time": iso_z(on),
            "clear_time": iso_z(cl) if cl else None,
            "detected_at": iso_z(da),
            "severity": sev,
            "confidence": conf,
            "evidence_event_ids": [str(x) for x in ev],
            "source": src,
            "attributes": attrs,
            "truth_label": "inferred",
        }
        for cid, k, et, eid, g, on, cl, da, sev, conf, ev, src, attrs in rows[:limit]
    ]
    next_cursor = (
        encode_cursor(
            {"onset_time": items[-1]["onset_time"], "candidate_id": items[-1]["candidate_id"]}
        )
        if len(rows) > limit
        else None
    )
    return Page(items=items, next_cursor=next_cursor)


# ---- incidents (P06.07) --------------------------------------------------------

INCIDENT_COLUMNS = (
    "incident_id, incident_type, severity, status, network_element_type, network_element_id, geometry_version, opened_at, updated_at, "
    "resolved_at, evidence_event_ids, root_cause_hypothesis, verified_cause, owner_role, confidence, duplicate_of"
)


def _incident_record(row: tuple) -> dict:
    (
        iid,
        itype,
        sev,
        status,
        et,
        eid,
        geometry,
        opened,
        updated,
        resolved,
        evidence,
        hypothesis,
        verified,
        owner,
        confidence,
        duplicate_of,
    ) = row
    record = {
        "schema_version": "1.0.0",
        "incident_id": str(iid),
        "incident_type": itype,
        "severity": sev,
        "status": status,
        "network_element_type": et,
        "network_element_id": eid,
        "geometry_version": geometry,
        "opened_at": iso_z(opened),
        "updated_at": iso_z(updated),
        "evidence_event_ids": [str(x) for x in evidence],
        "owner_role": owner,
        "confidence": confidence,
    }
    for key, value in (
        ("resolved_at", iso_z(resolved) if resolved else None),
        ("root_cause_hypothesis", hypothesis),
        ("verified_cause", verified),
        ("duplicate_of", str(duplicate_of) if duplicate_of else None),
    ):
        if value is not None:
            record[key] = value
    return record


@app.get("/api/v1/incidents")
def list_incidents(
    conn: Annotated[psycopg.Connection, Depends(get_conn)],
    status: str | None = None,
    incident_type: str | None = None,
    include_duplicates: bool = False,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page:
    """`incident/v1` records, newest first. Merged duplicates are hidden unless asked for."""
    limit = min(max(limit, 1), MAX_LIMIT)
    after = decode_cursor(cursor)
    clauses, params = ["TRUE"], []
    if status:
        clauses.append("status = %s")
        params.append(status)
    if incident_type:
        clauses.append("incident_type = %s")
        params.append(incident_type)
    if not include_duplicates:
        clauses.append("duplicate_of IS NULL")
    if after:
        clauses.append("(opened_at, incident_id::text) < (%s, %s)")
        params += [after["opened_at"], after["incident_id"]]
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {INCIDENT_COLUMNS} FROM incidents WHERE {' AND '.join(clauses)} ORDER BY opened_at DESC, incident_id::text DESC LIMIT %s",
            (*params, limit + 1),
        )
        rows = cur.fetchall()
    items = [_incident_record(r) for r in rows[:limit]]
    next_cursor = (
        encode_cursor(
            {"opened_at": items[-1]["opened_at"], "incident_id": items[-1]["incident_id"]}
        )
        if len(rows) > limit
        else None
    )
    return Page(items=items, next_cursor=next_cursor)


@app.get("/api/v1/incidents/{incident_id}")
def get_incident_detail(
    incident_id: str, conn: Annotated[psycopg.Connection, Depends(get_conn)]
) -> dict:
    """The incident record plus what an operator needs to judge it: the linked candidates and why, ranked
    hypotheses (never a verified cause), independent evidence modalities and the full transition history."""
    try:
        uuid_value = str(uuid.UUID(incident_id))
    except ValueError as exc:
        raise HTTPException(404, "unknown incident") from exc
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {INCIDENT_COLUMNS}, evidence_sources, evidence_cleared_at FROM incidents WHERE incident_id = %s",
            (uuid_value,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "unknown incident")
        cur.execute(
            "SELECT c.candidate_id, c.kind, c.network_element_id, c.onset_time, c.clear_time, c.confidence, ic.relation "
            "FROM incident_candidates ic JOIN detection_candidates c USING (candidate_id) WHERE ic.incident_id = %s ORDER BY c.onset_time, c.candidate_id",
            (uuid_value,),
        )
        candidates = [
            {
                "candidate_id": str(a),
                "kind": b,
                "network_element_id": c,
                "onset_time": iso_z(d),
                "clear_time": iso_z(e) if e else None,
                "detector_confidence": f,
                "relation": g,
            }
            for a, b, c, d, e, f, g in cur.fetchall()
        ]
        cur.execute(
            "SELECT rank, hypothesis, likelihood, supporting_candidate_ids, generated_at FROM incident_hypotheses WHERE incident_id = %s ORDER BY rank",
            (uuid_value,),
        )
        hypotheses = [
            {
                "rank": a,
                "hypothesis": b,
                "likelihood": c,
                "supporting_candidate_ids": [str(x) for x in d],
                "generated_at": iso_z(e),
            }
            for a, b, c, d, e in cur.fetchall()
        ]
        cur.execute(
            "SELECT from_status, to_status, changed_by, note, changed_at FROM incident_transitions WHERE incident_id = %s ORDER BY changed_at, id",
            (uuid_value,),
        )
        transitions = [
            {"from_status": a, "to_status": b, "changed_by": c, "note": d, "changed_at": iso_z(e)}
            for a, b, c, d, e in cur.fetchall()
        ]
        cur.execute(
            "SELECT note_id, author, author_roles, note, created_at FROM incident_notes WHERE incident_id = %s ORDER BY created_at, note_id",
            (uuid_value,),
        )
        notes = [
            {"note_id": a, "author": b, "author_roles": c, "note": d, "created_at": iso_z(e)}
            for a, b, c, d, e in cur.fetchall()
        ]
        cur.execute(
            "SELECT recommendation_id, action_type, generated_at, expires_at, status FROM recommendations WHERE trigger_incident_id = %s ORDER BY generated_at DESC",
            (uuid_value,),
        )
        recommendations = [
            {
                "recommendation_id": str(a),
                "action_type": b,
                "generated_at": iso_z(c),
                "expires_at": iso_z(d),
                "status": e,
            }
            for a, b, c, d, e in cur.fetchall()
        ]
        cur.execute(
            "SELECT command_id, recommendation_id, action_type, status, requested_by, requested_at, approved_by FROM commands "
            "WHERE recommendation_id = ANY(%s::uuid[]) ORDER BY requested_at DESC",
            ([r["recommendation_id"] for r in recommendations],),
        )
        commands = [
            {
                "command_id": str(a),
                "recommendation_id": str(b),
                "action_type": c,
                "status": d,
                "requested_by": e,
                "requested_at": iso_z(f),
                "approved_by": g,
            }
            for a, b, c, d, e, f, g in cur.fetchall()
        ]
    return {
        "incident": _incident_record(row[:16]),
        "evidence_sources": row[16],
        "evidence_cleared_at": iso_z(row[17]) if row[17] else None,
        "candidates": candidates,
        "hypotheses": hypotheses,
        "transitions": transitions,
        "notes": notes,
        "recommendations": recommendations,
        "commands": commands,
    }


# ---- emergency calls and unit assignments (P07.01) ------------------------------


@app.get("/api/v1/emergency/calls")
def list_emergency_calls(
    conn: Annotated[psycopg.Connection, Depends(get_conn)],
    status: str | None = None,
    call_type: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page:
    limit = min(max(limit, 1), MAX_LIMIT)
    after = decode_cursor(cursor)
    clauses, params = ["TRUE"], []
    if status:
        clauses.append("status = %s")
        params.append(status)
    if call_type:
        clauses.append("call_type = %s")
        params.append(call_type)
    if after:
        clauses.append("(reported_at, call_id::text) < (%s, %s)")
        params += [after["reported_at"], after["call_id"]]
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT call_id, call_type, call_subtype, priority, ST_Y(location::geometry), ST_X(location::geometry), geometry_version,
                   reported_at, source_reliability, status, truth_label
            FROM emergency_calls WHERE {" AND ".join(clauses)} ORDER BY reported_at DESC, call_id::text DESC LIMIT %s
            """,
            (*params, limit + 1),
        )
        rows = cur.fetchall()
    items = [
        {
            "schema_version": "1.0.0",
            "call_id": str(cid),
            "call_type": ct,
            "call_subtype": sub,
            "priority": pr,
            "location": {"coordinate_reference": "EPSG:4326", "latitude": lat, "longitude": lon},
            "geometry_version": g,
            "reported_at": iso_z(rep),
            "source_reliability": rel,
            "status": st,
            "truth_label": tl,
        }
        for cid, ct, sub, pr, lat, lon, g, rep, rel, st, tl in rows[:limit]
    ]
    next_cursor = (
        encode_cursor({"reported_at": items[-1]["reported_at"], "call_id": items[-1]["call_id"]})
        if len(rows) > limit
        else None
    )
    return Page(items=items, next_cursor=next_cursor)


@app.get("/api/v1/emergency/calls/{call_id}")
def get_emergency_call_detail(
    call_id: str, conn: Annotated[psycopg.Connection, Depends(get_conn)]
) -> dict:
    try:
        uuid_value = str(uuid.UUID(call_id))
    except ValueError as exc:
        raise HTTPException(404, "unknown call") from exc
    with conn.cursor() as cur:
        cur.execute(
            "SELECT call_id, call_type, call_subtype, priority, ST_Y(location::geometry), ST_X(location::geometry), geometry_version, "
            "reported_at, source_reliability, status, truth_label FROM emergency_calls WHERE call_id = %s",
            (uuid_value,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "unknown call")
        cur.execute(
            "SELECT assignment_id, unit_id, agency, capability, status, assigned_at, acknowledged_at, arrived_at, cleared_at, "
            "route_alternatives, handover, truth_label FROM emergency_unit_assignments WHERE call_id = %s ORDER BY assigned_at",
            (uuid_value,),
        )
        assignments = [
            {
                "schema_version": "1.0.0",
                "assignment_id": str(aid),
                "call_id": uuid_value,
                "unit_id": u,
                "agency": ag,
                "capability": cap,
                "status": st,
                "assigned_at": iso_z(asg),
                **({"acknowledged_at": iso_z(ack)} if ack else {}),
                **({"arrived_at": iso_z(arr)} if arr else {}),
                **({"cleared_at": iso_z(clr)} if clr else {}),
                "route_alternatives": alts,
                **({"handover": ho} if ho else {}),
                "truth_label": tl,
            }
            for aid, u, ag, cap, st, asg, ack, arr, clr, alts, ho, tl in cur.fetchall()
        ]
        cur.execute(
            "SELECT from_status, to_status, changed_by, note, changed_at FROM emergency_call_transitions WHERE call_id = %s ORDER BY changed_at, id",
            (uuid_value,),
        )
        transitions = [
            {"from_status": a, "to_status": b, "changed_by": c, "note": d, "changed_at": iso_z(e)}
            for a, b, c, d, e in cur.fetchall()
        ]
        cur.execute(
            "SELECT t.assignment_id, a.unit_id, t.from_status, t.to_status, t.changed_by, t.note, t.changed_at FROM emergency_assignment_transitions t "
            "JOIN emergency_unit_assignments a USING (assignment_id) WHERE a.call_id = %s ORDER BY t.changed_at, t.id",
            (uuid_value,),
        )
        assignment_transitions = [
            {
                "assignment_id": str(a),
                "unit_id": u,
                "from_status": b,
                "to_status": c,
                "changed_by": d,
                "note": n,
                "changed_at": iso_z(e),
            }
            for a, u, b, c, d, n, e in cur.fetchall()
        ]
    cid, ct, sub, pr, lat, lon, g, rep, rel, st, tl = row
    call = {
        "schema_version": "1.0.0",
        "call_id": str(cid),
        "call_type": ct,
        "call_subtype": sub,
        "priority": pr,
        "location": {"coordinate_reference": "EPSG:4326", "latitude": lat, "longitude": lon},
        "geometry_version": g,
        "reported_at": iso_z(rep),
        "source_reliability": rel,
        "status": st,
        "truth_label": tl,
    }
    return {
        "call": call,
        "assignments": assignments,
        "transitions": transitions,
        "assignment_transitions": assignment_transitions,
    }


# ---- routing (P07.02) ------------------------------------------------------------


@app.get("/api/v1/routes")
def get_routes(
    conn: Annotated[psycopg.Connection, Depends(get_conn)],
    origin: str,
    destination: str,
    geometry_version: str = "2026-09-18.1",
    vehicle_class: str | None = None,
    max_alternatives: int = 3,
) -> dict:
    """Fastest-safe route and alternatives, honoring live closures/hazards and using real
    measured travel time and reliability where a corridor has recent KPI data."""
    from backend.routing.route_service import route as compute_route

    routes = compute_route(
        conn,
        origin,
        destination,
        geometry_version,
        datetime.now(timezone.utc),
        vehicle_class,
        max_alternatives,
    )
    if not routes:
        raise HTTPException(404, "no route found (unknown node or no connecting path)")
    return {
        "origin": origin,
        "destination": destination,
        "geometry_version": geometry_version,
        "route_alternatives": [{**r.as_record(), "edges": list(r.edges)} for r in routes],
    }


# ---- recommendations (P07.04) ----------------------------------------------------


@app.get("/api/v1/recommendations")
def list_recommendations(
    conn: Annotated[psycopg.Connection, Depends(get_conn)],
    status: str | None = None,
    trigger_incident_id: str | None = None,
    trigger_emergency_call_id: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page:
    limit = min(max(limit, 1), MAX_LIMIT)
    after = decode_cursor(cursor)
    clauses, params = ["TRUE"], []
    if status:
        clauses.append("status = %s")
        params.append(status)
    if trigger_incident_id:
        clauses.append("trigger_incident_id = %s")
        params.append(trigger_incident_id)
    if trigger_emergency_call_id:
        clauses.append("trigger_emergency_call_id = %s")
        params.append(trigger_emergency_call_id)
    if after:
        clauses.append("(generated_at, recommendation_id::text) < (%s, %s)")
        params += [after["generated_at"], after["recommendation_id"]]
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT recommendation_id, trigger_incident_id, trigger_emergency_call_id, action_type, generated_at, expires_at, status,
                   alternatives, safety_bounds, constraints
            FROM recommendations WHERE {" AND ".join(clauses)} ORDER BY generated_at DESC, recommendation_id::text DESC LIMIT %s
            """,
            (*params, limit + 1),
        )
        rows = cur.fetchall()
    items = [
        {
            "schema_version": "1.0.0",
            "recommendation_id": str(rid),
            **({"trigger_incident_id": str(ti)} if ti else {}),
            **({"trigger_emergency_call_id": str(tc)} if tc else {}),
            "action_type": at,
            "generated_at": iso_z(gen),
            "expires_at": iso_z(exp),
            "status": st,
            "alternatives": alts,
            "safety_bounds": bounds,
            "constraints": cons,
        }
        for rid, ti, tc, at, gen, exp, st, alts, bounds, cons in rows[:limit]
    ]
    next_cursor = (
        encode_cursor(
            {
                "generated_at": items[-1]["generated_at"],
                "recommendation_id": items[-1]["recommendation_id"],
            }
        )
        if len(rows) > limit
        else None
    )
    return Page(items=items, next_cursor=next_cursor)


# ---- commands (P07.05) -----------------------------------------------------------


@app.get("/api/v1/commands")
def list_commands(
    conn: Annotated[psycopg.Connection, Depends(get_conn)],
    status: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page:
    limit = min(max(limit, 1), MAX_LIMIT)
    after = decode_cursor(cursor)
    clauses, params = ["TRUE"], []
    if status:
        clauses.append("status = %s")
        params.append(status)
    if after:
        clauses.append("(requested_at, command_id::text) < (%s, %s)")
        params += [after["requested_at"], after["command_id"]]
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT command_id, idempotency_key, recommendation_id, action_type, target_adapter, target_entity_id, requested_by,
                   requested_at, approved_by, approved_at, expires_at, policy_decision, status, acknowledged_at, rolled_back_at,
                   error_code, error_message, error_retryable
            FROM commands WHERE {" AND ".join(clauses)} ORDER BY requested_at DESC, command_id::text DESC LIMIT %s
            """,
            (*params, limit + 1),
        )
        rows = cur.fetchall()
    items = [_command_record(r) for r in rows[:limit]]
    next_cursor = (
        encode_cursor(
            {"requested_at": items[-1]["requested_at"], "command_id": items[-1]["command_id"]}
        )
        if len(rows) > limit
        else None
    )
    return Page(items=items, next_cursor=next_cursor)


def _command_record(row: tuple) -> dict:
    (
        cid,
        ikey,
        rid,
        at,
        adapter,
        entity,
        reqby,
        reqat,
        apby,
        apat,
        exp,
        pdec,
        status,
        ack,
        rb,
        ecode,
        emsg,
        eretry,
    ) = row
    record = {
        "schema_version": "1.0.0",
        "command_id": str(cid),
        "idempotency_key": ikey,
        "action_type": at,
        "target": {"adapter": adapter, "entity_id": entity},
        "requested_by": reqby,
        "requested_at": iso_z(reqat),
        "expires_at": iso_z(exp),
        "policy_decision": pdec,
        "status": status,
    }
    for key, value in (
        ("recommendation_id", str(rid) if rid else None),
        ("approved_by", apby),
        ("approved_at", iso_z(apat) if apat else None),
        ("acknowledged_at", iso_z(ack) if ack else None),
        ("rolled_back_at", iso_z(rb) if rb else None),
    ):
        if value is not None:
            record[key] = value
    if ecode:
        record["error"] = {"error_code": ecode, "message": emsg, "retryable": eretry}
    return record


@app.get("/api/v1/commands/{command_id}")
def get_command_detail(
    command_id: str, conn: Annotated[psycopg.Connection, Depends(get_conn)]
) -> dict:
    try:
        uuid_value = str(uuid.UUID(command_id))
    except ValueError as exc:
        raise HTTPException(404, "unknown command") from exc
    with conn.cursor() as cur:
        cur.execute(
            "SELECT command_id, idempotency_key, recommendation_id, action_type, target_adapter, target_entity_id, requested_by, "
            "requested_at, approved_by, approved_at, expires_at, policy_decision, status, acknowledged_at, rolled_back_at, error_code, "
            "error_message, error_retryable FROM commands WHERE command_id = %s",
            (uuid_value,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "unknown command")
        cur.execute(
            "SELECT from_status, to_status, changed_by, note, changed_at FROM command_transitions WHERE command_id = %s ORDER BY changed_at, id",
            (uuid_value,),
        )
        transitions = [
            {"from_status": a, "to_status": b, "changed_by": c, "note": d, "changed_at": iso_z(e)}
            for a, b, c, d, e in cur.fetchall()
        ]
        cur.execute(
            "SELECT requested_by_role, approved_by_role FROM commands WHERE command_id = %s",
            (uuid_value,),
        )
        requested_role, approved_role = cur.fetchone()
        cur.execute("SELECT params FROM command_params WHERE command_id = %s", (uuid_value,))
        params_row = cur.fetchone()
        cur.execute(
            f"SELECT {_OUTCOME_COLUMNS} FROM command_outcomes WHERE command_id = %s ORDER BY verified_at DESC LIMIT 1",
            (uuid_value,),
        )
        outcome_row = cur.fetchone()
        recommendation = None
        if row[2] is not None:
            cur.execute(
                "SELECT action_type, status, trigger_incident_id, trigger_emergency_call_id FROM recommendations WHERE recommendation_id = %s",
                (row[2],),
            )
            rec = cur.fetchone()
            if rec:
                recommendation = {
                    "recommendation_id": str(row[2]),
                    "action_type": rec[0],
                    "status": rec[1],
                    "trigger_incident_id": str(rec[2]) if rec[2] else None,
                    "trigger_emergency_call_id": str(rec[3]) if rec[3] else None,
                }
    from backend.repositories.outcomes import to_contract  # noqa: PLC0415
    from backend.roles import SAFETY_CLASS_OF_ACTION  # noqa: PLC0415

    return {
        "command": _command_record(row),
        "transitions": transitions,
        "safety_class": SAFETY_CLASS_OF_ACTION.get(row[3], "SC-1"),
        "requested_by_role": requested_role,
        "approved_by_role": approved_role,
        "params": params_row[0] if params_row else None,
        "outcome": to_contract(outcome_row) if outcome_row else None,
        "recommendation": recommendation,
    }


# ---- outcomes (P07.09) ------------------------------------------------------------

_OUTCOME_COLUMNS = (
    "outcome_id, command_id, pre_window_start, pre_window_end, post_window_start, post_window_end, classification, "
    "verified_at, verifier, rollback_triggered, detail"
)


@app.get("/api/v1/outcomes")
def list_outcomes(
    conn: Annotated[psycopg.Connection, Depends(get_conn)],
    command_id: str | None = None,
    classification: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page:
    from backend.repositories.outcomes import to_contract  # noqa: PLC0415

    limit = min(max(limit, 1), MAX_LIMIT)
    after = decode_cursor(cursor)
    clauses, params = ["TRUE"], []
    if command_id:
        try:
            clauses.append("command_id = %s")
            params.append(str(uuid.UUID(command_id)))
        except ValueError as exc:
            raise HTTPException(400, "command_id must be a uuid") from exc
    if classification:
        clauses.append("classification = %s")
        params.append(classification)
    if after:
        clauses.append("(verified_at, outcome_id::text) < (%s, %s)")
        params += [after["verified_at"], after["outcome_id"]]
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_OUTCOME_COLUMNS} FROM command_outcomes WHERE {' AND '.join(clauses)} "
            "ORDER BY verified_at DESC, outcome_id::text DESC LIMIT %s",
            (*params, limit + 1),
        )
        rows = cur.fetchall()
    items = [to_contract(r) for r in rows[:limit]]
    next_cursor = (
        encode_cursor(
            {"verified_at": items[-1]["verified_at"], "outcome_id": items[-1]["outcome_id"]}
        )
        if len(rows) > limit
        else None
    )
    return Page(items=items, next_cursor=next_cursor)


@app.get("/api/v1/outcomes/{outcome_id}")
def get_outcome_detail(
    outcome_id: str, conn: Annotated[psycopg.Connection, Depends(get_conn)]
) -> dict:
    from backend.repositories.outcomes import to_contract  # noqa: PLC0415

    try:
        uuid_value = str(uuid.UUID(outcome_id))
    except ValueError as exc:
        raise HTTPException(404, "unknown outcome") from exc
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_OUTCOME_COLUMNS} FROM command_outcomes WHERE outcome_id = %s", (uuid_value,)
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "unknown outcome")
    detail = {
        k: v
        for k, v in (row[10] or {}).items()
        if k not in ("pre_measurements", "post_measurements", "escalation_reason")
    }
    return {"outcome": to_contract(row), "detail": detail}


# ---- observation history ------------------------------------------------------


@app.get("/api/v1/observations")
def list_observations(
    conn: Annotated[psycopg.Connection, Depends(get_conn)],
    device_id: str | None = None,
    event_type: str | None = None,
    order: str = "asc",
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Page:
    """Observation history. `order=asc` (default) pages forward through time from the start; `order=desc` starts at the newest,
    which is what a screen showing "the latest reading" wants. Each item carries the position it was observed at."""
    if order not in ("asc", "desc"):
        raise HTTPException(400, "order must be asc or desc")
    limit = min(max(limit, 1), MAX_LIMIT)
    after = decode_cursor(cursor)
    ascending = order == "asc"
    comparison = ">" if ascending else "<"
    clauses, params = [], []
    if after:
        clauses.append(f"(observation_time, event_id::text) {comparison} (%s, %s)")
        params += [after["observation_time"], after["event_id"]]
    elif ascending:
        clauses.append("(observation_time, event_id::text) > (%s, %s)")
        params += ["1970-01-01T00:00:00Z", ""]
    if device_id:
        clauses.append("device_id = %s")
        params.append(device_id)
    if event_type:
        clauses.append("event_type = %s")
        params.append(event_type)
    where = " AND ".join(clauses) if clauses else "TRUE"
    direction = "ASC" if ascending else "DESC"

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT event_id, device_id, event_type, observation_time, measurements, truth_label,
                   ST_Y(location::geometry), ST_X(location::geometry)
            FROM observation_events WHERE {where}
            ORDER BY observation_time {direction}, event_id::text {direction} LIMIT %s
            """,
            (*params, limit + 1),
        )
        rows = cur.fetchall()
    cols = [
        "event_id",
        "device_id",
        "event_type",
        "observation_time",
        "measurements",
        "truth_label",
        "latitude",
        "longitude",
    ]
    items = [dict(zip(cols, r, strict=True)) for r in rows[:limit]]
    for it in items:
        it["event_id"] = str(it["event_id"])
        it["observation_time"] = it["observation_time"].isoformat()
    next_cursor = (
        encode_cursor(
            {"observation_time": items[-1]["observation_time"], "event_id": items[-1]["event_id"]}
        )
        if len(rows) > limit
        else None
    )
    return Page(items=items, next_cursor=next_cursor)


# ---- live feed with reconnect --------------------------------------------------


@app.websocket("/api/v1/live")
async def live_feed(
    websocket: WebSocket,
    since: str | None = None,
    device_id: str | None = None,
    corridor_id: str | None = None,
) -> None:
    """On connect, first sends every matching observation_events row with
    received_at > `since` (backlog catch-up), then polls for and streams new
    matching rows every LIVE_POLL_INTERVAL_S. A client that disconnects and
    reconnects with `since` set to the last event it saw receives exactly
    what it missed - no gap, no duplicate (received_at is monotonically
    assigned by the database, not the client).

    `device_id`/`corridor_id` scope the feed - a real operator UI watching
    one corridor should never receive every other corridor's firehose;
    omitting both streams every event, unfiltered."""
    await websocket.accept()
    try:
        cursor_time = (
            datetime.fromisoformat(since) if since else datetime(1970, 1, 1, tzinfo=timezone.utc)
        )
    except ValueError:
        await websocket.close(code=1008, reason=f"invalid since={since!r}, expected ISO 8601")
        return
    conn = psycopg.connect(dsn_from_env())

    clauses = ["received_at > %s"]
    params: list = []
    if device_id:
        clauses.append("device_id = %s")
        params.append(device_id)
    if corridor_id:
        clauses.append("corridor_id = %s")
        params.append(corridor_id)
    where = " AND ".join(clauses)

    def poll(cutoff: datetime) -> list:
        # Runs off the event loop (asyncio.to_thread below): psycopg's
        # blocking call would otherwise stall every other connection's
        # live feed for the length of each poll.
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT event_id, device_id, event_type, observation_time, received_at, measurements
                FROM observation_events WHERE {where}
                ORDER BY received_at ASC LIMIT 200
                """,
                (cutoff, *params),
            )
            return cur.fetchall()

    try:
        while True:
            rows = await asyncio.to_thread(poll, cursor_time)
            for event_id, device_id, event_type, obs_time, received_at, measurements in rows:
                await websocket.send_json(
                    {
                        "event_id": str(event_id),
                        "device_id": device_id,
                        "event_type": event_type,
                        "observation_time": obs_time.isoformat(),
                        "received_at": received_at.isoformat(),
                        "measurements": measurements,
                    }
                )
                cursor_time = received_at
            await asyncio.sleep(LIVE_POLL_INTERVAL_S)
    except WebSocketDisconnect:
        pass
    finally:
        conn.close()
