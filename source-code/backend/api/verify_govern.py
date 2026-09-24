"""P08.09 acceptance evidence (API side): the audit trail, platform status and shift handover - against the real Keycloak, Postgres and
FastAPI app under uvicorn, with a real access token per named demo person:

    python source-code/backend/api/verify_govern.py

What is proven: the audit trail is readable by the auditor only, is newest-first, filters by actor, entity and time, pages without gaps
or repeats, records refusals as well as actions, and cannot be edited or deleted; platform status comes from probes made at request
time (a refused connection is `down`, a worker that never reported is `unknown`, a stale heartbeat or source is called stale) and the
stale-after budgets equal the ones the map and device screens use; a handover names its author from the token, resolves what each open
item is from the database, is acknowledged by a different person, exactly once, even when two people try at the same moment.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg
import uvicorn
from psycopg.types.json import Jsonb

os.environ.pop("AIOPS_AUTH_MODE", None)

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.api import oidc_client, routes_govern  # noqa: E402
from backend.api.app import app  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.repositories import incidents as incident_repo  # noqa: E402
from database.migrate import dsn_from_env, migrate  # noqa: E402

HOST, PORT = "127.0.0.1", 8814
BASE = f"http://{HOST}:{PORT}"
GEOMETRY = "2026-09-18.1"
IDENTITIES = json.loads(
    (SOURCE_ROOT / "infra" / "platform" / "output" / "demo_identities.json").read_text(
        encoding="utf-8"
    )
)
ev = Evidence("P08.09")
PEOPLE = {
    "operator": "alex.chen",
    "operator2": "alex.two",
    "supervisor": "sam.okafor",
    "supervisor2": "sam.two",
    "dispatcher": "dana.rivera",
    "commander": "eve.laurent",
    "field": "fin.hassan",
    "auditor": "ana.petrov",
    "demo": "dee.moreno",
}
PROBE_TYPE = "govern_probe_sensor"


def run_server() -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(80):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("uvicorn did not start")


def insert_device(db: psycopg.Connection, device_id: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO devices (device_id, device_type, deployment_type, agency_scope, geometry_version, location, status, registered_at, privacy_classification, retention_class) "
            "VALUES (%s, %s, 'simulated', 'city-traffic-ops', %s, ST_SetSRID(ST_MakePoint(74.3587, 31.5204), 4326)::geography, 'active', now(), 'none', 'standard') "
            "ON CONFLICT (device_id) DO UPDATE SET status = 'active', device_type = EXCLUDED.device_type",
            (device_id, PROBE_TYPE, GEOMETRY),
        )
    db.commit()


def insert_event(
    db: psycopg.Connection, device_id: str, observed_ago_s: float, received_ago_s: float = 0.0
) -> None:
    now = datetime.now(timezone.utc)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO observation_events (event_id, event_type, device_id, agency_scope, observation_time, ingest_time, sequence_number, clock_quality, geometry_version, location, "
            "measurements, truth_label, privacy_classification, retention_class, content_sha256, received_at) VALUES "
            "(%s, 'traffic.loop_detector.count', %s, 'city-traffic-ops', %s, %s, %s, 'synced', %s, ST_SetSRID(ST_MakePoint(74.3587, 31.5204), 4326)::geography, %s, 'simulated', 'none', 'standard', %s, %s)",
            (
                str(uuid.uuid4()),
                device_id,
                now - timedelta(seconds=observed_ago_s),
                now - timedelta(seconds=observed_ago_s),
                int(time.time() * 1000) % 2**40,
                GEOMETRY,
                Jsonb(
                    [
                        {
                            "name": "vehicle_count",
                            "value": 1,
                            "unit": "count",
                            "quality": "valid",
                            "confidence": 0.9,
                        }
                    ]
                ),
                str(uuid.uuid4()),
                now - timedelta(seconds=received_ago_s),
            ),
        )
    db.commit()


def key() -> str:
    return f"verify-{uuid.uuid4().hex}"


def by_id(items: list[dict], wanted: str) -> dict:
    return next(i for i in items if i["id"] == wanted)


async def main() -> int:  # noqa: PLR0915
    tokens = {
        name: oidc_client.login(user, IDENTITIES[user]["password"]).access_token
        for name, user in PEOPLE.items()
    }

    def h(who: str) -> dict:
        return {"Authorization": f"Bearer {tokens[who]}"}

    with psycopg.connect(dsn_from_env()) as db:
        migrate(db)
        started_at = datetime.now(timezone.utc) - timedelta(seconds=2)
        incident_id = incident_repo.create_incident(
            db,
            "congestion",
            "high",
            "segment",
            "int-a2_int-a3",
            GEOMETRY,
            [str(uuid.uuid4())],
            "OPS",
            0.8,
            "verify-fixture",
        )
        call_id = str(uuid.uuid4())
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO emergency_calls (call_id, call_type, call_subtype, priority, location, geometry_version, reported_at, source_reliability, status, truth_label) "
                "VALUES (%s, 'ambulance', 'unspecified', 'high', ST_SetSRID(ST_MakePoint(74.3587, 31.5204), 4326)::geography, %s, now(), 'verified_dispatch', 'received', 'simulated')",
                (call_id, GEOMETRY),
            )
        db.commit()
        server = run_server()
        try:
            async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
                # ------------------------------------------------------------------ real actions, so the trail has something to say
                sign = await c.post(
                    "/api/v1/commands",
                    headers=h("operator"),
                    json={
                        "action_type": "variable_message_sign",
                        "target_entity_id": "int-a1_int-a2",
                        "message": "verify",
                        "idempotency_key": key(),
                    },
                )
                command_id = sign.json()["command_id"]
                await c.post(
                    f"/api/v1/incidents/{incident_id}/transition",
                    headers=h("operator"),
                    json={"to_status": "acknowledged"},
                )
                own_approval = await c.post(
                    f"/api/v1/commands/{command_id}/review",
                    headers=h("operator"),
                    json={"decision": "approve"},
                )
                colleague = await c.post(
                    f"/api/v1/commands/{command_id}/review",
                    headers=h("operator2"),
                    json={"decision": "approve"},
                )
                ev.check(
                    "setup_a_command_was_requested_the_requester_could_not_approve_it_and_a_colleague_decided_it",
                    sign.status_code == 201
                    and own_approval.status_code == 403
                    and colleague.status_code == 200,
                    detail=f"{sign.status_code} {own_approval.status_code} {colleague.status_code}",
                )

                # ------------------------------------------------------------------ audit: who may read it
                statuses = {
                    who: (await c.get("/api/v1/audit", headers=h(who))).status_code
                    for who in PEOPLE
                }
                ev.check(
                    "only_the_auditor_role_can_read_the_audit_trail",
                    statuses["auditor"] == 200
                    and all(s == 403 for who, s in statuses.items() if who != "auditor"),
                    detail=str(statuses),
                )
                ev.check(
                    "an_unauthenticated_request_for_the_trail_is_refused",
                    (await c.get("/api/v1/audit")).status_code == 401,
                )
                first = (
                    await c.get("/api/v1/audit", headers=h("auditor"), params={"limit": 200})
                ).json()
                trail = first["items"]
                refusals = [
                    i
                    for i in trail
                    if i["source"] == "operator"
                    and i["outcome"] == "denied"
                    and i["entity_type"] == "endpoint"
                    and i["action"] == "GET /api/v1/audit"
                ]
                ev.check(
                    "refusals_are_in_the_trail_with_the_person_and_their_role",
                    {i["actor"] for i in refusals}
                    >= {PEOPLE["operator"], PEOPLE["field"], PEOPLE["demo"]}
                    and all(i["actor_roles"] for i in refusals),
                    detail=f"{len(refusals)} refusals",
                )
                sources = {i["source"] for i in trail}
                ev.check(
                    "the_trail_unifies_operator_actions_and_state_histories",
                    {"operator", "command_history", "incident_history"} <= sources,
                    detail=str(sorted(sources)),
                )
                times = [i["at"] for i in trail]
                ev.check(
                    "newest_first",
                    times == sorted(times, reverse=True) and len(times) > 5,
                    detail=f"{len(times)} rows",
                )

                # ------------------------------------------------------------------ audit: filters
                by_actor = (
                    await c.get(
                        "/api/v1/audit",
                        headers=h("auditor"),
                        params={"actor": "ALEX.CH", "limit": 200},
                    )
                ).json()["items"]
                ev.check(
                    "filter_by_actor_is_a_case_insensitive_partial_match_and_only_that_actor",
                    by_actor and all(i["actor"] == PEOPLE["operator"] for i in by_actor),
                    detail=f"{len(by_actor)} rows",
                )
                by_entity = (
                    await c.get(
                        "/api/v1/audit",
                        headers=h("auditor"),
                        params={"entity_type": "command", "entity_id": command_id, "limit": 200},
                    )
                ).json()["items"]
                ev.check(
                    "filter_by_entity_finds_that_commands_actions_and_its_state_history_together",
                    {i["source"] for i in by_entity} >= {"operator", "command_history"}
                    and all(i["entity_id"] == command_id for i in by_entity),
                    detail=str([(i["source"], i["action"], i["outcome"]) for i in by_entity]),
                )
                only_denied = (
                    await c.get(
                        "/api/v1/audit",
                        headers=h("auditor"),
                        params={"outcome": "denied", "limit": 200},
                    )
                ).json()["items"]
                ev.check(
                    "filter_by_outcome",
                    only_denied and all(i["outcome"] == "denied" for i in only_denied),
                )
                future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
                ev.check(
                    "filter_by_time_since_the_future_is_empty",
                    (
                        await c.get("/api/v1/audit", headers=h("auditor"), params={"since": future})
                    ).json()["items"]
                    == [],
                )
                recent = (
                    await c.get(
                        "/api/v1/audit",
                        headers=h("auditor"),
                        params={"since": started_at.isoformat(), "limit": 200},
                    )
                ).json()["items"]
                ev.check(
                    "filter_by_time_since_the_start_of_this_run_finds_its_rows_and_nothing_older",
                    recent
                    and all(
                        i["at"] >= started_at.isoformat().replace("+00:00", "Z")[:19]
                        for i in recent
                    ),
                    detail=f"{len(recent)} rows",
                )
                past = (
                    await c.get(
                        "/api/v1/audit",
                        headers=h("auditor"),
                        params={"until": "2000-01-01T00:00:00Z"},
                    )
                ).json()["items"]
                ev.check("filter_by_time_until_the_past_is_empty", past == [])
                bad_range = await c.get(
                    "/api/v1/audit",
                    headers=h("auditor"),
                    params={"since": future, "until": "2000-01-01T00:00:00Z"},
                )
                bad_time = await c.get(
                    "/api/v1/audit", headers=h("auditor"), params={"since": "yesterday-ish"}
                )
                ev.check(
                    "a_reversed_or_malformed_time_range_is_refused_with_a_reason",
                    bad_range.status_code == 422 and bad_time.status_code == 422,
                    detail=f"{bad_range.status_code} {bad_time.status_code}",
                )
                wildcard = (
                    await c.get("/api/v1/audit", headers=h("auditor"), params={"actor": "%"})
                ).json()["items"]
                ev.check(
                    "a_percent_sign_in_the_actor_filter_is_text_not_a_wildcard", wildcard == []
                )

                # ------------------------------------------------------------------ audit: pagination is stable
                bound = datetime.now(timezone.utc).isoformat()

                async def walk(size: int) -> tuple[list[str], int]:
                    ids_, cursor_, pages_ = [], None, 0
                    while True:
                        page_ = (
                            await c.get(
                                "/api/v1/audit",
                                headers=h("auditor"),
                                params={
                                    "until": bound,
                                    "limit": size,
                                    **({"cursor": cursor_} if cursor_ else {}),
                                },
                            )
                        ).json()
                        ids_ += [i["audit_id"] for i in page_["items"]]
                        pages_ += 1
                        cursor_ = page_["next_cursor"]
                        if cursor_ is None or pages_ > 1000:
                            return ids_, pages_

                big, big_pages = await walk(200)
                small, small_pages = await walk(7)
                ev.check(
                    "cursor_pagination_walks_the_whole_trail_without_gaps_or_repeats",
                    small == big and len(set(small)) == len(small) and small_pages > big_pages,
                    detail=f"{len(small)} rows: {small_pages} pages of 7, {big_pages} of 200",
                )
                ev.check(
                    "a_malformed_cursor_is_refused",
                    (
                        await c.get(
                            "/api/v1/audit", headers=h("auditor"), params={"cursor": "not-a-cursor"}
                        )
                    ).status_code
                    == 400,
                )

                # ------------------------------------------------------------------ audit: cannot be edited
                blocked = {}
                for name, sql in (
                    ("update", "UPDATE operator_audit SET actor = 'nobody'"),
                    ("delete", "DELETE FROM operator_audit"),
                    ("truncate", "TRUNCATE operator_audit"),
                ):
                    try:
                        with db.cursor() as cur:
                            cur.execute(sql)
                        db.commit()
                        blocked[name] = False
                    except psycopg.errors.RaiseException:
                        db.rollback()
                        blocked[name] = True
                ev.check(
                    "the_operator_audit_table_refuses_update_delete_and_truncate",
                    all(blocked.values()),
                    detail=str(blocked),
                )
                with (
                    db.cursor() as cur
                ):  # a row-level trigger only fires when there is a row to change
                    cur.execute(
                        "INSERT INTO scenario_control_audit (action, role, outcome, actor) VALUES ('status', 'verify', 'allowed', 'verify-govern')"
                    )
                db.commit()
                blocked = {}
                for name, sql in (
                    ("update", "UPDATE scenario_control_audit SET actor = 'nobody'"),
                    ("delete", "DELETE FROM scenario_control_audit"),
                    ("truncate", "TRUNCATE scenario_control_audit"),
                ):
                    try:
                        with db.cursor() as cur:
                            cur.execute(sql)
                        db.commit()
                        blocked[name] = False
                    except psycopg.errors.RaiseException:
                        db.rollback()
                        blocked[name] = True
                ev.check(
                    "the_scenario_control_audit_table_refuses_update_delete_and_truncate",
                    all(blocked.values()),
                    detail=str(blocked),
                )
                verbs = {}
                for verb in ("POST", "PUT", "PATCH", "DELETE"):
                    verbs[verb] = (
                        await c.request(verb, "/api/v1/audit", headers=h("auditor"))
                    ).status_code
                ev.check(
                    "there_is_no_endpoint_that_changes_the_trail",
                    all(code in (403, 405) for code in verbs.values()),
                    detail=str(verbs),
                )

                # ------------------------------------------------------------------ platform status: who and what
                allowed = {
                    who: (await c.get("/api/v1/ops/status", headers=h(who))).status_code
                    for who in PEOPLE
                }
                ev.check(
                    "platform_status_is_for_operators_supervisors_commanders_and_auditors",
                    {w for w, s in allowed.items() if s == 200}
                    == {
                        "operator",
                        "operator2",
                        "supervisor",
                        "supervisor2",
                        "commander",
                        "auditor",
                    },
                    detail=str(allowed),
                )
                with db.cursor() as cur:
                    cur.execute("DELETE FROM service_heartbeats")
                    cur.execute("DELETE FROM ingestion_rejections WHERE event_id LIKE 'govern-%'")
                db.commit()
                status = (await c.get("/api/v1/ops/status", headers=h("supervisor"))).json()
                services = {s["id"]: s for s in status["services"]}
                ev.check(
                    "the_probes_are_real_and_the_running_stack_answers",
                    all(
                        services[i]["status"] == "healthy"
                        for i in ("api", "database", "identity-provider", "kafka", "mqtt")
                    )
                    and services["database"]["latency_ms"] is not None,
                    detail=str({i: (s["status"], s["latency_ms"]) for i, s in services.items()}),
                )
                ev.check(
                    "a_worker_that_never_reported_is_unknown_not_healthy",
                    all(
                        services[i]["status"] == "unknown" and services[i]["age_s"] is None
                        for i in ("demo-feeder", "command-executor", "outcome-verifier")
                    ),
                )
                ev.check(
                    "the_overall_verdict_counts_unknowns_and_is_not_a_blanket_ok",
                    status["counts"]["unknown"] == 3 and status["counts"]["healthy"] >= 5,
                    detail=str(status["counts"]),
                )
                os.environ["AIOPS_KAFKA_ADDR"] = "127.0.0.1:1"
                broken = {
                    s["id"]: s
                    for s in (await c.get("/api/v1/ops/status", headers=h("supervisor"))).json()[
                        "services"
                    ]
                }
                del os.environ["AIOPS_KAFKA_ADDR"]
                ev.check(
                    "a_probe_that_cannot_connect_says_down_with_the_reason_and_the_others_still_report",
                    broken["kafka"]["status"] == "down"
                    and broken["kafka"]["detail"]
                    and broken["mqtt"]["status"] == "healthy",
                    detail=broken["kafka"]["detail"],
                )

                async def worker_state(service: str, age_s: float | None) -> dict:
                    with db.cursor() as cur:
                        cur.execute("DELETE FROM service_heartbeats WHERE service = %s", (service,))
                        if age_s is not None:
                            cur.execute(
                                "INSERT INTO service_heartbeats (service, last_seen, detail) VALUES (%s, now() - make_interval(secs => %s), %s)",
                                (service, age_s, Jsonb({"executed_this_run": 3})),
                            )
                    db.commit()
                    return by_id(
                        (await c.get("/api/v1/ops/status", headers=h("supervisor"))).json()[
                            "services"
                        ],
                        service,
                    )

                healthy, late, gone = (
                    await worker_state("command-executor", 2),
                    await worker_state("command-executor", 25),
                    await worker_state("command-executor", 400),
                )
                ev.check(
                    "a_worker_heartbeat_is_healthy_when_recent_degraded_when_late_and_down_when_gone",
                    (healthy["status"], late["status"], gone["status"])
                    == ("healthy", "degraded", "down"),
                    detail=f"{healthy['detail']} | {late['detail']} | {gone['detail']}",
                )
                ev.check(
                    "what_a_worker_reported_is_passed_through",
                    healthy.get("reported") == {"executed_this_run": 3},
                )

                # ------------------------------------------------------------------ platform status: data freshness and ingestion
                for device in ("govern-fresh", "govern-stale", "govern-never"):
                    insert_device(db, device)
                insert_event(db, "govern-fresh", 5)
                insert_event(db, "govern-stale", 1200)
                sources = by_id(
                    (await c.get("/api/v1/ops/status", headers=h("supervisor"))).json()["sources"],
                    PROBE_TYPE,
                )
                ev.check(
                    "source_freshness_counts_reporting_stale_and_never_reported_devices",
                    (sources["devices"], sources["reporting"], sources["stale"], sources["never"])
                    == (3, 1, 1, 1)
                    and sources["status"] == "fresh",
                    detail=str(sources),
                )
                with db.cursor() as cur:
                    cur.execute("DELETE FROM observation_events WHERE device_id = 'govern-fresh'")
                db.commit()
                stale_only = by_id(
                    (await c.get("/api/v1/ops/status", headers=h("supervisor"))).json()["sources"],
                    PROBE_TYPE,
                )
                ev.check(
                    "a_source_whose_newest_reading_is_past_its_budget_is_stale_with_its_age",
                    stale_only["status"] == "stale"
                    and stale_only["age_s"] >= 1190
                    and stale_only["budget_s"] == 300,
                    detail=str(stale_only),
                )
                with db.cursor() as cur:
                    cur.execute("DELETE FROM observation_events WHERE device_id LIKE 'govern-%'")
                db.commit()
                none_at_all = by_id(
                    (await c.get("/api/v1/ops/status", headers=h("supervisor"))).json()["sources"],
                    PROBE_TYPE,
                )
                ev.check(
                    "a_source_that_never_reported_is_unknown_and_no_age_is_invented",
                    none_at_all["status"] == "unknown"
                    and none_at_all["age_s"] is None
                    and none_at_all["newest_observation_time"] is None,
                )

                with db.cursor() as cur:
                    cur.execute(
                        "SELECT count(*) FROM observation_events WHERE received_at > now() - interval '5 minutes'"
                    )
                    quiet = cur.fetchone()[0] == 0
                if quiet:
                    empty = (await c.get("/api/v1/ops/status", headers=h("supervisor"))).json()[
                        "ingestion"
                    ]
                    ev.check(
                        "with_nothing_received_ingestion_is_unknown_and_no_rate_or_lag_is_invented",
                        empty["status"] == "unknown"
                        and empty["lag_p50_s"] is None
                        and empty["events"] == 0,
                        detail=str(empty),
                    )
                for _ in range(20):
                    insert_event(db, "govern-fresh", 4)
                measured = (await c.get("/api/v1/ops/status", headers=h("supervisor"))).json()[
                    "ingestion"
                ]
                ev.check(
                    "ingestion_reports_what_arrived_and_how_long_after_it_was_observed",
                    measured["events"] >= 20
                    and measured["status"] == "healthy"
                    and (not quiet or 3.5 <= measured["lag_p50_s"] <= 5.5),
                    detail=str(measured),
                )
                for _ in range(40):
                    insert_event(db, "govern-fresh", 100)
                lagging = (await c.get("/api/v1/ops/status", headers=h("supervisor"))).json()[
                    "ingestion"
                ]
                ev.check(
                    "ingestion_lag_over_the_allowance_is_degraded",
                    lagging["status"] == "degraded" and lagging["lag_p95_s"] > 30,
                    detail=str(lagging),
                )
                with db.cursor() as cur:
                    cur.execute(
                        "INSERT INTO ingestion_rejections (event_id, reason, detail) VALUES ('govern-1', 'unknown_device', 'verify')"
                    )
                db.commit()
                rejected = (await c.get("/api/v1/ops/status", headers=h("supervisor"))).json()[
                    "ingestion"
                ]["rejected"]
                ev.check(
                    "rejected_events_are_counted_by_reason",
                    rejected.get("unknown_device", 0) >= 1,
                    detail=str(rejected),
                )
                with db.cursor() as cur:
                    cur.execute("DELETE FROM observation_events WHERE device_id LIKE 'govern-%'")
                    cur.execute("DELETE FROM devices WHERE device_id LIKE 'govern-%'")
                    cur.execute("DELETE FROM ingestion_rejections WHERE event_id LIKE 'govern-%'")
                    cur.execute("DELETE FROM service_heartbeats")
                db.commit()

                model = (
                    SOURCE_ROOT / "frontend" / "src" / "features" / "map" / "model.ts"
                ).read_text(encoding="utf-8")
                block = re.search(
                    r"DEVICE_STALE_AFTER_S[^=]*=\s*\{(.*?)\};", model, re.DOTALL
                ).group(1)
                frontend = {k: int(v) for k, v in re.findall(r"(\w+):\s*(\d+)", block)}
                ev.check(
                    "the_stale_after_budgets_equal_the_ones_the_map_and_device_screens_use",
                    frontend == routes_govern.DEVICE_STALE_AFTER_S,
                    detail=f"{frontend} vs {routes_govern.DEVICE_STALE_AFTER_S}",
                )

                # ------------------------------------------------------------------ handover: create
                good = {
                    "outgoing_shift": "Day",
                    "incoming_shift": "Night",
                    "summary": "Corridor A slow after the stalled vehicle; sign message is up.",
                    "open_items": [
                        {"kind": "incident", "ref": incident_id, "note": "Watch the queue"},
                        {"kind": "command", "ref": command_id},
                        {"kind": "call", "ref": call_id},
                        {"kind": "other", "note": "Camera 4 is being replaced at 22:00"},
                    ],
                }
                made = await c.post("/api/v1/handovers", headers=h("operator"), json=good)
                created = made.json()
                labels = {i["kind"]: i for i in created.get("open_items", [])}
                with db.cursor() as cur:
                    cur.execute("SELECT status FROM commands WHERE command_id = %s", (command_id,))
                    command_status = cur.fetchone()[0]
                ev.check(
                    "a_handover_names_its_author_from_the_token_and_the_server_says_what_each_item_is",
                    made.status_code == 201
                    and created["author"] == PEOPLE["operator"]
                    and created["status"] == "awaiting_acknowledgement"
                    and labels["incident"]["status"] == "acknowledged"
                    and "congestion" in labels["incident"]["label"]
                    and labels["command"]["status"] == command_status
                    and "call" in labels["call"]["label"]
                    and labels["other"]["ref"] is None,
                    detail=made.text[:300],
                )
                ev.check(
                    "the_client_cannot_name_the_author",
                    (
                        await c.post(
                            "/api/v1/handovers",
                            headers=h("operator"),
                            json={**good, "author": "someone.else"},
                        )
                    ).status_code
                    == 422,
                )
                refusals = {
                    "unknown_incident": {
                        **good,
                        "open_items": [{"kind": "incident", "ref": str(uuid.uuid4())}],
                    },
                    "not_a_uuid": {**good, "open_items": [{"kind": "command", "ref": "abc"}]},
                    "no_ref": {**good, "open_items": [{"kind": "call"}]},
                    "other_without_note": {**good, "open_items": [{"kind": "other"}]},
                    "same_shift": {**good, "incoming_shift": "day"},
                    "blank_summary": {**good, "summary": "   "},
                    "too_many_items": {**good, "open_items": [{"kind": "other", "note": "x"}] * 21},
                }
                results = {
                    name: (
                        await c.post("/api/v1/handovers", headers=h("operator"), json=body)
                    ).status_code
                    for name, body in refusals.items()
                }
                ev.check(
                    "an_incomplete_or_untrue_handover_is_refused_with_a_reason",
                    all(s == 422 for s in results.values()),
                    detail=str(results),
                )
                writers = {
                    who: (
                        await c.post(
                            "/api/v1/handovers", headers=h(who), json={**good, "open_items": []}
                        )
                    ).status_code
                    for who in ("supervisor", "dispatcher", "commander", "field", "auditor", "demo")
                }
                ev.check(
                    "only_roles_with_handover_write_can_write_a_handover",
                    writers
                    == {
                        "supervisor": 201,
                        "dispatcher": 201,
                        "commander": 201,
                        "field": 403,
                        "auditor": 403,
                        "demo": 403,
                    },
                    detail=str(writers),
                )
                readers = {
                    who: (await c.get("/api/v1/handovers", headers=h(who))).status_code
                    for who in PEOPLE
                }
                ev.check(
                    "every_operating_role_can_read_handovers_but_not_the_demo_operator",
                    {w for w, s in readers.items() if s == 200} == set(PEOPLE) - {"demo"},
                    detail=str(readers),
                )

                # ------------------------------------------------------------------ handover: list
                listed = (
                    await c.get("/api/v1/handovers", headers=h("supervisor"), params={"limit": 100})
                ).json()["items"]
                created_times = [i["created_at"] for i in listed]
                ev.check(
                    "handovers_list_newest_first_and_include_the_new_one",
                    created_times == sorted(created_times, reverse=True)
                    and any(i["handover_id"] == created["handover_id"] for i in listed),
                )
                pages, cursor, seen = 0, None, []
                while True:
                    page = (
                        await c.get(
                            "/api/v1/handovers",
                            headers=h("supervisor"),
                            params={"limit": 2, **({"cursor": cursor} if cursor else {})},
                        )
                    ).json()
                    seen += [i["handover_id"] for i in page["items"]]
                    pages += 1
                    cursor = page["next_cursor"]
                    if cursor is None or pages > 100:
                        break
                ev.check(
                    "handover_pagination_has_no_gaps_or_repeats",
                    len(seen) == len(set(seen))
                    and set(seen) >= {created["handover_id"]}
                    and pages >= 2,
                    detail=f"{len(seen)} in {pages} pages",
                )

                # ------------------------------------------------------------------ handover: acknowledge
                own = await c.post(
                    f"/api/v1/handovers/{created['handover_id']}/acknowledge", headers=h("operator")
                )
                ev.check(
                    "the_author_cannot_acknowledge_their_own_handover",
                    own.status_code == 403 and own.json()["detail"]["error"] == "own_handover",
                    detail=own.text[:200],
                )
                for who in ("field", "auditor", "demo"):
                    r = await c.post(
                        f"/api/v1/handovers/{created['handover_id']}/acknowledge", headers=h(who)
                    )
                    ev.check(
                        f"a_{who}_cannot_acknowledge_it_because_they_do_not_hold_handover_write",
                        r.status_code == 403 and r.json()["detail"]["error"] == "forbidden",
                        detail=r.text[:160],
                    )
                ok = await c.post(
                    f"/api/v1/handovers/{created['handover_id']}/acknowledge",
                    headers=h("supervisor"),
                )
                ev.check(
                    "the_incoming_person_acknowledges_by_name",
                    ok.status_code == 200
                    and ok.json()["acknowledged_by"] == PEOPLE["supervisor"]
                    and ok.json()["status"] == "acknowledged"
                    and ok.json()["acknowledged_at"],
                    detail=ok.text[:200],
                )
                again = await c.post(
                    f"/api/v1/handovers/{created['handover_id']}/acknowledge",
                    headers=h("supervisor2"),
                )
                ev.check(
                    "a_second_acknowledgement_is_a_conflict_that_names_who_already_did",
                    again.status_code == 409
                    and again.json()["detail"]["acknowledged_by"] == PEOPLE["supervisor"],
                    detail=again.text[:200],
                )
                missing = [
                    (
                        await c.post(f"/api/v1/handovers/{x}/acknowledge", headers=h("supervisor"))
                    ).status_code
                    for x in (str(uuid.uuid4()), "not-a-uuid")
                ]
                ev.check(
                    "acknowledging_an_unknown_or_malformed_handover_is_404",
                    missing == [404, 404],
                    detail=str(missing),
                )
                awaiting = (
                    await c.get(
                        "/api/v1/handovers",
                        headers=h("supervisor"),
                        params={"status": "awaiting_acknowledgement", "limit": 100},
                    )
                ).json()["items"]
                done = (
                    await c.get(
                        "/api/v1/handovers",
                        headers=h("supervisor"),
                        params={"status": "acknowledged", "limit": 100},
                    )
                ).json()["items"]
                ev.check(
                    "the_status_filter_separates_awaiting_from_acknowledged",
                    all(i["acknowledged_by"] is None for i in awaiting)
                    and any(i["handover_id"] == created["handover_id"] for i in done)
                    and all(i["acknowledged_by"] for i in done),
                )
                second = (
                    await c.post(
                        "/api/v1/handovers", headers=h("operator"), json={**good, "open_items": []}
                    )
                ).json()
                raced = await asyncio.gather(
                    *[
                        c.post(
                            f"/api/v1/handovers/{second['handover_id']}/acknowledge", headers=h(who)
                        )
                        for who in ("supervisor", "supervisor2", "commander", "dispatcher")
                    ]
                )
                codes = sorted(r.status_code for r in raced)
                with db.cursor() as cur:
                    cur.execute(
                        "SELECT acknowledged_by FROM shift_handovers WHERE handover_id = %s",
                        (second["handover_id"],),
                    )
                    winner = cur.fetchone()[0]
                ev.check(
                    "four_people_acknowledging_at_once_produce_exactly_one_acknowledgement",
                    codes == [200, 409, 409, 409]
                    and winner in {"sam.okafor", "sam.two", "eve.laurent", "dana.rivera"},
                    detail=f"{codes} winner={winner}",
                )
                with db.cursor() as cur:
                    cur.execute(
                        "SELECT count(*) FROM operator_audit WHERE entity_type = 'handover' AND entity_id = %s",
                        (created["handover_id"],),
                    )
                    audited = cur.fetchone()[0]
                trail = (
                    await c.get(
                        "/api/v1/audit",
                        headers=h("auditor"),
                        params={
                            "entity_type": "handover",
                            "entity_id": created["handover_id"],
                            "limit": 50,
                        },
                    )
                ).json()["items"]
                actions = sorted((i["actor"], i["action"], i["outcome"]) for i in trail)
                ev.check(
                    "handover_actions_are_in_the_audit_trail_including_the_refusals",
                    (PEOPLE["operator"], "handover.acknowledge", "denied") in actions
                    and (PEOPLE["supervisor"], "handover.acknowledge", "allowed") in actions
                    and (PEOPLE["operator"], "handover.create", "allowed") in actions
                    and (PEOPLE["supervisor2"], "handover.acknowledge", "denied") in actions
                    and audited >= 4,
                    detail=str(actions),
                )
        finally:
            server.should_exit = True
            await asyncio.sleep(0.5)
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
