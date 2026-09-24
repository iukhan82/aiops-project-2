"""P07.04 acceptance evidence, against the real Postgres and the real seeded
network (P07.02):

    python source-code/backend/control/verify_recommendations.py

Proves both generators against real incidents (P06.07's repository) and real
KPI data (P06.01): diversion for a closed segment, using the real router;
signal-plan-change for a congested corridor, using real measured delay; hard
safety-bounds enforcement (an over-deviation alternative is genuinely dropped,
not merely flagged); superseding when a fresher recommendation replaces one;
expiry; the contract-shaped API.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg
import uvicorn
from jsonschema import Draft202012Validator
from psycopg.types.json import Jsonb

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

os.environ.setdefault(
    "AIOPS_AUTH_MODE", "off"
)  # this script tests data, not authentication; verify_auth.py proves the latter
from backend.api.app import app  # noqa: E402
from backend.control.diversion import DEFAULT_BOUNDS, build_diversion_recommendation  # noqa: E402
from backend.control.engine import Alternative, Metric, UnsafeAlternative, enforce  # noqa: E402
from backend.control.recommendation_service import recommend_for_incident  # noqa: E402
from backend.control.signal_plan import build_signal_recommendation  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.repositories import recommendations as reco_repo  # noqa: E402
from backend.repositories.incidents import create_incident  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

GEOMETRY = "2026-09-18.1"
SCHEMA = json.loads(
    (SOURCE_ROOT / "contracts" / "recommendation" / "v1" / "schema.json").read_text(
        encoding="utf-8"
    )
)
HOST, PORT = "127.0.0.1", 8801
ev = Evidence("P07.04")


def run_server() -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("uvicorn did not start")


async def api_checks(recommendation_id: str) -> None:
    server = run_server()
    try:
        async with httpx.AsyncClient(base_url=f"http://{HOST}:{PORT}", timeout=30) as client:
            page = (await client.get("/api/v1/recommendations", params={"limit": 500})).json()
            validator = Draft202012Validator(SCHEMA)
            errors = [e.message for item in page["items"] for e in validator.iter_errors(item)]
            ev.check(
                "api_recommendations_are_contract_valid",
                bool(page["items"]) and not errors,
                detail=f"{len(page['items'])} records; {errors[:1]}",
            )
            mine = [i for i in page["items"] if i["recommendation_id"] == recommendation_id]
            ev.check(
                "api_serves_the_recommendation_just_created_with_alternatives_and_bounds",
                bool(mine)
                and mine[0]["alternatives"]
                and set(mine[0]["safety_bounds"])
                == {"min_pedestrian_clearance_s", "max_signal_deviation_s"},
            )
    finally:
        server.should_exit = True
        await asyncio.sleep(0.5)


def main() -> int:
    with psycopg.connect(dsn_from_env()) as conn:
        now = datetime.now(timezone.utc)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM corridor_kpis WHERE window_start > %s", (now - timedelta(hours=1),)
            )
        conn.commit()

        # ---- diversion, on a real closed segment, using the real router ----
        closed_edge = "int-a1_int-a2"
        incident_id = create_incident(
            conn,
            "collision",
            "high",
            "segment",
            closed_edge,
            GEOMETRY,
            [str(uuid.uuid4())],
            "EMERG",
            0.9,
            "test:synthetic",
            at=now,
        )
        alternatives, constraints = build_diversion_recommendation(
            conn, closed_edge, "int-a1", "int-c4", GEOMETRY, now, DEFAULT_BOUNDS
        )
        ev.check(
            "diversion_generator_produces_alternatives_with_benefit_and_harm_from_the_real_router",
            len(alternatives) == 3
            and all(
                a.predicted_benefit or a.predicted_harm or a.description == "Take no action"
                for a in alternatives
            )
            and any("Divert traffic" in a.description for a in alternatives),
            detail=str(constraints),
        )
        divert = next(a for a in alternatives if a.description.startswith("Divert"))
        route_portion = divert.description.split(" onto ", 1)[1]
        ev.check(
            "diversion_route_avoids_the_closed_segment_because_it_reuses_the_real_router",
            closed_edge not in route_portion.split(" -> "),
        )

        rec_id = reco_repo.create_recommendation(
            conn,
            "diversion",
            [a.as_record() for a in alternatives],
            DEFAULT_BOUNDS.as_record(),
            constraints,
            now,
            now + timedelta(seconds=600),
            trigger_incident_id=incident_id,
        )
        stored = reco_repo.get(conn, rec_id)
        ev.check(
            "diversion_recommendation_persists_and_is_readable_back",
            stored is not None
            and stored["status"] == "proposed"
            and stored["action_type"] == "diversion"
            and len(stored["alternatives"]) == 3,
        )
        as_contract = {
            "schema_version": "1.0.0",
            "recommendation_id": str(stored["recommendation_id"]),
            "action_type": stored["action_type"],
            "generated_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=600)).isoformat(),
            "status": stored["status"],
            "alternatives": stored["alternatives"],
            "safety_bounds": stored["safety_bounds"],
        }
        errors = list(Draft202012Validator(SCHEMA).iter_errors(as_contract))
        ev.check(
            "stored_recommendation_is_contract_valid_end_to_end", not errors, detail=str(errors[:1])
        )

        # ---- a fresher recommendation for the same incident supersedes the first ----
        rec_id_2 = reco_repo.create_recommendation(
            conn,
            "diversion",
            [a.as_record() for a in alternatives],
            DEFAULT_BOUNDS.as_record(),
            constraints,
            now + timedelta(seconds=30),
            now + timedelta(seconds=630),
            trigger_incident_id=incident_id,
        )
        reco_repo.supersede(conn, rec_id, rec_id_2)
        ev.check(
            "superseding_marks_the_old_one_and_points_at_the_new_one",
            reco_repo.get(conn, rec_id)["status"] == "superseded"
            and str(reco_repo.get(conn, rec_id)["superseded_by"]) == rec_id_2,
        )
        ev.check(
            "only_one_recommendation_is_live_for_this_incident",
            len(reco_repo.active_for_trigger(conn, incident_id)) == 1
            and str(reco_repo.active_for_trigger(conn, incident_id)[0]["recommendation_id"])
            == rec_id_2,
        )

        # ---- expiry ----
        expiring = reco_repo.create_recommendation(
            conn,
            "diversion",
            [a.as_record() for a in alternatives],
            DEFAULT_BOUNDS.as_record(),
            constraints,
            now - timedelta(seconds=700),
            now - timedelta(seconds=100),
            trigger_incident_id=incident_id,
        )
        n = reco_repo.expire_stale(conn, now)
        ev.check(
            "a_recommendation_past_its_expiry_is_marked_expired",
            n >= 1 and reco_repo.get(conn, expiring)["status"] == "expired",
        )

        # ---- signal plan change, using a real, freshly-inserted corridor KPI ----
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO corridor_kpis (corridor_id, direction, window_start, window_seconds, geometry_version, kpis, quality, coverage, "
                "sample_count, segments_reporting, segments_expected) VALUES ('corridor-a', 'east', %s, 300, %s, %s::jsonb, 'valid', 1.0, 10, 1, 1)",
                (
                    now - timedelta(minutes=1),
                    GEOMETRY,
                    Jsonb(
                        {
                            "delay_s": 45.0,
                            "queue_fraction": 0.6,
                            "travel_time_s": 90.0,
                            "free_flow_travel_time_s": 45.0,
                        }
                    ),
                ),
            )
        conn.commit()
        signal_alts, signal_constraints = build_signal_recommendation(
            conn, "int-a2", "corridor-a", "east", GEOMETRY, now
        )
        ev.check(
            "signal_generator_uses_the_real_measured_corridor_delay",
            "measured-corridor-delay" in signal_constraints,
        )
        over_bound = [
            a for a in signal_alts if a.signal_deviation_s > DEFAULT_BOUNDS.max_signal_deviation_s
        ]
        ev.check(
            "no_alternative_exceeding_the_signal_deviation_bound_survives_enforcement",
            not over_bound,
            detail=f"{len(signal_alts)} alternatives kept, deviations {[a.signal_deviation_s for a in signal_alts]}",
        )
        ev.check(
            "pedestrian_clearance_is_preserved_on_every_signal_alternative",
            all(
                a.pedestrian_clearance_s is None
                or a.pedestrian_clearance_s >= DEFAULT_BOUNDS.min_pedestrian_clearance_s
                for a in signal_alts
            ),
        )

        # ---- unsafe-only input is genuinely rejected, not silently passed through ----
        unsafe = [
            Alternative(
                "x",
                "an unsafe alternative",
                (Metric("b", 1, "s"),),
                (Metric("h", 1, "s"),),
                0.5,
                signal_deviation_s=999.0,
            )
        ]
        rejected = False
        try:
            enforce(unsafe, DEFAULT_BOUNDS)
        except UnsafeAlternative:
            rejected = True
        ev.check("an_all_unsafe_alternative_set_is_rejected_outright", rejected)

        # ---- the service wiring: incident -> generator -> stored, superseding correctly ----
        service_rec = recommend_for_incident(
            conn,
            incident_id,
            "collision",
            "segment",
            closed_edge,
            GEOMETRY,
            now + timedelta(seconds=60),
            diversion_endpoints=("int-a1", "int-c4"),
        )
        ev.check(
            "the_service_generates_and_stores_a_recommendation_for_a_live_incident",
            service_rec is not None and reco_repo.get(conn, service_rec)["status"] == "proposed",
        )
        ev.check(
            "the_service_call_superseded_the_prior_live_recommendation_for_the_same_incident",
            reco_repo.get(conn, rec_id_2)["status"] == "superseded",
        )
        no_generator = recommend_for_incident(
            conn,
            incident_id,
            "wrong_way",
            "segment",
            closed_edge,
            GEOMETRY,
            now,
            diversion_endpoints=None,
        )
        ev.check(
            "an_incident_kind_with_no_applicable_generator_or_missing_context_returns_none_not_an_error",
            no_generator is None,
        )

        asyncio.run(api_checks(service_rec))

        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM recommendations WHERE trigger_incident_id = %s", (incident_id,)
            )
            cur.execute("DELETE FROM incidents WHERE incident_id = %s", (incident_id,))
            cur.execute(
                "DELETE FROM corridor_kpis WHERE corridor_id = 'corridor-a' AND direction = 'east' AND window_start = %s",
                (now - timedelta(minutes=1),),
            )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM incidents WHERE incident_id = %s", (incident_id,))
            ev.check(
                "synthetic_incident_and_recommendations_removed_after_the_test",
                cur.fetchone()[0] == 0,
            )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
