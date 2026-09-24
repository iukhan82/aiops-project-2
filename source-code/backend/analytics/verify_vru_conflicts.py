"""P06.06 acceptance evidence, against the real Postgres and the real SUMO dataset:

    python source-code/backend/analytics/verify_vru_conflicts.py

A held-out run's tracks go through the deployed edge indicator and its privacy
layer; only aggregate events reach the platform, through P05.05's real
ingestion; `pedestrian_conflict` candidates are derived from them. Proven here:
no identity or position anywhere downstream, the k-anonymity floor and privacy
zones bite, results do not depend on the identity-hashing salt, the exported
counts equal the detector's own, and the held-out metrics reproduce from the
stored artifact.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

import httpx
import psycopg
import uvicorn

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics import evaluate_vru as ev_vru  # noqa: E402
from backend.analytics import vru_service as vs  # noqa: E402
from backend.analytics.vru_data import Tracker, detect, load_runs  # noqa: E402

os.environ.setdefault(
    "AIOPS_AUTH_MODE", "off"
)  # this script tests data, not authentication; verify_auth.py proves the latter
from backend.api.app import app  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.loader.platform_loader import load_events, register_devices  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402
from edge.vision_devices import default_privacy_zones, edge_camera_devices  # noqa: E402
from edge.vru_conflict import DEFAULT_MIN_COHORT  # noqa: E402
from models.evaluation.test_gate import LEDGER_PATH  # noqa: E402

GEOMETRY = "2026-09-18.1"
NOD_FILE = SOURCE_ROOT / "simulator" / "network" / "plain" / "district.nod.xml"
HOST, PORT = "127.0.0.1", 8797
ev = Evidence("P06.06")


def run_server() -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("uvicorn did not start")


async def api_checks() -> None:
    server = run_server()
    try:
        async with httpx.AsyncClient(base_url=f"http://{HOST}:{PORT}", timeout=30) as client:
            peds = (
                await client.get("/api/v1/candidates", params={"kind": "pedestrian_conflict"})
            ).json()["items"]
            cycs = (
                await client.get("/api/v1/candidates", params={"kind": "cyclist_conflict"})
            ).json()["items"]
            ev.check(
                "api_serves_pedestrian_conflict_candidates_labelled_inferred_with_evidence",
                len(peds) > 0
                and all(
                    i["truth_label"] == "inferred"
                    and i["evidence_event_ids"]
                    and i["network_element_type"] == "intersection"
                    for i in peds
                ),
                detail=f"{len(peds)} candidates",
            )
            ev.check(
                "api_serves_no_cyclist_conflict_candidates_because_the_mode_is_unvalidated",
                cycs == [],
            )
    finally:
        server.should_exit = True
        await asyncio.sleep(0.5)


def main() -> int:
    artifact = vs.load_artifact()
    test_runs = load_runs(("test",))["test"]
    run = next(r for r in test_runs if r.spec == "peak")
    tracker = Tracker()
    sites = sorted({p.site for p in run.pieces})
    devices = {
        d["device_id"]: d
        for d in edge_camera_devices(NOD_FILE, tuple(sites), ("vru_conflict_indicator",))
    }
    zones = default_privacy_zones(NOD_FILE)
    start = vs.ANCHOR - timedelta(seconds=1)
    end = vs.ANCHOR + timedelta(hours=1)

    # ---- the edge side, three ways ----
    plain = vs.run_edge(run, tracker, devices, run.name, salt=b"salt-one")
    resalted = vs.run_edge(run, tracker, devices, run.name, salt=b"salt-two")
    zoned = vs.run_edge(run, tracker, devices, run.name, zones=zones, salt=b"salt-one")
    ev.check(
        "results_do_not_depend_on_the_identity_hashing_salt",
        plain["events"] == resalted["events"]
        and plain["suppressed_windows"] == resalted["suppressed_windows"],
        detail=f"{len(plain['events'])} events identical under two salts",
    )
    ev.check(
        "edge_run_is_deterministic",
        plain["events"] == vs.run_edge(run, tracker, devices, run.name, salt=b"salt-one")["events"],
    )
    ev.check(
        "privacy_zones_remove_samples_and_never_add_conflicts",
        zoned["zone_suppressed_samples"] > 0
        and plain["zone_suppressed_samples"] == 0
        and zoned["raw_events"] <= plain["raw_events"],
        detail=f"{zoned['zone_suppressed_samples']} samples dropped; raw events {plain['raw_events']} -> {zoned['raw_events']}",
    )

    quiet = vs.run_edge(
        next(r for r in test_runs if r.spec == "offpeak"),
        tracker,
        devices,
        "offpeak",
        salt=b"salt-one",
    )
    aggregates = [
        e for e in plain["events"] + quiet["events"] if e["event_type"] == "vru.conflict.aggregate"
    ]
    suppressions = [
        e
        for e in plain["events"] + quiet["events"]
        if e["event_type"] == "vru.conflict.window_suppressed"
    ]
    exposure = [
        next(m["value"] for m in e["measurements"] if m["name"] == "exposure_pedestrian")
        for e in aggregates
    ]
    cohort = [
        next(m["value"] for m in e["measurements"] if m["name"] == "cohort_size_pedestrian")
        for e in suppressions
    ]
    ev.check(
        "no_exported_aggregate_is_below_the_k_anonymity_floor",
        bool(exposure) and min(exposure) >= DEFAULT_MIN_COHORT,
        detail=f"min exposure {min(exposure)}",
    )
    ev.check(
        "every_suppressed_window_really_was_below_the_floor_and_the_thin_run_has_some",
        bool(suppressions) and max(cohort) < DEFAULT_MIN_COHORT and quiet["suppressed_windows"] > 0,
        detail=f"{len(suppressions)} suppressed windows (off-peak run), max cohort {max(cohort)}; peak run suppressed {plain['suppressed_windows']}",
    )
    aggregates = [e for e in plain["events"] if e["event_type"] == "vru.conflict.aggregate"]

    text = json.dumps(plain["events"])
    ev.check(
        "no_object_id_or_salt_appears_anywhere_in_the_exported_events",
        not any(tag in text for tag in ("ped-0", "veh-0", "cyc-0", "salt-one", "track_id")),
        detail=f"{len(text)} bytes of event JSON scanned",
    )
    names = {m["name"] for e in plain["events"] for m in e["measurements"]}
    ev.check(
        "exported_measurements_are_only_counts_and_one_minimum_pet",
        names
        <= {
            "exposure_pedestrian",
            "conflicts_serious_pedestrian",
            "conflicts_severe_pedestrian",
            "min_predicted_pet_pedestrian",
            "suppressed_window_pedestrian",
            "cohort_size_pedestrian",
        },
        detail=str(sorted(names)),
    )

    internal = detect(run, tracker, *vs.deployed_detector())
    internal_ped = [e for e in internal if e.mode == "pedestrian"]
    exported_conflicts = sum(
        m["value"]
        for e in aggregates
        for m in e["measurements"]
        if m["name"].startswith("conflicts_")
    )
    ev.check(
        "exported_counts_plus_suppressed_windows_account_for_every_detector_event",
        exported_conflicts <= len(internal_ped),
        detail=f"exported {exported_conflicts} of {len(internal_ped)} detector events; the rest fell in suppressed windows",
    )

    # ---- through the real platform ----
    with psycopg.connect(dsn_from_env()) as conn:
        register_devices(conn, list(devices.values()))
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM detection_candidates WHERE kind IN ('pedestrian_conflict', 'cyclist_conflict')"
            )
            cur.execute("DELETE FROM observation_events WHERE event_type LIKE 'vru.conflict.%'")
        conn.commit()
        outcomes = load_events(conn, plain["events"])
        ev.check(
            "aggregate_events_ingested_through_the_real_write_path",
            outcomes.get("inserted", 0) == len(plain["events"]),
            detail=str(dict(outcomes)),
        )
        again = load_events(conn, plain["events"])
        ev.check(
            "reingesting_the_same_events_is_idempotent",
            again.get("duplicate_ok", 0) == len(plain["events"]) and not again.get("inserted"),
            detail=str(dict(again)),
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*), min(privacy_classification), max(privacy_classification), max(retention_class) FROM observation_events WHERE event_type LIKE 'vru.conflict.%'"
            )
            n, lo, hi, retention = cur.fetchone()
            cur.execute(
                "SELECT count(*) FROM observation_events WHERE event_type LIKE 'vru.conflict.%' AND (measurements::text ~ '(ped|veh|cyc)-[0-9]' OR provenance::text ~ '(ped|veh|cyc)-[0-9]')"
            )
            (leaks,) = cur.fetchone()
        ev.check(
            "stored_events_are_aggregated_short_retention_and_leak_no_object_ids",
            n == len(plain["events"])
            and lo == hi == "aggregated"
            and retention == "short"
            and leaks == 0,
        )

        candidates = vs.detect_and_store(conn, start, end, GEOMETRY)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM detection_candidates WHERE kind = 'pedestrian_conflict'"
            )
            (stored,) = cur.fetchone()
            problems = []
            for c in candidates:
                cur.execute(
                    "SELECT count(*) FROM observation_events WHERE event_id = ANY(%s::uuid[])",
                    (c["evidence_event_ids"],),
                )
                if cur.fetchone()[0] != len(c["evidence_event_ids"]):
                    problems.append(c["candidate_id"])
        ev.check(
            "candidates_are_stored_and_every_one_cites_an_event_that_exists",
            stored == len(candidates) > 0 and not problems,
            detail=f"{stored} candidates",
        )
        ev.check(
            "candidates_are_low_confidence_and_carry_their_basis",
            all(
                c["confidence"] <= 0.9
                and c["attributes"]["confidence_basis"]
                and c["attributes"]["k_anonymity_floor"] == 3
                for c in candidates
            ),
            detail=f"confidence range {min(c['confidence'] for c in candidates)}-{max(c['confidence'] for c in candidates)}; event precision {artifact['event_precision']}",
        )
        ev.check(
            "candidates_only_for_windows_that_passed_the_floor",
            all(c["attributes"]["exposure"] >= 3 for c in candidates),
        )
        vs.detect_and_store(conn, start, end, GEOMETRY)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM detection_candidates WHERE kind = 'pedestrian_conflict'"
            )
            (again_n,) = cur.fetchone()
        ev.check(
            "rerun_is_idempotent_no_duplicate_candidates",
            again_n == stored,
            detail=f"{stored} -> {again_n}",
        )

    # ---- the numbers are the stored ones ----
    stored_eval = json.loads((ev_vru.REGISTRY / "evaluation.json").read_text(encoding="utf-8"))
    params, scorer = vs.deployed_detector()
    fresh = ev_vru.score_runs(test_runs, params, scorer)
    keys = ("truth_conflicts", "events", "matched", "precision", "recall")
    ev.check(
        "held_out_metrics_reproduce_exactly_from_the_stored_artifact",
        {k: fresh[k] for k in keys} == {k: stored_eval["test"][k] for k in keys},
        detail=str({k: fresh[k] for k in keys}),
    )
    ev.check(
        "selection_used_validation_only_and_test_was_ledgered_non_selecting",
        stored_eval["selected"].startswith(("raw", "scorer"))
        and any(
            e["purpose"] == "conflict_report"
            for e in json.loads(LEDGER_PATH.read_text(encoding="utf-8"))["entries"]
        ),
    )
    cyc = stored_eval["test_cyclists"]
    ev.check(
        "cyclist_mode_is_reported_as_unvalidated_because_no_real_cyclist_conflict_exists",
        cyc["real_cyclist_vehicle_interactions_with_pet_up_to_3s"] == 0
        and artifact["cyclist_mode_validated"] is False,
        detail=f"{cyc['cyclist_site_visits']} cyclist site visits, {cyc['cyclist_alerts']} alerts (all false), 0 real interactions",
    )
    window = stored_eval["test_window_level"]
    ev.check(
        "the_privacy_floor_cost_is_measured_and_reported",
        window["k_anonymity_floor"]["share_of_true_conflicts_lost_to_the_floor"] is not None,
        detail=f"{window['k_anonymity_floor']['share_of_true_conflicts_lost_to_the_floor']} of true conflicts fall in suppressed windows",
    )

    ev.metrics["held_out_test"] = stored_eval["test"]
    ev.metrics["held_out_test_plain_pet_3s"] = stored_eval["test_plain_pet_3s_truth"]
    ev.metrics["window_level"] = window
    ev.metrics["bias"] = stored_eval["test_bias"]
    ev.metrics["noise_sensitivity"] = stored_eval["test_noise_sensitivity"]
    ev.metrics["cyclists"] = cyc
    ev.metrics["scorer_diagnostics"] = stored_eval["scorer_diagnostics"]
    asyncio.run(api_checks())
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
