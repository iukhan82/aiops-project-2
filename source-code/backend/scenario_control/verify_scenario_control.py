"""P05.09 / P08.09 acceptance evidence, run against the real Keycloak, the real Postgres and the real P03.07 manifest output:

    python source-code/backend/scenario_control/verify_scenario_control.py

The scenario-control API used to trust a role header the caller wrote. It now takes a verified Keycloak token and the `demo.control`
capability. This proves: no token, a forged header, a tampered token, and every operating role are refused (and the refusals are in
the API's own audit trail, with the person); the demo operator can start, replay and reset a run, and the run says who started it;
the concurrent-run bound is enforced under genuinely concurrent requests; replay is the real P03.07 mechanism (same accepted set every
time); the demo controls change no operational record; the demo operator's token reaches nothing in the operational API; and the
trail cannot be rewritten.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path

import httpx
import psycopg
import uvicorn

os.environ.pop("AIOPS_AUTH_MODE", None)

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.api import oidc_client  # noqa: E402
from backend.api.app import app as operator_app  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.scenario_control.app import MAX_CONCURRENT_RUNS, app as demo_app  # noqa: E402
from database.migrate import dsn_from_env, migrate  # noqa: E402

HOST = "127.0.0.1"
DEMO, OPERATOR = f"http://{HOST}:8792", f"http://{HOST}:8793"
IDENTITIES = json.loads(
    (SOURCE_ROOT / "infra" / "platform" / "output" / "demo_identities.json").read_text(
        encoding="utf-8"
    )
)
PEOPLE = {
    "demo": "dee.moreno",
    "operator": "alex.chen",
    "supervisor": "sam.okafor",
    "dispatcher": "dana.rivera",
    "commander": "eve.laurent",
    "field": "fin.hassan",
    "auditor": "ana.petrov",
}
ev = Evidence("P08.09", name="p08_09_demo_controls")


def serve(application, port: int) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(application, host=HOST, port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(80):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("uvicorn did not start in time")


def counts(db: psycopg.Connection) -> dict[str, int]:
    out = {}
    with db.cursor() as cur:
        for table in (
            "operator_audit",
            "commands",
            "incidents",
            "observation_events",
            "command_transitions",
            "incident_transitions",
            "emergency_calls",
        ):
            cur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed names
            out[table] = cur.fetchone()[0]
    db.rollback()
    return out


async def main() -> int:  # noqa: PLR0915
    tokens = {
        name: oidc_client.login(user, IDENTITIES[user]["password"]).access_token
        for name, user in PEOPLE.items()
    }

    def h(who: str) -> dict:
        return {"Authorization": f"Bearer {tokens[who]}"}

    with psycopg.connect(dsn_from_env()) as db:
        migrate(db)
        db.commit()
        with db.cursor() as cur:
            cur.execute("UPDATE scenario_runs SET status = 'reset' WHERE status = 'running'")
        db.commit()
        demo_server, operator_server = serve(demo_app, 8792), serve(operator_app, 8793)
        try:
            async with (
                httpx.AsyncClient(base_url=DEMO, timeout=60) as demo,
                httpx.AsyncClient(base_url=OPERATOR, timeout=60) as operator,
            ):
                ev.check(
                    "health_is_public_and_says_which_service_it_is",
                    (await demo.get("/scenario-control/v1/health")).json().get("service")
                    == "scenario-control",
                )

                # ------------------------------------------------------------------ identity
                none = await demo.post("/scenario-control/v1/runs")
                header_only = await demo.post(
                    "/scenario-control/v1/runs", headers={"X-Demo-Role": "demo_operator"}
                )
                ev.check(
                    "a_request_with_no_token_is_401_and_the_role_header_is_not_an_identity",
                    none.status_code == 401
                    and header_only.status_code == 401
                    and header_only.json()["detail"]["error"] == "unauthenticated",
                    detail=header_only.text[:200],
                )
                forged = await demo.post(
                    "/scenario-control/v1/runs",
                    headers={**h("operator"), "X-Demo-Role": "demo_operator"},
                )
                ev.check(
                    "a_forged_role_header_beside_an_operators_token_changes_nothing",
                    forged.status_code == 403 and forged.json()["detail"]["error"] == "forbidden",
                    detail=forged.text[:200],
                )
                head, payload, signature = tokens["demo"].split(".")
                tampered = f"{head}.{payload}.{signature[:-4]}{'AAAA' if not signature.endswith('AAAA') else 'BBBB'}"
                bad = await demo.post(
                    "/scenario-control/v1/runs", headers={"Authorization": f"Bearer {tampered}"}
                )
                ev.check(
                    "a_token_with_a_tampered_signature_is_401",
                    bad.status_code == 401,
                    detail=bad.text[:160],
                )
                refused = {}
                for who in (
                    "operator",
                    "supervisor",
                    "dispatcher",
                    "commander",
                    "field",
                    "auditor",
                ):
                    refused[who] = (
                        await demo.post("/scenario-control/v1/runs", headers=h(who))
                    ).status_code
                ev.check(
                    "no_operating_role_can_start_a_demo_run",
                    all(code == 403 for code in refused.values()),
                    detail=str(refused),
                )
                listing = {}
                for who in ("operator", "supervisor", "auditor"):
                    listing[f"runs:{who}"] = (
                        await demo.get("/scenario-control/v1/runs", headers=h(who))
                    ).status_code
                    listing[f"audit:{who}"] = (
                        await demo.get("/scenario-control/v1/audit", headers=h(who))
                    ).status_code
                ev.check(
                    "no_operating_role_can_even_list_runs_or_read_the_demo_trail",
                    all(code == 403 for code in listing.values()),
                    detail=str(listing),
                )

                # ------------------------------------------------------------------ the demo operator's run
                before = counts(db)
                started = time.monotonic()
                r = await demo.post("/scenario-control/v1/runs", headers=h("demo"))
                run = r.json()
                run_id = run["run_id"]
                ev.check(
                    "the_demo_operator_starts_a_run_that_replays_the_real_p03_07_manifest",
                    r.status_code == 200
                    and run["status"] == "completed"
                    and run["result"]["accepted_count"] > 0,
                    detail=f"{ {k: v for k, v in run['result'].items() if k != 'accepted_ids'} } in {time.monotonic() - started:.1f}s",
                )
                ev.check(
                    "the_run_says_who_started_it_by_name_from_the_token",
                    run["started_by"] == PEOPLE["demo"],
                    detail=run["started_by"],
                )
                first_sha = run["result"]["accepted_sha256"]
                detail = await demo.get(f"/scenario-control/v1/runs/{run_id}", headers=h("demo"))
                ev.check(
                    "the_run_can_be_read_back",
                    detail.status_code == 200 and detail.json()["run_id"] == run_id,
                )
                unknown = [
                    (
                        await demo.get(f"/scenario-control/v1/runs/{x}", headers=h("demo"))
                    ).status_code
                    for x in ("00000000-0000-0000-0000-000000000000", "not-a-uuid")
                ]
                ev.check(
                    "an_unknown_or_malformed_run_id_is_404",
                    unknown == [404, 404],
                    detail=str(unknown),
                )
                replay = await demo.post(
                    f"/scenario-control/v1/runs/{run_id}/replay", headers=h("demo")
                )
                ev.check(
                    "replay_is_deterministic_same_accepted_set",
                    replay.status_code == 200
                    and replay.json()["result"]["accepted_sha256"] == first_sha,
                    detail=f"{first_sha[:12]} {replay.json()['result']['accepted_sha256'][:12]}",
                )
                denied_replay = await demo.post(
                    f"/scenario-control/v1/runs/{run_id}/replay", headers=h("auditor")
                )
                ev.check(
                    "replay_and_reset_by_another_role_are_refused",
                    denied_replay.status_code == 403
                    and (
                        await demo.post(
                            f"/scenario-control/v1/runs/{run_id}/reset", headers=h("supervisor")
                        )
                    ).status_code
                    == 403,
                )
                reset = await demo.post(
                    f"/scenario-control/v1/runs/{run_id}/reset", headers=h("demo")
                )
                ev.check(
                    "reset_by_the_demo_operator_succeeds",
                    reset.status_code == 200 and reset.json()["status"] == "reset",
                )
                listed = (await demo.get("/scenario-control/v1/runs", headers=h("demo"))).json()
                ev.check(
                    "the_run_list_names_the_bound_and_includes_the_run",
                    any(i["run_id"] == run_id for i in listed["items"])
                    and listed["bound"]["max_concurrent"] == MAX_CONCURRENT_RUNS
                    and listed["bound"]["running"] == 0,
                    detail=str(listed["bound"]),
                )
                after = counts(db)
                ev.check(
                    "the_demo_controls_wrote_nothing_to_the_operational_tables",
                    before == after,
                    detail=f"before={before} after={after}",
                )

                # ------------------------------------------------------------------ its own audit trail
                trail = (
                    await demo.get(
                        "/scenario-control/v1/audit", headers=h("demo"), params={"limit": 200}
                    )
                ).json()["items"]
                mine = [
                    (i["actor"], i["action"], i["outcome"]) for i in trail if i["run_id"] == run_id
                ]
                ev.check(
                    "the_demo_trail_records_each_action_with_the_person",
                    (PEOPLE["demo"], "start", "allowed") in mine
                    and (PEOPLE["demo"], "replay", "allowed") in mine
                    and (PEOPLE["demo"], "reset", "allowed") in mine
                    and (PEOPLE["auditor"], "replay", "denied_role") in mine
                    and (PEOPLE["supervisor"], "reset", "denied_role") in mine,
                    detail=str(mine),
                )
                denials = {(i["actor"], i["outcome"]) for i in trail}
                ev.check(
                    "the_demo_trail_records_refused_requests_too_including_those_with_no_identity",
                    ("<anonymous>", "denied_identity") in denials
                    and (PEOPLE["operator"], "denied_role") in denials,
                    detail=str(sorted(denials, key=str)),
                )
                times = [i["at"] for i in trail]
                ev.check("the_demo_trail_is_newest_first", times == sorted(times, reverse=True))
                with db.cursor() as cur:
                    cur.execute(
                        "SELECT count(*) FROM operator_audit WHERE actor = %s AND action LIKE '%%scenario-control%%'",
                        (PEOPLE["demo"],),
                    )
                    leaked = cur.fetchone()[0]
                db.rollback()
                ev.check("the_demo_actions_are_not_in_the_operator_audit_trail", leaked == 0)

                # ------------------------------------------------------------------ the bound, under real concurrency
                with db.cursor() as cur:
                    cur.execute(
                        "UPDATE scenario_runs SET status = 'reset' WHERE status = 'running'"
                    )
                db.commit()
                responses = await asyncio.gather(
                    *[
                        demo.post("/scenario-control/v1/runs", headers=h("demo"))
                        for _ in range(MAX_CONCURRENT_RUNS + 1)
                    ]
                )
                statuses = sorted(x.status_code for x in responses)
                bound = next((x.json()["detail"] for x in responses if x.status_code == 429), {})
                ev.check(
                    "concurrent_starts_are_bounded_and_the_refusal_says_why",
                    statuses.count(200) <= MAX_CONCURRENT_RUNS
                    and statuses.count(429) >= 1
                    and bound.get("error") == "run_bound_reached"
                    and bound.get("max_concurrent") == MAX_CONCURRENT_RUNS,
                    detail=f"statuses={statuses} {bound}",
                )
                trail = (
                    await demo.get(
                        "/scenario-control/v1/audit", headers=h("demo"), params={"limit": 50}
                    )
                ).json()["items"]
                ev.check(
                    "the_refused_start_is_in_the_trail_as_a_bound",
                    any(
                        i["outcome"] == "denied_bound" and i["actor"] == PEOPLE["demo"]
                        for i in trail
                    ),
                )

                # ------------------------------------------------------------------ the two identities do not cross
                crossings = {}
                for label, method, path, body in (
                    (
                        "commands",
                        "POST",
                        "/api/v1/commands",
                        {
                            "action_type": "variable_message_sign",
                            "target_entity_id": "int-a1_int-a2",
                            "message": "x",
                            "idempotency_key": "demo-cross-1",
                        },
                    ),
                    ("incidents", "GET", "/api/v1/incidents", None),
                    ("recommendations", "GET", "/api/v1/recommendations", None),
                    ("outcomes", "GET", "/api/v1/outcomes", None),
                    ("audit", "GET", "/api/v1/audit", None),
                    ("status", "GET", "/api/v1/ops/status", None),
                    ("handovers", "GET", "/api/v1/handovers", None),
                ):
                    crossings[label] = (
                        await operator.request(method, path, headers=h("demo"), json=body)
                    ).status_code
                picture = (
                    await operator.get("/api/v1/devices", headers=h("demo"), params={"limit": 1})
                ).status_code
                ev.check(
                    "the_demo_operators_token_reaches_nothing_operational_it_only_sees_the_picture",
                    all(code == 403 for code in crossings.values()) and picture == 200,
                    detail=f"{crossings} devices={picture}",
                )
                ev.check(
                    "an_operators_token_is_not_accepted_by_the_demo_controls",
                    refused["operator"] == 403 and refused["supervisor"] == 403,
                )
        finally:
            demo_server.should_exit = True
            operator_server.should_exit = True
            await asyncio.sleep(0.5)
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
