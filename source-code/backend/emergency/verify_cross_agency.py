"""P07.03 acceptance evidence, against the real Postgres:

    python source-code/backend/emergency/verify_cross_agency.py

A three-agency traffic-collision call (police perimeter, fire extrication
staged behind police, ambulance staged behind fire) is dispatched through
the real repository. Proves: a unit asked to go on-scene before its
prerequisite arrives is blocked (`staged`, not on_scene, and the attempt
raises); it proceeds once released; handovers chain police -> fire -> EMS
with required fields; the call's own derived timeline (P07.01's adapter)
stays consistent with a multi-unit call; nothing here ever carries a
patient/medical field.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.emergency.cad_avl_adapter import plan_call_steps  # noqa: E402
from backend.emergency.cross_agency import (  # noqa: E402
    HandoverStep,
    StagingStep,
    StagingViolation,
    advance_to_scene,
    plan_assignments,
    record_handover,
    release_staged,
)  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.repositories import emergency as repo  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

GEOMETRY = "2026-09-18.1"
LOCATION = {"latitude": 31.523, "longitude": 74.359}
ev = Evidence("P07.03")


def route(rid: str, eta: float) -> dict:
    return {
        "route_id": rid,
        "distance_m": eta * 10,
        "eta_seconds": eta,
        "eta_uncertainty_seconds": eta * 0.15,
        "geometry_version": GEOMETRY,
        "selected": True,
    }


def main() -> int:
    with psycopg.connect(dsn_from_env()) as conn:
        t0 = datetime.now(timezone.utc)
        call_id = repo.create_call(
            conn,
            "police",
            "traffic_collision",
            "high",
            LOCATION,
            GEOMETRY,
            "verified_dispatch",
            "simulated",
            "test:synthetic",
            at=t0,
        )
        repo.transition_call(conn, call_id, "dispatched", "test:synthetic", at=t0)
        repo.transition_call(conn, call_id, "unit_assigned", "test:synthetic", at=t0)

        steps = [
            StagingStep(
                "police-perimeter-1", "police-dispatch", ["traffic_control"], route("r1", 60)
            ),
            StagingStep(
                "fire-extrication-1",
                "fire-dispatch",
                ["extrication"],
                route("r2", 90),
                after="police-perimeter-1",
            ),
            StagingStep(
                "ambulance-transport-1",
                "ems-dispatch",
                ["als", "transport"],
                route("r3", 100),
                after="fire-extrication-1",
            ),
        ]
        assignments = plan_assignments(conn, call_id, steps, "simulated", t0)
        ev.check(
            "three_agency_assignments_created_in_dependency_order",
            len(assignments) == 3
            and {a["agency"] for a in repo.assignments_for_call(conn, call_id)}
            == {"police-dispatch", "fire-dispatch", "ems-dispatch"},
        )

        # Units are dispatched at once (all `assigned`); physically, fire and EMS happen to be ready to enter
        # before police has actually secured the perimeter - the real scenario staging exists to prevent.
        arrived: dict[str, bool] = {}
        fire_blocked = ambulance_blocked = False
        try:
            advance_to_scene(
                conn,
                assignments["fire-extrication-1"],
                "fire-extrication-1",
                "police-perimeter-1",
                arrived,
                t0 + timedelta(seconds=40),
            )
        except StagingViolation:
            fire_blocked = True
        try:
            advance_to_scene(
                conn,
                assignments["ambulance-transport-1"],
                "ambulance-transport-1",
                "fire-extrication-1",
                arrived,
                t0 + timedelta(seconds=45),
            )
        except StagingViolation:
            ambulance_blocked = True
        statuses = {a["unit_id"]: a["status"] for a in repo.assignments_for_call(conn, call_id)}
        ev.check(
            "fire_and_ambulance_are_both_blocked_from_on_scene_before_their_real_prerequisite_arrives",
            fire_blocked
            and ambulance_blocked
            and statuses["fire-extrication-1"] == "staged"
            and statuses["ambulance-transport-1"] == "staged",
        )
        history = repo.assignment_transition_history(conn, assignments["fire-extrication-1"])
        ev.check(
            "the_staged_attempt_is_recorded_in_the_audit_trail_not_silently_dropped",
            [h["to_status"] for h in history] == ["assigned", "acknowledged", "en_route", "staged"],
        )

        advance_to_scene(
            conn,
            assignments["police-perimeter-1"],
            "police-perimeter-1",
            None,
            arrived,
            t0 + timedelta(seconds=60),
        )
        ev.check(
            "the_unit_with_no_prerequisite_reaches_on_scene_directly",
            next(
                a
                for a in repo.assignments_for_call(conn, call_id)
                if a["unit_id"] == "police-perimeter-1"
            )["status"]
            == "on_scene",
        )

        release_staged(
            conn,
            assignments["fire-extrication-1"],
            "fire-extrication-1",
            t0 + timedelta(seconds=150),
            arrived,
        )
        ev.check(
            "fire_proceeds_on_scene_once_police_has_actually_arrived",
            next(
                a["status"]
                for a in repo.assignments_for_call(conn, call_id)
                if a["unit_id"] == "fire-extrication-1"
            )
            == "on_scene",
        )
        release_staged(
            conn,
            assignments["ambulance-transport-1"],
            "ambulance-transport-1",
            t0 + timedelta(seconds=220),
            arrived,
        )

        # ---- handover chain: police -> fire (perimeter no longer needed once fire secures the scene),
        # then fire -> EMS is implicit in EMS's own on-scene arrival; EMS -> fire on departure (scene remains fire's) ----
        record_handover(
            conn,
            assignments["police-perimeter-1"],
            HandoverStep(
                "police-perimeter-1",
                "fire-extrication-1",
                "police-dispatch",
                "fire-dispatch",
                "perimeter secured, handed to fire",
            ),
            t0 + timedelta(seconds=300),
        )
        record_handover(
            conn,
            assignments["ambulance-transport-1"],
            HandoverStep(
                "ambulance-transport-1",
                "fire-extrication-1",
                "ems-dispatch",
                "fire-dispatch",
                "transport departed, scene remains with fire",
            ),
            t0 + timedelta(seconds=400),
        )
        repo.transition_assignment(
            conn,
            assignments["fire-extrication-1"],
            "clear",
            "test:synthetic",
            "scene closed",
            at=t0 + timedelta(seconds=500),
        )
        repo.transition_call(
            conn, call_id, "en_route", "test:synthetic", at=t0 + timedelta(seconds=61)
        )
        repo.transition_call(
            conn, call_id, "on_scene", "test:synthetic", at=t0 + timedelta(seconds=62)
        )
        repo.transition_call(
            conn, call_id, "cleared", "test:synthetic", at=t0 + timedelta(seconds=501)
        )

        final = repo.assignments_for_call(conn, call_id)
        by_unit = {a["unit_id"]: a for a in final}
        ev.check(
            "handovers_persist_with_every_required_field_and_the_right_agencies",
            set(by_unit["police-perimeter-1"]["handover"])
            == {"from_agency", "to_agency", "handover_at", "acknowledged_by"}
            and by_unit["police-perimeter-1"]["handover"]["to_agency"] == "fire-dispatch"
            and by_unit["ambulance-transport-1"]["handover"]["from_agency"] == "ems-dispatch",
        )
        ev.check(
            "all_three_units_reached_a_terminal_status", {a["status"] for a in final} == {"clear"}
        )
        text = json.dumps(final, default=str)
        ev.check(
            "no_patient_or_medical_field_anywhere_in_the_case_file",
            not any(
                bad in text.lower()
                for bad in ("patient", "diagnosis", "medical_condition", "injury_detail")
            ),
        )

        # the call's own derived timeline (P07.01) stays coherent for a multi-unit call: on_scene once the
        # FIRST unit arrives (police, +60s), cleared once the LAST unit clears (fire, +500s)
        call_row = {"call_id": call_id, "reported_at": t0.isoformat()}
        assignment_rows = [
            {
                "assigned_at": a["assigned_at"].isoformat(),
                "acknowledged_at": a["acknowledged_at"].isoformat()
                if a["acknowledged_at"]
                else None,
                "arrived_at": a["arrived_at"].isoformat() if a["arrived_at"] else None,
                "cleared_at": a["cleared_at"].isoformat() if a["cleared_at"] else None,
            }
            for a in final
        ]
        derived = plan_call_steps(call_row, assignment_rows)
        on_scene_step = next(s for s in derived if s.to_status == "on_scene")
        cleared_step = next(s for s in derived if s.to_status == "cleared")
        # police is the first unit actually on scene (advance_to_scene calls it at +60s, and its own on_scene
        # transition lands at +30s past that = +90s); fire is the last to clear (+500s, after both handovers)
        ev.check(
            "the_derived_call_timeline_uses_the_first_arrival_and_the_last_clearance_across_three_agencies",
            abs((on_scene_step.at - (t0 + timedelta(seconds=90))).total_seconds()) < 1
            and abs((cleared_step.at - (t0 + timedelta(seconds=500))).total_seconds()) < 1,
            detail=f"on_scene at +{(on_scene_step.at - t0).total_seconds()}s, cleared at +{(cleared_step.at - t0).total_seconds()}s",
        )

        with conn.cursor() as cur:
            cur.execute("DELETE FROM emergency_calls WHERE call_id = %s", (call_id,))
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM emergency_calls WHERE call_id = %s", (call_id,))
            ev.check("synthetic_case_removed_after_the_test", cur.fetchone()[0] == 0)
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
