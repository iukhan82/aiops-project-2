"""P06.04 acceptance evidence, against the real Postgres and the real dataset:

    python source-code/backend/analytics/verify_congestion.py

A held-out blockage run goes through P05.05's real ingestion; the detector
reads it back from the database and must equal the file-based detector; its
candidates carry location, severity, onset/clear and real evidence event ids;
re-running is idempotent and closes episodes that were open; the held-out
numbers reproduce from the stored parameters; steady-demand classes raise no
false alarms; the P03 normal run raises none either.
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

from backend.analytics import congestion_service as cs  # noqa: E402
from backend.analytics.calibration import load_lane_shares  # noqa: E402
from backend.analytics.congestion import Params, SpillParams, detect_congestion, detect_spillback  # noqa: E402
from backend.analytics.evaluate_congestion import DATASET, REGISTRY, load_runs, score_runs, strip  # noqa: E402
from backend.analytics.forecast_data import read_events  # noqa: E402
from backend.analytics.kpi_service import load_segments  # noqa: E402
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
P03_NORMAL = SOURCE_ROOT / "simulator" / "sensors" / "output" / "run-a" / "observations.jsonl"
HOST, PORT = "127.0.0.1", 8795
ev = Evidence("P06.04")


def key(c: dict) -> tuple:
    return (c["kind"], c["network_element_id"], c["onset_time"])


def run_server() -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("uvicorn did not start")


async def api_checks(known_ids: set[str]) -> None:
    server = run_server()
    try:
        async with httpx.AsyncClient(base_url=f"http://{HOST}:{PORT}", timeout=30) as client:
            items, cursor = [], None
            while True:
                params = {"limit": 4, **({"cursor": cursor} if cursor else {})}
                page = (await client.get("/api/v1/candidates", params=params)).json()
                items += page["items"]
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            ids = [i["candidate_id"] for i in items]
            ev.check(
                "api_paginates_candidates_without_duplicates",
                len(ids) == len(set(ids)) and set(ids) >= known_ids,
                detail=f"n={len(ids)}",
            )
            ev.check(
                "api_labels_candidates_inferred_with_evidence",
                all(i["truth_label"] == "inferred" and i["evidence_event_ids"] for i in items),
            )
            only = (await client.get("/api/v1/candidates", params={"kind": "spillback"})).json()[
                "items"
            ]
            ev.check("api_filters_by_kind", all(i["kind"] == "spillback" for i in only))
    finally:
        server.should_exit = True
        await asyncio.sleep(0.5)


def main() -> int:
    art = cs.load_artifact()
    params, spill = Params(**art["params"]), SpillParams(**art["spillback_params"])
    segments = apply_lane_share(load_segments_json(DATASET / "segments.json"), load_lane_shares())
    events = read_events(DATASET / RUN / "events.jsonl")
    devices = [
        json.loads(line)
        for line in (DATASET / "devices.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    start = datetime.fromisoformat("2026-09-18T09:00:00+00:00")
    end = start + timedelta(hours=3)

    with psycopg.connect(dsn_from_env()) as conn:
        register_devices(conn, devices)
        reset_simulated_loops(conn)  # one simulated timeline in the DB at a time
        outcomes = load_events(conn, events)
        ev.check(
            "blockage_run_ingested_through_real_write_path",
            outcomes.get("inserted", 0) + outcomes.get("duplicate_ok", 0) == len(events),
            detail=str(dict(outcomes)),
        )
        db_segments = load_segments(conn, GEOMETRY)

        from_file_eps = detect_congestion(events, db_segments, params)
        from_file = cs.to_candidates(
            from_file_eps, detect_spillback(from_file_eps, db_segments, spill), GEOMETRY, art
        )
        from_db = cs.detect_and_store(conn, start, end, GEOMETRY)
        ev.check(
            "db_detector_equals_file_detector",
            sorted(
                map(
                    json.dumps,
                    [
                        {
                            **c,
                            "onset_time": str(c["onset_time"]),
                            "clear_time": str(c["clear_time"]),
                            "detected_at": str(c["detected_at"]),
                        }
                        for c in from_db
                    ],
                )
            )
            == sorted(
                map(
                    json.dumps,
                    [
                        {
                            **c,
                            "onset_time": str(c["onset_time"]),
                            "clear_time": str(c["clear_time"]),
                            "detected_at": str(c["detected_at"]),
                        }
                        for c in from_file
                    ],
                )
            ),
            detail=f"{len(from_db)} candidates",
        )
        ev.check(
            "blockage_run_yields_congestion_and_spillback_candidates",
            {c["kind"] for c in from_db} == {"congestion", "spillback"},
        )

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM detection_candidates")
            (n1,) = cur.fetchone()
            cs.detect_and_store(conn, start, end, GEOMETRY)
            cur.execute("SELECT count(*) FROM detection_candidates")
            (n2,) = cur.fetchone()
        ev.check("rerun_is_idempotent_no_duplicate_candidates", n1 == n2, detail=f"{n1} -> {n2}")

        # evidence: real event ids, inside the episode window, on the right segment
        problems = []
        with conn.cursor() as cur:
            for c in from_db:
                cur.execute(
                    "SELECT event_id, observation_time, lane_id FROM observation_events WHERE event_id = ANY(%s::uuid[])",
                    (c["evidence_event_ids"],),
                )
                rows = cur.fetchall()
                if len(rows) != len(set(c["evidence_event_ids"])):
                    problems.append(
                        (c["candidate_id"], "evidence id missing from observation_events")
                    )
                edges = {r[2].rsplit("_", 1)[0] for r in rows}
                allowed = {c["network_element_id"], c["attributes"].get("upstream_segment")}
                if not edges <= allowed:
                    problems.append(
                        (c["candidate_id"], f"evidence on unexpected segments {edges - allowed}")
                    )
                if any(r[1] < c["onset_time"] for r in rows):
                    problems.append((c["candidate_id"], "evidence before onset"))
        ev.check(
            "every_candidate_cites_real_events_on_its_own_segment_inside_its_window",
            not problems,
            detail=str(problems[:2]),
        )
        ev.check(
            "candidates_carry_location_severity_onset_and_confidence",
            all(
                c["network_element_id"]
                and c["severity"] in ("low", "medium", "high", "critical")
                and c["onset_time"]
                and 0 < c["confidence"] <= 1
                and c["attributes"].get("corridor_id")
                for c in from_db
            ),
        )

        # an episode still open when only part of the data has arrived gets its clear_time in place
        target = next(
            c
            for c in from_db
            if c["kind"] == "congestion" and c["clear_time"] and c["clear_time"] > c["detected_at"]
        )
        partial = cs.detect_and_store(
            conn, start, target["detected_at"] + timedelta(seconds=30), GEOMETRY, store=False
        )
        still_open = next((c for c in partial if c["candidate_id"] == target["candidate_id"]), None)
        ev.check(
            "episode_is_open_while_data_is_still_arriving",
            still_open is not None and still_open["clear_time"] is None,
            detail=f"onset {target['onset_time']}",
        )
        with conn.cursor() as cur:
            cur.execute("DELETE FROM detection_candidates")
            conn.commit()
            cs.store_candidates(conn, [still_open])
            cur.execute(
                "SELECT clear_time FROM detection_candidates WHERE candidate_id = %s",
                (target["candidate_id"],),
            )
            open_row = cur.fetchone()[0]
            cs.detect_and_store(conn, start, end, GEOMETRY)
            cur.execute(
                "SELECT count(*), max((clear_time IS NOT NULL)::int) FROM detection_candidates WHERE candidate_id = %s",
                (target["candidate_id"],),
            )
            rows, closed = cur.fetchone()
        ev.check(
            "full_run_closes_the_same_candidate_in_place",
            open_row is None and rows == 1 and closed == 1,
            detail="open row -> same candidate_id now has clear_time",
        )

    # ---- reproducibility + scenario gates ----
    stored = json.loads((REGISTRY / "evaluation.json").read_text(encoding="utf-8"))
    runs = load_runs(("test",))
    recomputed = strip(score_runs(runs["test"], runs["segments"], params, spill))
    ev.check(
        "held_out_metrics_reproduce_exactly_from_stored_parameters",
        json.loads(json.dumps(recomputed, sort_keys=True))
        == json.loads(json.dumps(stored["test"], sort_keys=True)),
    )
    test = stored["test"]
    ev.check(
        "no_false_alarms_in_steady_demand_classes",
        all(test[s]["detected_episodes"] == 0 for s in ("midday-steady", "pm-double")),
        detail=str({s: test[s]["detected_episodes"] for s in ("midday-steady", "pm-double")}),
    )
    blk = test["am-peak-blockage"]
    ev.check(
        "blockage_class_precision_meets_gate",
        blk["precision"] >= 0.8,
        detail=f"precision {blk['precision']} CI {blk['precision_ci95']} recall {blk['recall']} CI {blk['recall_ci95']}",
    )
    ev.check(
        "recall_is_reported_by_truth_severity_and_not_gated",
        set(blk["recall_by_truth_severity"]) == {"low", "medium", "high", "critical"},
    )
    ev.check(
        "spillback_reported_separately_from_congestion",
        blk["spillback"]["truth"] >= 1 and "precision" in blk["spillback"],
        detail=str(blk["spillback"]),
    )
    if P03_NORMAL.is_file():
        p03 = [json.loads(line) for line in P03_NORMAL.read_text(encoding="utf-8").splitlines()]
        ev.check(
            "p03_normal_run_raises_no_congestion",
            detect_congestion(p03, segments, params) == [],
            detail=f"{len(p03)} events",
        )
    ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    ev.check(
        "test_split_use_is_ledgered_as_non_selecting",
        any(e["purpose"] == "detector_report" for e in ledger["entries"]),
    )
    ev.metrics["held_out_test"] = test
    ev.metrics["selected_params"] = art

    asyncio.run(api_checks({c["candidate_id"] for c in from_db}))
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
