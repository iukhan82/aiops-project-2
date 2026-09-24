"""P06.05 acceptance evidence, against the real Postgres and the real dataset:

    python source-code/backend/analytics/verify_safety_candidates.py

A held-out blockage run and P03.05's overlay events go through P05.05's real
ingestion; stalled-vehicle candidates come from the real EdgeRuntime fused
with congestion, overlay candidates from their rules; everything lands in
`detection_candidates` with evidence, uncertainty and per-source parts. The
held-out fusion metrics reproduce from the stored parameters and meet the
gates; the overlay handling passes its specification test.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import psycopg
import uvicorn

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics import evaluate_stall as es  # noqa: E402
from backend.analytics import safety_service as ss  # noqa: E402
from backend.analytics.calibration import load_lane_shares  # noqa: E402
from backend.analytics.congestion import match_spans  # noqa: E402
from backend.analytics.evaluate_congestion import ANCHOR, DATASET, load_runs  # noqa: E402
from backend.analytics.forecast_data import read_events  # noqa: E402
from backend.analytics.stall_candidates import FusionParams, alarm_spans, fuse, run_edge_runtime  # noqa: E402
from backend.analytics.topology import apply_lane_share, load_segments_json  # noqa: E402

os.environ.setdefault(
    "AIOPS_AUTH_MODE", "off"
)  # this script tests data, not authentication; verify_auth.py proves the latter
from backend.api.app import app  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.loader.platform_loader import load_events, register_devices, reset_simulated_loops  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402
from models.evaluation.test_gate import LEDGER_PATH  # noqa: E402

GEOMETRY = "2026-09-18.1"
RUN = "test/p06-test-s20260925-am-peak-blockage"
SCEN = SOURCE_ROOT / "simulator" / "scenarios" / "output" / "run-a"
SENSORS = SOURCE_ROOT / "simulator" / "sensors" / "output" / "run-a"
HOST, PORT = "127.0.0.1", 8796
ev = Evidence("P06.05")


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


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
            kinds = set()
            for kind in (
                "stalled_vehicle",
                "collision",
                "wrong_way",
                "flooding",
                "low_visibility",
                "congestion",
            ):
                items = (await client.get("/api/v1/candidates", params={"kind": kind})).json()[
                    "items"
                ]
                if items:
                    kinds.add(kind)
                assert all(
                    i["kind"] == kind and i["evidence_event_ids"] and i["truth_label"] == "inferred"
                    for i in items
                )
            ev.check(
                "api_serves_every_kind_with_evidence_labelled_inferred",
                {
                    "stalled_vehicle",
                    "collision",
                    "wrong_way",
                    "flooding",
                    "low_visibility",
                    "congestion",
                }
                <= kinds,
                detail=str(sorted(kinds)),
            )
    finally:
        server.should_exit = True
        await asyncio.sleep(0.5)


def main() -> int:
    segments = apply_lane_share(load_segments_json(DATASET / "segments.json"), load_lane_shares())
    events = read_events(DATASET / RUN / "events.jsonl")
    incidents = [
        json.loads(line)
        for line in (DATASET / RUN / "incidents.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    devices = jsonl(DATASET / "devices.jsonl")
    sensor_devices = jsonl(SENSORS / "devices.jsonl")
    start = datetime.fromisoformat("2026-09-18T09:00:00+00:00")
    end = start + timedelta(hours=3)
    params = ss.load_fusion_params()

    with psycopg.connect(dsn_from_env()) as conn:
        register_devices(conn, devices + sensor_devices)
        reset_simulated_loops(conn)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM observation_events WHERE device_id LIKE 'road-condition-%' OR device_id LIKE 'weather-station-%' OR device_id LIKE 'signal-%'"
            )
        conn.commit()
        ev.check(
            "blockage_run_ingested_through_real_write_path",
            sum(load_events(conn, events).values()) == len(events),
        )
        overlay_events = jsonl(SCEN / "overlay_events.jsonl")
        overlay_outcomes = load_events(conn, overlay_events)
        ev.check(
            "p03_05_overlay_events_ingested_through_real_write_path",
            sum(v for k, v in overlay_outcomes.items() if k in ("inserted", "duplicate_ok"))
            == len(overlay_events),
            detail=str(dict(overlay_outcomes)),
        )

        # ---- stalled vehicle: DB path == file path, and it finds real incidents ----
        db_stall = ss.detect_stall(conn, start, end, GEOMETRY, DATASET / "devices.jsonl")
        derived = run_edge_runtime(
            events, DATASET / "devices.jsonl", ss.EDGE_BASELINE, ss.EDGE_MODEL, "p06"
        )
        from backend.analytics.congestion import Params, detect_congestion
        from backend.analytics.congestion_service import load_artifact

        cong = detect_congestion(events, segments, Params(**load_artifact()["params"]))
        file_stall = fuse(alarm_spans(derived), cong, params, GEOMETRY)
        norm = lambda cs: sorted(  # noqa: E731
            (
                c["candidate_id"],
                str(c["onset_time"]),
                round(c["confidence"], 4),
                tuple(c["evidence_event_ids"]),
            )
            for c in cs
        )  # noqa: E731
        ev.check(
            "db_stall_candidates_equal_file_stall_candidates",
            norm(db_stall) == norm(file_stall),
            detail=f"{len(db_stall)} candidates",
        )
        inc_spans = [
            (
                i["edge_id"],
                ANCHOR + timedelta(seconds=i["onset_s"]),
                ANCHOR + timedelta(seconds=i["end_s"]),
            )
            for i in incidents
            if i["valid"]
        ]
        cand_spans = [
            (
                c["network_element_id"],
                c["onset_time"],
                datetime.fromisoformat(c["attributes"]["last_alarm_at"]),
            )
            for c in db_stall
        ]
        pairs = match_spans(cand_spans, inc_spans)
        ev.check(
            "stall_candidates_find_real_measured_incidents_in_this_run",
            len(pairs) >= 1,
            detail=f"{len(pairs)} of {len(inc_spans)} incidents matched, {len(db_stall)} candidates",
        )
        ev.check(
            "every_stall_candidate_keeps_its_per_source_uncertainty",
            all(
                c["attributes"]["sources"][0]["source"] == "edge_model" and 0 < c["confidence"] <= 1
                for c in db_stall
            ),
        )
        multi = [c for c in db_stall if c["attributes"]["corroborated"]]
        ev.check(
            "corroboration_by_a_second_source_raises_confidence",
            all(
                len(c["attributes"]["sources"]) == 2
                and c["confidence"] > c["attributes"]["sources"][0]["mean_probability"]
                for c in multi
            ),
            detail=f"{len(multi)} corroborated of {len(db_stall)}",
        )
        abstain_only = [
            e
            for e in derived
            if any(m["name"] == "decision" and m["value"] == "abstain" for m in e["measurements"])
        ]
        ev.check(
            "edge_abstentions_never_raise_a_candidate",
            fuse(alarm_spans(abstain_only), cong, FusionParams(1, False), GEOMETRY) == [],
            detail=f"{len(abstain_only)} abstain events",
        )

        # ---- overlays through the DB ----
        db_overlay = ss.detect_overlays(
            conn,
            datetime.fromisoformat("2026-09-18T09:00:00+00:00"),
            datetime.fromisoformat("2026-09-18T09:20:00+00:00"),
            GEOMETRY,
        )
        got = {
            c["kind"]
            for c in db_overlay
            if c["kind"] in ("collision", "wrong_way", "flooding", "low_visibility")
        }
        ev.check(
            "all_four_overlay_kinds_detected_from_the_database",
            got == {"collision", "wrong_way", "flooding", "low_visibility"},
            detail=str(sorted(got)),
        )
        ev.check(
            "signal_fault_overlay_is_not_a_safety_candidate",
            all(c["kind"] != "signal_fault" for c in db_overlay)
            and len([c for c in db_overlay]) == 4,
        )

        # ---- evidence really exists ----
        problems = []
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM detection_candidates WHERE kind IN ('stalled_vehicle','collision','wrong_way','flooding','low_visibility')"
            )
            (n_safety,) = cur.fetchone()
            for c in db_stall + db_overlay:
                cur.execute(
                    "SELECT count(*) FROM observation_events WHERE event_id = ANY(%s::uuid[])",
                    (c["evidence_event_ids"],),
                )
                if cur.fetchone()[0] != len(set(c["evidence_event_ids"])):
                    problems.append(c["candidate_id"])
        ev.check("every_candidate_cites_events_that_exist", not problems, detail=str(problems[:2]))

        # a congestion detector run gives the API something in every kind
        from backend.analytics import congestion_service as cs

        cs.detect_and_store(conn, start, end, GEOMETRY)
        ss.detect_stall(conn, start, end, GEOMETRY, DATASET / "devices.jsonl")
        ss.detect_overlays(
            conn,
            datetime.fromisoformat("2026-09-18T09:00:00+00:00"),
            datetime.fromisoformat("2026-09-18T09:20:00+00:00"),
            GEOMETRY,
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM detection_candidates WHERE kind IN ('stalled_vehicle','collision','wrong_way','flooding','low_visibility')"
            )
            (n_again,) = cur.fetchone()
        ev.check(
            "rerun_is_idempotent_no_duplicate_candidates",
            n_safety == n_again,
            detail=f"{n_safety} -> {n_again}",
        )

    # ---- held-out reproducibility and gates ----
    stored = json.loads(
        (SOURCE_ROOT / "models" / "registry" / "stall-fusion" / "evaluation.json").read_text(
            encoding="utf-8"
        )
    )
    runs = load_runs(("test",))
    es.init_adjacency(runs["segments"])
    test = es.score(es.prepare(runs, ("test",))["test"], params)
    keep = (
        "incidents",
        "incidents_detected",
        "candidates",
        "candidates_matched",
        "false_alarms_in_incident_free_runs",
        "recall",
        "precision",
    )
    ev.check(
        "held_out_stall_metrics_reproduce_exactly_from_stored_parameters",
        {k: test[k] for k in keep} == {k: stored["test"][k] for k in keep},
        detail=str({k: test[k] for k in keep}),
    )
    ev.check(
        "stall_recall_gate_on_held_out_incidents",
        test["recall"] >= 0.5,
        detail=f"recall {test['recall']} Wilson95 {test['recall_ci95']} ({test['incidents']} incidents)",
    )
    ev.check(
        "stall_false_alarm_gate_per_incident_free_hour",
        test["false_alarms_per_incident_free_hour"] <= 0.25,
        detail=f"{test['false_alarms_per_incident_free_hour']}/h over {test['incident_free_hours']} h",
    )
    ev.check(
        "stall_precision_is_reported_with_its_interval_not_gated",
        test["precision"] is not None and test["precision_ci95"] is not None,
        detail=f"precision {test['precision']} Wilson95 {test['precision_ci95']} - candidate-level, before P06.07 correlation",
    )
    over = json.loads(
        (SOURCE_ROOT / "models" / "registry" / "overlay-detectors" / "evaluation.json").read_text(
            encoding="utf-8"
        )
    )
    tot = over["injection_totals"]
    ev.check(
        "overlay_specification_test_all_injected_detected_exactly",
        tot["detected_exactly"] == tot["injected"]
        and tot["unexplained_candidates_in_positive_pass"] == 0,
        detail=str(tot),
    )
    ev.check(
        "overlay_hard_negatives_and_faults_raise_nothing",
        tot["candidates_from_hard_negatives"] == 0
        and over["fault_streams_safety_candidates"] == 0
        and over["p03_normal_run_safety_candidates"] == 0,
    )
    ev.check(
        "overlay_stuck_and_contradicted_flags_are_demoted_below_half",
        over["stuck_and_contradiction"]["stuck_flag_suspect"]
        and over["stuck_and_contradiction"]["stuck_flag_confidence"] < 0.5
        and over["stuck_and_contradiction"]["contradicted_flood_confidence"] < 0.5,
    )
    ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    ev.check(
        "stall_test_use_is_ledgered_as_non_selecting",
        any(e["purpose"] == "stall_report" for e in ledger["entries"]),
    )
    ev.metrics["held_out_stall_test"] = stored["test"]
    ev.metrics["overlay_specification_test"] = {
        k: over[k] for k in ("p03_05_overlays", "injection_totals", "stuck_and_contradiction")
    }

    asyncio.run(api_checks())
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
