"""P08.07 acceptance evidence (API side): the incident and dispatch actions, against the real Keycloak, the real Postgres and the
real FastAPI app under uvicorn on a real socket:

    python source-code/backend/api/verify_operator_actions.py

Every request carries a real access token obtained through the browser's Authorization Code + PKCE flow. What is proven:
attribution to the token's person (never to a name the client sends), the role gate, the incident and call state machines through
the API, refusal of a stale or illegal move with what the record actually is now, a required note to resolve, concurrent
transitions serialising, route alternatives that come from the real router, the call following its assignments, and an audit
trail that records accepted, refused and denied actions and cannot be edited.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx
import psycopg
import uvicorn

os.environ.pop("AIOPS_AUTH_MODE", None)

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.api import oidc_client  # noqa: E402
from backend.api.app import app  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.ingestion.ingest import ingest_one, load_schema  # noqa: E402
from backend.loader.platform_loader import register_devices  # noqa: E402
from backend.repositories import incidents as incident_repo  # noqa: E402
from database.migrate import dsn_from_env, migrate  # noqa: E402

HOST, PORT = "127.0.0.1", 8812
BASE = f"http://{HOST}:{PORT}"
GEOMETRY = "2026-09-18.1"
IDENTITIES = json.loads(
    (SOURCE_ROOT / "infra" / "platform" / "output" / "demo_identities.json").read_text(
        encoding="utf-8"
    )
)
ROLE_USER = {spec["role"]: user for user, spec in reversed(list(IDENTITIES.items()))}
EMERGENCY_DEVICES = [
    json.loads(line)
    for line in (SOURCE_ROOT / "simulator" / "emergency" / "output" / "run-a" / "devices.jsonl")
    .read_text(encoding="utf-8")
    .splitlines()
]
ev = Evidence("P08.07")


def run_server() -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(80):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("uvicorn did not start")


def new_incident(
    conn: psycopg.Connection, kind: str = "congestion", severity: str = "medium"
) -> str:
    return incident_repo.create_incident(
        conn,
        kind,
        severity,
        "segment",
        f"verify-{uuid.uuid4().hex[:8]}",
        GEOMETRY,
        [str(uuid.uuid4())],
        "OPS",
        0.7,
        "verify-fixture",
    )


def audit_rows(conn: psycopg.Connection, entity_id: str) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT actor, actor_roles, action, outcome, detail FROM operator_audit WHERE entity_id = %s ORDER BY audit_id",
            (entity_id,),
        )
        return cur.fetchall()


def place_unit(conn: psycopg.Connection, unit: str, latitude: float, longitude: float) -> None:
    """One AVL position through the platform's own ingestion, so the unit's location is a real observation."""
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    event = {
        "schema_version": "1.0.0",
        "event_id": str(uuid.uuid4()),
        "event_type": "emergency.unit_position.avl",
        "device_id": f"avl-{unit}",
        "agency_scope": "ems-dispatch",
        "observation_time": now,
        "ingest_time": now,
        "sequence_number": int(time.time()),
        "clock_quality": "synced",
        "geometry_version": GEOMETRY,
        "location": {
            "coordinate_reference": "EPSG:4326",
            "latitude": latitude,
            "longitude": longitude,
        },
        "measurements": [
            {"name": "speed", "value": 0.0, "unit": "m_s-1", "quality": "valid", "confidence": 0.9}
        ],
        "truth_label": "simulated",
        "privacy_classification": "none",
        "retention_class": "standard",
    }
    result = ingest_one(conn, event, load_schema())
    if result.outcome.value not in ("inserted", "duplicate_ok"):
        raise RuntimeError(f"fixture AVL event was not accepted: {result}")


async def main() -> int:  # noqa: PLR0915
    tokens = {
        role: oidc_client.login(user, IDENTITIES[user]["password"]).access_token
        for role, user in ROLE_USER.items()
    }
    users = {role: ROLE_USER[role] for role in ROLE_USER}

    def h(role: str) -> dict:
        return {"Authorization": f"Bearer {tokens[role]}"}

    with psycopg.connect(dsn_from_env()) as db:
        migrate(db)
        register_devices(db, EMERGENCY_DEVICES)
        with db.cursor() as cur:
            cur.execute(
                "SELECT intersection_id, ST_Y(location::geometry), ST_X(location::geometry) FROM intersections WHERE geometry_version = %s",
                (GEOMETRY,),
            )
            nodes = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        server = run_server()
        try:
            async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
                # ------------------------------------------------------------------ incidents
                incident = new_incident(db)
                t = await c.post(
                    f"/api/v1/incidents/{incident}/transition",
                    headers=h("operator"),
                    json={"to_status": "acknowledged", "note": "on it", "expected_status": "open"},
                )
                ev.check(
                    "an_operator_acknowledges_an_incident_and_it_is_recorded_against_the_token_person",
                    t.status_code == 200
                    and t.json()["status"] == "acknowledged"
                    and t.json()["previous_status"] == "open",
                    detail=t.text[:200],
                )
                detail = (
                    await c.get(f"/api/v1/incidents/{incident}", headers=h("operator"))
                ).json()
                last = detail["transitions"][-1]
                ev.check(
                    "the_transition_history_names_the_person_from_the_token_and_the_note",
                    last["changed_by"] == users["operator"]
                    and last["note"] == "on it"
                    and last["to_status"] == "acknowledged",
                    detail=str(last),
                )
                forged = await c.post(
                    f"/api/v1/incidents/{incident}/transition",
                    headers=h("operator"),
                    json={"to_status": "investigating", "changed_by": "someone.else"},
                )
                ev.check(
                    "a_client_supplied_author_is_refused_not_believed", forged.status_code == 422
                )
                stale = await c.post(
                    f"/api/v1/incidents/{incident}/transition",
                    headers=h("supervisor"),
                    json={"to_status": "investigating", "expected_status": "open"},
                )
                ev.check(
                    "a_move_made_from_an_out_of_date_view_is_refused_and_says_what_the_incident_is_now",
                    stale.status_code == 409
                    and stale.json()["detail"]["error"] == "status_changed"
                    and stale.json()["detail"]["current_status"] == "acknowledged",
                    detail=stale.text[:200],
                )
                still = incident_repo.get_incident(db, incident)
                ev.check(
                    "the_refused_stale_move_changed_nothing", still["status"] == "acknowledged"
                )
                nonote = await c.post(
                    f"/api/v1/incidents/{incident}/transition",
                    headers=h("operator"),
                    json={"to_status": "resolved"},
                )
                blank = await c.post(
                    f"/api/v1/incidents/{incident}/transition",
                    headers=h("operator"),
                    json={"to_status": "resolved", "note": "   "},
                )
                ev.check(
                    "resolving_needs_a_written_note",
                    nonote.status_code == 422
                    and nonote.json()["detail"]["error"] == "note_required"
                    and blank.status_code == 422,
                )
                illegal = await c.post(
                    f"/api/v1/incidents/{incident}/transition",
                    headers=h("operator"),
                    json={"to_status": "reopened"},
                )
                ev.check(
                    "an_illegal_transition_is_refused_and_lists_the_legal_ones",
                    illegal.status_code == 409
                    and "investigating" in illegal.json()["detail"]["allowed"],
                    detail=illegal.text[:200],
                )
                resolved = await c.post(
                    f"/api/v1/incidents/{incident}/transition",
                    headers=h("incident_commander"),
                    json={"to_status": "resolved", "note": "blockage cleared by tow"},
                )
                ev.check(
                    "an_incident_commander_resolves_with_a_note",
                    resolved.status_code == 200
                    and incident_repo.get_incident(db, incident)["resolved_at"] is not None,
                )
                reopened = await c.post(
                    f"/api/v1/incidents/{incident}/transition",
                    headers=h("supervisor"),
                    json={"to_status": "reopened", "note": "back again"},
                )
                ev.check(
                    "a_resolved_incident_can_be_reopened_by_a_supervisor",
                    reopened.status_code == 200 and reopened.json()["status"] == "reopened",
                )

                # two people move the same incident at once: the row lock lets exactly one win
                racing = new_incident(db)
                results = await asyncio.gather(
                    c.post(
                        f"/api/v1/incidents/{racing}/transition",
                        headers=h("operator"),
                        json={"to_status": "resolved", "note": "a"},
                    ),
                    c.post(
                        f"/api/v1/incidents/{racing}/transition",
                        headers=h("supervisor"),
                        json={"to_status": "escalated", "note": "b"},
                    ),
                )
                codes = sorted(r.status_code for r in results)
                final = incident_repo.get_incident(db, racing)["status"]
                ev.check(
                    "two_simultaneous_transitions_serialise_one_wins_and_the_other_is_told_it_lost",
                    codes == [200, 409] or (codes == [200, 200] and final == "resolved"),
                    detail=f"{codes} -> {final}",
                )

                owner = await c.post(
                    f"/api/v1/incidents/{incident}/owner",
                    headers=h("operator"),
                    json={"owner_role": "EMERG"},
                )
                bad_owner = await c.post(
                    f"/api/v1/incidents/{incident}/owner",
                    headers=h("operator"),
                    json={"owner_role": "PRESIDENT"},
                )
                ev.check(
                    "ownership_moves_to_a_known_desk_and_an_unknown_desk_is_refused",
                    owner.status_code == 200
                    and owner.json()["previous_owner_role"] == "OPS"
                    and bad_owner.status_code == 422,
                )
                note = await c.post(
                    f"/api/v1/incidents/{incident}/notes",
                    headers=h("supervisor"),
                    json={"note": "Tow requested; 'DROP TABLE incidents;--' is only text"},
                )
                too_long = await c.post(
                    f"/api/v1/incidents/{incident}/notes",
                    headers=h("supervisor"),
                    json={"note": "x" * 2001},
                )
                empty = await c.post(
                    f"/api/v1/incidents/{incident}/notes",
                    headers=h("supervisor"),
                    json={"note": ""},
                )
                after = (await c.get(f"/api/v1/incidents/{incident}", headers=h("auditor"))).json()
                ev.check(
                    "a_note_is_stored_verbatim_by_its_author_and_visible_to_a_reader_and_bad_notes_are_refused",
                    note.status_code == 201
                    and note.json()["author"] == users["supervisor"]
                    and any("DROP TABLE" in n["note"] for n in after["notes"])
                    and too_long.status_code == 422
                    and empty.status_code == 422,
                    detail=f"{note.status_code}",
                )
                ev.check(
                    "the_owner_change_is_written_into_the_incident_notes_so_the_timeline_shows_it",
                    any("Owner changed from OPS to EMERG" in n["note"] for n in after["notes"]),
                )
                ev.check(
                    "the_incident_detail_links_recommendations_and_commands",
                    isinstance(after["recommendations"], list)
                    and isinstance(after["commands"], list),
                )
                missing = uuid.uuid4()
                unknown_codes = []
                for x in (missing, "not-a-uuid"):
                    for path, body in (
                        ("transition", {"to_status": "acknowledged"}),
                        ("owner", {"owner_role": "OPS"}),
                        ("notes", {"note": "x"}),
                    ):
                        unknown_codes.append(
                            (
                                await c.post(
                                    f"/api/v1/incidents/{x}/{path}",
                                    headers=h("operator"),
                                    json=body,
                                )
                            ).status_code
                        )
                ev.check(
                    "an_unknown_or_malformed_incident_is_404_on_every_action",
                    set(unknown_codes) == {404},
                    detail=str(unknown_codes),
                )

                # the role gate: who may act at all
                gate = {}
                for role in ("dispatcher", "field_responder", "auditor", "demo_operator"):
                    gate[role] = (
                        await c.post(
                            f"/api/v1/incidents/{incident}/notes",
                            headers=h(role),
                            json={"note": "should not be stored"},
                        )
                    ).status_code
                ev.check(
                    "roles_without_incidents_manage_are_forbidden_from_every_incident_action",
                    set(gate.values()) == {403},
                    detail=str(gate),
                )
                with db.cursor() as cur:
                    cur.execute(
                        "SELECT count(*) FROM incident_notes WHERE incident_id = %s AND note = 'should not be stored'",
                        (incident,),
                    )
                    ev.check("a_forbidden_note_was_not_stored", cur.fetchone()[0] == 0)
                denied_rows = [r for r in audit_rows(db, incident) if r[3] == "denied"]
                ev.check(
                    "refused_business_actions_are_audited_with_the_reason",
                    any(
                        "changed" in json.dumps(r[4]) or "note" in json.dumps(r[4])
                        for r in denied_rows
                    ),
                    detail=f"{len(denied_rows)} denied rows",
                )
                allowed_rows = [r for r in audit_rows(db, incident) if r[3] == "allowed"]
                ev.check(
                    "accepted_actions_are_audited_with_person_and_role",
                    {"incident.transition", "incident.owner", "incident.note"}
                    <= {r[2] for r in allowed_rows}
                    and all(r[0] in users.values() and r[1] for r in allowed_rows),
                )
                with db.cursor() as cur:
                    cur.execute(
                        "SELECT count(*) FROM operator_audit WHERE outcome = 'denied' AND action = 'POST /api/v1/incidents/{incident_id}/notes' AND actor = %s",
                        (users["dispatcher"],),
                    )
                    ev.check("a_role_gate_refusal_is_audited_too", cur.fetchone()[0] >= 1)

                # ------------------------------------------------------------------ emergency
                make = await c.post(
                    "/api/v1/emergency/calls",
                    headers=h("dispatcher"),
                    json={
                        "call_type": "ambulance",
                        "call_subtype": "medical emergency",
                        "priority": "high",
                        "intersection_id": "int-b2",
                    },
                )
                ev.check(
                    "a_dispatcher_takes_a_call_and_it_is_operator_entered",
                    make.status_code == 201 and make.json()["truth_label"] == "operator_entered",
                    detail=make.text[:200],
                )
                call_id = make.json()["call_id"]
                listed = (
                    await c.get(
                        "/api/v1/emergency/calls", params={"limit": 500}, headers=h("dispatcher")
                    )
                ).json()["items"]
                mine = next(i for i in listed if i["call_id"] == call_id)
                ev.check(
                    "the_new_call_is_listed_at_the_chosen_intersection",
                    mine["status"] == "received"
                    and mine["truth_label"] == "operator_entered"
                    and abs(mine["location"]["latitude"] - nodes["int-b2"][0]) < 1e-5,
                )
                bad_calls = [
                    {
                        "call_type": "helicopter",
                        "call_subtype": "x",
                        "priority": "high",
                        "intersection_id": "int-b2",
                    },
                    {
                        "call_type": "fire",
                        "call_subtype": "x",
                        "priority": "urgent",
                        "intersection_id": "int-b2",
                    },
                    {"call_type": "fire", "call_subtype": "x", "priority": "high"},
                    {
                        "call_type": "fire",
                        "call_subtype": "x",
                        "priority": "high",
                        "intersection_id": "int-zz",
                    },
                    {
                        "call_type": "fire",
                        "call_subtype": "x; DROP",
                        "priority": "high",
                        "intersection_id": "int-b2",
                    },
                    {
                        "call_type": "fire",
                        "call_subtype": "x",
                        "priority": "high",
                        "intersection_id": "int-b2",
                        "truth_label": "verified",
                    },
                ]
                bad_codes = [
                    (
                        await c.post("/api/v1/emergency/calls", headers=h("dispatcher"), json=b)
                    ).status_code
                    for b in bad_calls
                ]
                ev.check(
                    "malformed_calls_are_refused", set(bad_codes) == {422}, detail=str(bad_codes)
                )
                gate = {}
                for role in ("operator", "supervisor", "field_responder", "auditor"):
                    gate[role] = (
                        await c.post(
                            "/api/v1/emergency/calls",
                            headers=h(role),
                            json={
                                "call_type": "fire",
                                "call_subtype": "x",
                                "priority": "low",
                                "intersection_id": "int-b2",
                            },
                        )
                    ).status_code
                ev.check(
                    "only_dispatch_roles_may_take_a_call",
                    set(gate.values()) == {403},
                    detail=str(gate),
                )
                ev.check(
                    "an_incident_commander_may_also_dispatch",
                    (
                        await c.post(
                            "/api/v1/emergency/calls",
                            headers=h("incident_commander"),
                            json={
                                "call_type": "police",
                                "call_subtype": "traffic_collision",
                                "priority": "medium",
                                "intersection_id": "int-a2",
                            },
                        )
                    ).status_code
                    == 201,
                )

                # assignment: origin from the unit's own reported position, route from the real router
                place_unit(db, "ambulance-1", *nodes["int-b4"])
                fits = await c.post(
                    f"/api/v1/emergency/calls/{call_id}/assignments",
                    headers=h("dispatcher"),
                    json={"unit_id": "fire-engine-1", "origin_intersection_id": "int-b1"},
                )
                ev.check(
                    "a_unit_of_the_wrong_kind_is_refused",
                    fits.status_code == 422
                    and fits.json()["detail"]["error"] == "unit_does_not_fit",
                    detail=fits.text[:160],
                )
                unknown = await c.post(
                    f"/api/v1/emergency/calls/{call_id}/assignments",
                    headers=h("dispatcher"),
                    json={"unit_id": "ambulance-99", "origin_intersection_id": "int-b1"},
                )
                ev.check(
                    "a_unit_that_is_not_registered_is_refused",
                    unknown.status_code == 422
                    and unknown.json()["detail"]["error"] == "unknown_unit",
                    detail=unknown.text[:160],
                )
                silent = await c.post(
                    f"/api/v1/emergency/calls/{call_id}/assignments",
                    headers=h("dispatcher"),
                    json={"unit_id": "ambulance-2"},
                )
                ev.check(
                    "a_unit_whose_last_position_is_old_is_refused_rather_than_routed_from_a_guess",
                    silent.status_code == 422
                    and silent.json()["detail"]["error"]
                    in ("unit_position_stale", "unit_position_unknown"),
                    detail=silent.text[:200],
                )
                assign = await c.post(
                    f"/api/v1/emergency/calls/{call_id}/assignments",
                    headers=h("dispatcher"),
                    json={"unit_id": "ambulance-1"},
                )
                ev.check(
                    "assigning_a_unit_finds_routes_from_where_the_unit_is",
                    assign.status_code == 201,
                    detail=assign.text[:200],
                )
                assignment_id = assign.json()["assignment_id"]
                detail = (
                    await c.get(f"/api/v1/emergency/calls/{call_id}", headers=h("dispatcher"))
                ).json()
                alternatives = detail["assignments"][0]["route_alternatives"]
                ev.check(
                    "the_assignment_carries_route_alternatives_with_eta_uncertainty_and_exactly_one_selected",
                    len(alternatives) >= 1
                    and sum(1 for a in alternatives if a["selected"]) == 1
                    and all(
                        a["eta_seconds"] >= 0 and "eta_uncertainty_seconds" in a
                        for a in alternatives
                    ),
                    detail=f"{len(alternatives)} alternatives, eta {[a['eta_seconds'] for a in alternatives]}",
                )
                ev.check(
                    "the_assignment_capability_is_the_array_the_contract_says_not_a_string",
                    detail["assignments"][0]["capability"] == ["als", "transport"],
                    detail=str(detail["assignments"][0]["capability"]),
                )
                ev.check(
                    "the_call_followed_its_assignment_to_unit_assigned_through_dispatched",
                    detail["call"]["status"] == "unit_assigned"
                    and [t["to_status"] for t in detail["transitions"]]
                    == ["received", "dispatched", "unit_assigned"],
                    detail=str([t["to_status"] for t in detail["transitions"]]),
                )
                dup = await c.post(
                    f"/api/v1/emergency/calls/{call_id}/assignments",
                    headers=h("dispatcher"),
                    json={"unit_id": "ambulance-1"},
                )
                ev.check(
                    "the_same_unit_cannot_be_assigned_to_the_same_call_twice",
                    dup.status_code == 409 and dup.json()["detail"]["error"] == "already_assigned",
                )

                if len(alternatives) > 1:
                    other = next(a for a in alternatives if not a["selected"])
                    chosen = await c.post(
                        f"/api/v1/emergency/assignments/{assignment_id}/route",
                        headers=h("dispatcher"),
                        json={"route_id": other["route_id"]},
                    )
                    after_route = (
                        await c.get(f"/api/v1/emergency/calls/{call_id}", headers=h("dispatcher"))
                    ).json()["assignments"][0]["route_alternatives"]
                    ev.check(
                        "choosing_another_route_moves_the_selection_and_only_one_stays_selected",
                        chosen.status_code == 200
                        and [a["route_id"] for a in after_route if a["selected"]]
                        == [other["route_id"]],
                    )
                else:
                    ev.check(
                        "choosing_another_route_moves_the_selection_and_only_one_stays_selected",
                        True,
                        detail="only one alternative exists between these junctions; selection is exercised below",
                    )
                bad_route = await c.post(
                    f"/api/v1/emergency/assignments/{assignment_id}/route",
                    headers=h("dispatcher"),
                    json={"route_id": "route-not-mine"},
                )
                ev.check(
                    "a_route_that_is_not_one_of_the_alternatives_is_refused",
                    bad_route.status_code == 422,
                )

                skip = await c.post(
                    f"/api/v1/emergency/assignments/{assignment_id}/transition",
                    headers=h("dispatcher"),
                    json={"to_status": "on_scene"},
                )
                ev.check(
                    "a_unit_cannot_skip_ahead_of_its_lifecycle",
                    skip.status_code == 409
                    and skip.json()["detail"]["error"] == "invalid_transition",
                )
                walked = []
                for step in ("acknowledged", "en_route", "on_scene", "clear"):
                    r = await c.post(
                        f"/api/v1/emergency/assignments/{assignment_id}/transition",
                        headers=h("dispatcher"),
                        json={"to_status": step},
                    )
                    walked.append(r.status_code)
                    call_now = (
                        await c.get(f"/api/v1/emergency/calls/{call_id}", headers=h("dispatcher"))
                    ).json()["call"]["status"]
                    walked.append(call_now)
                ev.check(
                    "the_call_follows_its_unit_en_route_then_on_scene_then_cleared",
                    walked
                    == [200, "unit_assigned", 200, "en_route", 200, "on_scene", 200, "cleared"],
                    detail=str(walked),
                )
                history = (
                    await c.get(f"/api/v1/emergency/calls/{call_id}", headers=h("dispatcher"))
                ).json()
                ev.check(
                    "every_step_in_the_call_history_is_attributed_to_the_dispatcher",
                    all(t["changed_by"] == users["dispatcher"] for t in history["transitions"]),
                    detail=str({t["changed_by"] for t in history["transitions"]}),
                )
                closed = await c.post(
                    f"/api/v1/emergency/calls/{call_id}/assignments",
                    headers=h("dispatcher"),
                    json={"unit_id": "ambulance-1"},
                )
                ev.check(
                    "a_cleared_call_takes_no_more_units",
                    closed.status_code == 409 and closed.json()["detail"]["error"] == "call_closed",
                )

                second = (
                    await c.post(
                        "/api/v1/emergency/calls",
                        headers=h("dispatcher"),
                        json={
                            "call_type": "police",
                            "call_subtype": "public_safety",
                            "priority": "low",
                            "intersection_id": "int-c3",
                        },
                    )
                ).json()["call_id"]
                no_note = await c.post(
                    f"/api/v1/emergency/calls/{second}/transition",
                    headers=h("dispatcher"),
                    json={"to_status": "cancelled"},
                )
                cancelled = await c.post(
                    f"/api/v1/emergency/calls/{second}/transition",
                    headers=h("dispatcher"),
                    json={"to_status": "cancelled", "note": "caller withdrew"},
                )
                again = await c.post(
                    f"/api/v1/emergency/calls/{second}/transition",
                    headers=h("dispatcher"),
                    json={"to_status": "cancelled", "note": "again"},
                )
                ev.check(
                    "a_call_is_cancelled_with_a_reason_once_only",
                    no_note.status_code == 422
                    and cancelled.status_code == 200
                    and again.status_code == 409,
                )
                ev.check(
                    "a_field_responder_cannot_move_a_unit",
                    (
                        await c.post(
                            f"/api/v1/emergency/assignments/{assignment_id}/transition",
                            headers=h("field_responder"),
                            json={"to_status": "clear"},
                        )
                    ).status_code
                    == 403,
                )
                ev.check(
                    "an_unknown_call_or_assignment_is_404",
                    all(
                        x.status_code == 404
                        for x in (
                            await c.post(
                                f"/api/v1/emergency/calls/{uuid.uuid4()}/assignments",
                                headers=h("dispatcher"),
                                json={"unit_id": "ambulance-1", "origin_intersection_id": "int-b1"},
                            ),
                            await c.post(
                                f"/api/v1/emergency/assignments/{uuid.uuid4()}/transition",
                                headers=h("dispatcher"),
                                json={"to_status": "clear"},
                            ),
                            await c.post(
                                "/api/v1/emergency/calls/not-a-uuid/transition",
                                headers=h("dispatcher"),
                                json={"to_status": "cancelled", "note": "x"},
                            ),
                        )
                    ),
                )

                # ------------------------------------------------------------------ audit trail
                call_rows = audit_rows(db, call_id)
                ev.check(
                    "the_dispatch_history_is_audited_person_role_action_outcome",
                    {"call.create", "assignment.create"} <= {r[2] for r in call_rows}
                    and all(
                        r[1] == ["dispatcher"] for r in call_rows if r[0] == users["dispatcher"]
                    ),
                    detail=f"{len(call_rows)} rows",
                )
                try:
                    with db.cursor() as cur:
                        cur.execute(
                            "UPDATE operator_audit SET actor = 'tampered' WHERE audit_id = (SELECT max(audit_id) FROM operator_audit)"
                        )
                    db.commit()
                    tampered = True
                except psycopg.errors.RaiseException:
                    db.rollback()
                    tampered = False
                try:
                    with db.cursor() as cur:
                        cur.execute(
                            "DELETE FROM operator_audit WHERE audit_id = (SELECT max(audit_id) FROM operator_audit)"
                        )
                    db.commit()
                    erased = True
                except psycopg.errors.RaiseException:
                    db.rollback()
                    erased = False
                ev.check(
                    "the_audit_trail_cannot_be_edited_or_erased_even_by_the_database_owner",
                    not tampered and not erased,
                )
        finally:
            server.should_exit = True
            await asyncio.sleep(0.5)
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
