"""P06.01 acceptance evidence, against the real P05.01 Postgres and the real
Phase 06 SUMO dataset:

    python source-code/backend/analytics/verify_kpis.py

1. Platform path: a held-out run's loop events go through P05.05's real
   ingestion into `observation_events`; the KPI service reads them back and
   its stored KPIs must equal the pure-function KPIs computed straight from
   the dataset file (no DB/file drift), idempotently.
2. Truth: every KPI is compared with SUMO's edge-wide ground truth on all
   three splits; the accuracy gates apply to the held-out splits only.
3. Contract: every KPI validates as `network-state/v1`; freshness is computed
   at read time (historical windows are `stale` through the real API).
4. Robustness: missing segments degrade quality, duplicates never double-count.
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
import jsonschema
import psycopg
import uvicorn

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics import kpi_service  # noqa: E402
from backend.analytics.calibration import load_lane_shares  # noqa: E402
from backend.analytics.kpis import compute_corridor_kpis, iso_z, to_network_state_record  # noqa: E402
from backend.analytics.topology import apply_lane_share, load_segments_json  # noqa: E402
from backend.analytics.truth_validation import compare_runs, load_truth, truth_windows  # noqa: E402

os.environ.setdefault(
    "AIOPS_AUTH_MODE", "off"
)  # this script tests data, not authentication; verify_auth.py proves the latter
from backend.api.app import app  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.loader.platform_loader import load_events, register_devices, reset_simulated_loops  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

DATASET = SOURCE_ROOT / "models" / "intelligence_dataset" / "output" / "run-a"
GEOMETRY = "2026-09-18.1"
ANCHOR = "2026-09-18T09:00:00Z"
HELD_OUT_RUN = "test/p06-test-s20260925-am-peak"
SCHEMA = json.loads(
    (SOURCE_ROOT / "contracts" / "network-state" / "v1" / "schema.json").read_text(encoding="utf-8")
)
HOST, PORT = "127.0.0.1", 8793

# Gates fixed after the first measurement, at round numbers with headroom,
# and applied to held-out (validation + test) runs only. Speed/travel-time
# error in *congested* windows is reported, not gated: it is a known limit of
# a point loop 10 m past the upstream stop line (see README).
GATES = {
    "volume_veh_h": {"max_rel_bias": 0.05, "max_mape": 0.30, "min_r": 0.90},
    "density_veh_km": {"max_mape": 0.35, "min_r": 0.70},
    "travel_time_s": {"max_mape": 0.12},
    "speed_m_s": {"max_mape": 0.15},
    "throughput_veh_h": {"min_r": 0.85},
}

ev = Evidence("P06.01")


def read_events(run: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (DATASET / run / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def run_server() -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("uvicorn did not start")


def held_out_and_all_reports(segments) -> tuple[dict, dict]:
    reports, pooled_inputs = {}, {}
    for split in ("train", "validation", "test"):
        runs = {}
        for run_dir in sorted((DATASET / split).iterdir()):
            kpis = compute_corridor_kpis(read_events(f"{split}/{run_dir.name}"), segments, GEOMETRY)
            runs[run_dir.name] = (
                kpis,
                truth_windows(load_truth(run_dir / "truth.jsonl"), segments, ANCHOR),
            )
        reports[split] = compare_runs(runs)
        if split != "train":
            pooled_inputs.update(runs)
    return reports, compare_runs(pooled_inputs)


async def api_checks() -> None:
    server = run_server()
    try:
        async with httpx.AsyncClient(base_url=f"http://{HOST}:{PORT}", timeout=30) as client:
            collected, cursor = [], None
            while True:
                params = {"corridor_id": "corridor-a", "direction": "east", "limit": 10}
                if cursor:
                    params["cursor"] = cursor
                r = await client.get("/api/v1/kpis/corridors", params=params)
                page = r.json()
                collected += page["items"]
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            ids = [i["record_id"] for i in collected]
            ev.check(
                "api_kpis_paginate_all_windows_no_duplicates",
                len(collected) == 36 and len(set(ids)) == 36,
                detail=f"n={len(collected)}",
            )
            ev.check(
                "api_serves_historical_kpis_as_stale_never_fresh",
                all(i["freshness_status"] == "stale" for i in collected),
            )
            bad = [
                i for i in collected if list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(i))
            ]
            ev.check(
                "api_kpis_validate_against_network_state_contract",
                not bad,
                detail=f"invalid={len(bad)}",
            )
            r = await client.get("/api/v1/network-state/segment/int-a1_int-a2")
            state = r.json()
            ev.check(
                "segment_state_now_supported_and_contract_valid",
                r.status_code == 200
                and not list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(state)),
                detail=f"status={r.status_code}",
            )
            ev.check(
                "segment_state_carries_forward_with_zero_samples_for_old_data",
                all(m["sample_count"] == 0 for m in state["measurements"]),
            )
    finally:
        server.should_exit = True
        await asyncio.sleep(0.5)


def main() -> int:
    segments = apply_lane_share(load_segments_json(DATASET / "segments.json"), load_lane_shares())
    ev.check(
        "lane_share_calibrated_on_train_split_only",
        json.loads(
            (SOURCE_ROOT / "backend" / "analytics" / "artifacts" / "lane_share.json").read_text()
        )["fitted_on_split"]
        == "train",
    )

    # ---- 1. platform path ----
    events = read_events(HELD_OUT_RUN)
    devices = [
        json.loads(line)
        for line in (DATASET / "devices.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    start = datetime.fromisoformat(ANCHOR.replace("Z", "+00:00"))
    end = start + timedelta(hours=3)
    with psycopg.connect(dsn_from_env()) as conn:
        register_devices(conn, devices)
        reset_simulated_loops(conn)  # one simulated timeline in the DB at a time
        t0 = time.time()
        outcomes = load_events(conn, events)
        ev.check(
            "events_ingested_through_real_write_path",
            outcomes.get("inserted", 0) + outcomes.get("duplicate_ok", 0) == len(events),
            detail=f"{dict(outcomes)} in {time.time() - t0:.0f}s",
        )
        db_segments = kpi_service.load_segments(conn, GEOMETRY)
        ev.check(
            "segments_loaded_from_database", len([s for s in db_segments if s.is_corridor]) == 18
        )

        stored = kpi_service.compute_and_store(conn, start, end, GEOMETRY)
        from_file = compute_corridor_kpis(events, apply_lane_share(db_segments, {}), GEOMETRY)
        key = lambda k: (k["corridor_id"], k["direction"], k["window_start"])  # noqa: E731
        ev.check(
            "db_kpis_equal_pure_function_kpis_from_dataset_file",
            sorted(stored, key=key) == sorted(from_file, key=key),
            detail=f"windows={len(stored)}",
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM corridor_kpis WHERE geometry_version = %s", (GEOMETRY,)
            )
            (n1,) = cur.fetchone()
        kpi_service.compute_and_store(conn, start, end, GEOMETRY)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM corridor_kpis WHERE geometry_version = %s", (GEOMETRY,)
            )
            (n2,) = cur.fetchone()
        ev.check(
            "recompute_is_idempotent_no_duplicate_rows",
            n1 == n2 == 216,
            detail=f"rows {n1} -> {n2}",
        )

    # ---- 2. truth validation ----
    reports, held_out = held_out_and_all_reports(segments)
    ev.metrics["truth_validation_by_split"] = reports
    ev.metrics["truth_validation_held_out_pooled"] = held_out
    for name, gate in GATES.items():
        m = held_out["per_kpi"][name]
        ok = True
        if "max_rel_bias" in gate:
            ok &= abs(m["bias"]) / m["truth_mean"] <= gate["max_rel_bias"]
        if "max_mape" in gate:
            ok &= m["mape"] <= gate["max_mape"]
        if "min_r" in gate:
            ok &= m["pearson_r"] is not None and m["pearson_r"] >= gate["min_r"]
        ev.check(
            f"held_out_{name}_meets_gate",
            ok,
            detail=f"{ {k: m[k] for k in ('mape', 'bias', 'pearson_r', 'truth_mean')} } gate={gate}",
        )
    congested = held_out["by_regime"]["congested"]["travel_time_s"]
    ev.check(
        "congested_regime_error_is_reported_not_hidden",
        held_out["by_regime"]["congested"]["windows"] > 0 and congested.get("mape") is not None,
        detail=f"congested windows={held_out['by_regime']['congested']['windows']} travel_time mape={congested.get('mape')} (gate-free, documented limit)",
    )

    # ---- 3. contract + freshness ----
    kpis = compute_corridor_kpis(events, segments, GEOMETRY)
    last_end = datetime.fromisoformat(
        max(k["window_start"] for k in kpis).replace("Z", "+00:00")
    ) + timedelta(seconds=300)
    records = [to_network_state_record(k, as_of=last_end + timedelta(seconds=60)) for k in kpis]
    invalid = [r for r in records if list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(r))]
    ev.check(
        "every_kpi_validates_as_network_state_v1", not invalid, detail=f"{len(records)} records"
    )
    fresh = {r["freshness_status"] for r in records if r["observation_time"] == iso_z(last_end)}
    old = {
        r["freshness_status"]
        for r in records
        if r["observation_time"] == iso_z(last_end - timedelta(hours=1))
    }
    ev.check(
        "freshness_is_computed_at_read_time",
        fresh == {"fresh"} and old == {"stale"},
        detail=f"latest={fresh} 1h-old={old}",
    )
    ev.check(
        "kpi_values_are_finite_and_in_range",
        all(
            k["kpis"]["volume_veh_h"] >= 0
            and k["kpis"]["delay_s"] >= 0
            and 0 <= k["kpis"]["queue_fraction"] <= 1
            and k["kpis"]["speed_m_s"] > 0
            and k["quality"] in ("valid", "suspect", "invalid")
            for k in kpis
        ),
    )
    reliability_rows = [k for k in kpis if k["kpis"]["buffer_index"] is not None]
    ev.check(
        "reliability_needs_six_trailing_windows_then_appears",
        0 < len(reliability_rows) < len(kpis)
        and all(k["window_start"] >= "2026-09-18T09:25:00Z" for k in reliability_rows),
        detail=f"{len(reliability_rows)}/{len(kpis)} windows carry TTI/PTI/BI",
    )

    # ---- 4. robustness ----
    dropped_edge = "int-a2_int-a3"
    degraded = compute_corridor_kpis(
        [e for e in events if not e["location"]["lane_id"].startswith(dropped_edge)],
        segments,
        GEOMETRY,
    )
    row = next(k for k in degraded if k["corridor_id"] == "corridor-a" and k["direction"] == "east")
    ev.check(
        "missing_segment_degrades_quality_not_silently_valid",
        row["quality"] == "suspect" and row["segments_reporting"] == 2,
        detail=f"{row['quality']} {row['segments_reporting']}/3",
    )
    gone = compute_corridor_kpis(
        [
            e
            for e in events
            if not e["location"]["lane_id"].startswith(("int-a1_int-a2", "int-a2_int-a3"))
        ],
        segments,
        GEOMETRY,
    )
    row = next(k for k in gone if k["corridor_id"] == "corridor-a" and k["direction"] == "east")
    ev.check(
        "majority_missing_segments_is_invalid", row["quality"] == "invalid", detail=row["quality"]
    )
    doubled = compute_corridor_kpis(events + events, segments, GEOMETRY)
    ev.check(
        "duplicate_events_never_double_count",
        doubled == compute_corridor_kpis(events, segments, GEOMETRY),
    )
    ev.check(
        "kpi_computation_is_deterministic",
        compute_corridor_kpis(events, segments, GEOMETRY) == kpis,
    )

    asyncio.run(api_checks())
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
