"""P06.03 acceptance evidence, against the real Postgres (corridor KPIs from
P06.01's held-out run) and the trained package on disk:

    python source-code/backend/analytics/verify_forecasts.py

Proves the package is hash-verified, that serving equals offline evaluation
(no train/serve skew), that forecasts are contract-valid `forecast/v1` records
carrying uncertainty and the model/baseline split, that the service abstains
instead of forecasting from inputs it should not trust, that storage is
idempotent, that the API serves them as `predicted`, and that the test split
was opened exactly once for the model comparison.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import os
import sys
import tempfile
import threading
import time
from datetime import timedelta
from pathlib import Path

import httpx
import jsonschema
import numpy as np
import psycopg
import uvicorn

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics import forecast_service as svc  # noqa: E402
from backend.analytics.calibration import load_lane_shares  # noqa: E402
from backend.analytics.forecast_data import (  # noqa: E402
    FEATURE_VERSION,
    HORIZONS_S,
    TARGETS,
    RunSeries,
    make_samples,
    read_events,
    series_from_events,
)
from backend.analytics.forecast_model import IntegrityError, drift_flag, load_package, psi  # noqa: E402
from backend.analytics.forecast_service import fetch_kpi_rows  # noqa: E402
from backend.analytics.kpi_service import load_segments, store_kpis, stored_kpis  # noqa: E402
from backend.analytics.kpis import compute_corridor_kpis  # noqa: E402
from backend.analytics.topology import apply_lane_share, load_segments_json  # noqa: E402

os.environ.setdefault(
    "AIOPS_AUTH_MODE", "off"
)  # this script tests data, not authentication; verify_auth.py proves the latter
from backend.api.app import app  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402
from models.evaluation.test_gate import LEDGER_PATH, TestSplitReopened, record_opening  # noqa: E402

PACKAGE_DIR = SOURCE_ROOT / "models" / "registry" / "traffic-forecast" / "1.0.0"
DATASET = SOURCE_ROOT / "models" / "intelligence_dataset" / "output" / "run-a"
RUN = "test/p06-test-s20260925-am-peak"
GEOMETRY = "2026-09-18.1"
SCHEMA = json.loads(
    (SOURCE_ROOT / "contracts" / "forecast" / "v1" / "schema.json").read_text(encoding="utf-8")
)
HOST, PORT = "127.0.0.1", 8794
ev = Evidence("P06.03")
validator = jsonschema.Draft202012Validator(SCHEMA)


def origin_end(t: int):
    return svc.TIMELINE_ANCHOR + timedelta(seconds=(t + 1) * svc.WINDOW_S)


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
            items, cursor = [], None
            while True:
                params = {
                    "corridor_id": "corridor-a",
                    "direction": "east",
                    "horizon_seconds": 900,
                    "limit": 3,
                }
                if cursor:
                    params["cursor"] = cursor
                page = (await client.get("/api/v1/forecasts/corridors", params=params)).json()
                items += page["items"]
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            ids = [i["forecast_id"] for i in items]
            ev.check(
                "api_paginates_forecasts_without_duplicates",
                len(items) >= 8 and len(ids) == len(set(ids)),
                detail=f"n={len(items)}",
            )
            ev.check(
                "api_forecasts_validate_and_are_labelled_predicted",
                all(
                    not list(validator.iter_errors(i)) and i["truth_label"] == "predicted"
                    for i in items
                ),
            )
            ev.check(
                "api_orders_newest_valid_window_first",
                [i["valid_from"] for i in items]
                == sorted((i["valid_from"] for i in items), reverse=True),
            )
    finally:
        server.should_exit = True
        await asyncio.sleep(0.5)


def main() -> int:
    pkg = load_package(PACKAGE_DIR)
    ev.check(
        "package_loads_only_after_hash_verification", pkg.meta["feature_version"] == FEATURE_VERSION
    )
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "pkg"
        shutil.copytree(PACKAGE_DIR, bad)
        blob = bytearray((bad / "models.joblib").read_bytes())
        blob[len(blob) // 2] ^= 0xFF
        (bad / "models.joblib").write_bytes(bytes(blob))
        try:
            load_package(bad)
            tampered = False
        except IntegrityError:
            tampered = True
        (bad / "artifact_manifest.json").unlink()
        try:
            load_package(bad)
            no_manifest = False
        except IntegrityError:
            no_manifest = True
    ev.check("tampered_or_unmanifested_package_is_refused", tampered and no_manifest)

    ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    sha = json.loads((DATASET / "dataset_manifest.json").read_text(encoding="utf-8"))[
        "dataset_sha256"
    ]
    finals = [
        e
        for e in ledger["entries"]
        if e["purpose"] == "final_comparison"
        and e["dataset_sha256"] == sha
        and e["feature_version"] == FEATURE_VERSION
    ]
    try:
        record_opening(
            "final_comparison",
            sha,
            FEATURE_VERSION,
            {"attempt": "verification re-open without a reason"},
        )
        reopened = False
    except TestSplitReopened:
        reopened = True
    ev.check(
        "test_split_opened_exactly_once_and_reopening_is_refused",
        len(finals) == 1 and reopened,
        detail=f"final_comparison entries={len(finals)}",
    )

    segments = apply_lane_share(load_segments_json(DATASET / "segments.json"), load_lane_shares())
    series, starts = series_from_events(read_events(DATASET / RUN / "events.jsonl"), segments)
    offline_run = RunSeries("p06-test-s20260925-am-peak", "test", "am-peak", series, starts)

    all_records, all_abstain = [], []
    with psycopg.connect(dsn_from_env()) as conn:
        # Self-contained: store this run's KPIs (computed from the dataset file with the
        # DB's own segments) so the check does not depend on which run another verify
        # script last loaded into the shared database; the DB-read path is P06.01's proof.
        with conn.cursor() as cur:
            cur.execute("DELETE FROM corridor_kpis")
            cur.execute("DELETE FROM forecasts")
        conn.commit()
        store_kpis(
            conn,
            compute_corridor_kpis(
                read_events(DATASET / RUN / "events.jsonl"), load_segments(conn, GEOMETRY), GEOMETRY
            ),
        )
        for t in (8, 14, 20, 27):
            records, abstentions = svc.forecast_corridors(
                conn, pkg, origin_end(t), origin_end(t) + timedelta(seconds=60), GEOMETRY
            )
            all_records += records
            all_abstain += abstentions
            ev.check(
                f"origin_window_{t}_emits_model_and_baseline_records_for_every_series_and_horizon",
                len(records) == 6 * 3 * 2 and not abstentions,
                detail=f"{len(records)} records",
            )
        bad = [r for r in all_records if list(validator.iter_errors(r))]
        ev.check(
            "every_forecast_validates_as_forecast_v1",
            not bad,
            detail=f"{len(all_records)} records, {len(bad)} invalid",
        )
        ev.check(
            "uncertainty_brackets_every_value_and_values_are_non_negative",
            all(
                m["uncertainty"]["lower_bound"] <= m["value"] <= m["uncertainty"]["upper_bound"]
                and m["value"] >= 0
                for r in all_records
                for m in r["measurements"]
            ),
        )
        served_by_baseline = {
            (r["horizon_seconds"], m["name"])
            for r in all_records
            if r["model_id"].startswith("traffic-forecast-baseline")
            for m in r["measurements"]
        }
        ev.check(
            "targets_rejected_on_validation_are_served_by_the_baseline_and_labelled_so",
            {n for _, n in served_by_baseline} == {"travel_time_s"}
            and len(served_by_baseline) == 3,
            detail=str(sorted(served_by_baseline)),
        )
        ev.check(
            "model_records_name_the_baseline_they_were_compared_against",
            all("baseline_id" in r for r in all_records if r["model_id"] == "traffic-forecast"),
        )

        # ---- serving == offline evaluation (no train/serve skew) ----
        worst = 0.0
        for horizon_s, bins in HORIZONS_S.items():
            offline = make_samples([offline_run], bins)
            for target in TARGETS:
                pred = pkg.predict(offline, horizon_s, target)["value"]
                for r in all_records:
                    if r["horizon_seconds"] != horizon_s:
                        continue
                    for m in r["measurements"]:
                        if m["name"] != target:
                            continue
                        corridor, direction = r["network_element_id"].split("/")
                        t = (
                            int(
                                (
                                    svc.parse_time(r["predicted_at"]) - svc.TIMELINE_ANCHOR
                                ).total_seconds()
                                // svc.WINDOW_S
                            )
                            - 1
                        )
                        i = next(
                            k
                            for k, mm in enumerate(offline.meta)
                            if (mm["corridor_id"], mm["direction"], mm["t"])
                            == (corridor, direction, t)
                        )
                        worst = max(worst, abs(m["value"] - float(pred[i])))
        ev.check(
            "serving_matches_offline_evaluation_within_rounding",
            worst < 1e-3,
            detail=f"max abs difference {worst:.6f}",
        )

        # ---- accuracy on this run, model vs baseline, straight from the served records ----
        kpi_rows = {
            (k["corridor_id"], k["direction"], k["window_start"]): k
            for k in stored_kpis(conn, GEOMETRY)
        }
        err = {"model": [], "baseline": []}
        for r in all_records:
            if r["horizon_seconds"] != 900 or r["model_id"] != "traffic-forecast":
                continue
            corridor, direction = r["network_element_id"].split("/")
            actual = kpi_rows.get((corridor, direction, r["valid_from"]))
            for m in r["measurements"]:
                if actual and m["name"] == "volume_veh_h":
                    err["model"].append(abs(m["value"] - actual["kpis"]["volume_veh_h"]))
        ev.metrics["served_15min_volume_mae_on_one_heldout_run"] = {
            "n": len(err["model"]),
            "mae": round(float(np.mean(err["model"])), 2),
        }
        ev.check(
            "served_forecasts_can_be_scored_against_the_kpi_that_actually_followed",
            len(err["model"]) >= 20,
        )

        # ---- idempotent storage ----
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM forecasts WHERE model_version = '1.0.0'")
            (n1,) = cur.fetchone()
        svc.forecast_corridors(
            conn, pkg, origin_end(14), origin_end(14) + timedelta(seconds=60), GEOMETRY
        )
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM forecasts WHERE model_version = '1.0.0'")
            (n2,) = cur.fetchone()
        ev.check(
            "recomputing_a_forecast_replaces_it_no_duplicates", n1 == n2, detail=f"{n1} -> {n2}"
        )

        # ---- abstention (pure function, real KPI rows) ----
        oe = origin_end(14)
        rows = fetch_kpi_rows(conn, oe, GEOMETRY)
        rec, ab = svc.forecast_from_kpi_rows(rows, pkg, oe, oe + timedelta(hours=1), GEOMETRY)
        ev.check("stale_inputs_produce_no_forecast", not rec and "stale" in ab[0]["reason"])
        gap_rows = [
            r
            for r in rows
            if not (
                r["corridor_id"] == "corridor-a"
                and r["direction"] == "east"
                and r["window_start"] == rows[0]["window_start"]
            )
        ]
        rec, ab = svc.forecast_from_kpi_rows(
            gap_rows, pkg, oe, oe + timedelta(seconds=60), GEOMETRY
        )
        ev.check(
            "a_gap_in_the_history_abstains_for_the_whole_network_context",
            not rec and any("gap" in a["reason"] for a in ab),
            detail=str(ab[:2]),
        )
        invalid_rows = [
            {**r, "quality": "invalid"}
            if (r["corridor_id"], r["direction"]) == ("corridor-b", "west")
            else r
            for r in rows
        ]
        rec, ab = svc.forecast_from_kpi_rows(
            invalid_rows, pkg, oe, oe + timedelta(seconds=60), GEOMETRY
        )
        ev.check(
            "an_invalid_input_window_abstains",
            not rec and any("invalid" in a["reason"] for a in ab),
        )
        early = origin_end(2)
        rec, ab = svc.forecast_from_kpi_rows(
            fetch_kpi_rows(conn, early, GEOMETRY),
            pkg,
            early,
            early + timedelta(seconds=60),
            GEOMETRY,
        )
        ev.check("origin_before_the_trained_range_abstains", not rec)
        late = origin_end(33)
        rec, ab = svc.forecast_from_kpi_rows(
            fetch_kpi_rows(conn, late, GEOMETRY), pkg, late, late + timedelta(seconds=60), GEOMETRY
        )
        ev.check(
            "horizons_beyond_the_trained_timeline_abstain_individually",
            {r["horizon_seconds"] for r in rec} == {300}
            and any("beyond the trained timeline" in a["reason"] for a in ab),
            detail=f"horizons served {sorted({r['horizon_seconds'] for r in rec})}",
        )
        rec_a, _ = svc.forecast_from_kpi_rows(rows, pkg, oe, oe + timedelta(seconds=60), GEOMETRY)
        rec_b, _ = svc.forecast_from_kpi_rows(rows, pkg, oe, oe + timedelta(seconds=60), GEOMETRY)
        ev.check("forecasting_is_deterministic", rec_a == rec_b)

    # ---- drift measures ----
    rng = np.random.default_rng(3)
    base = rng.normal(0, 1, 5000)
    ev.check(
        "psi_is_near_zero_for_the_same_distribution_and_large_for_a_shift",
        psi(base, rng.normal(0, 1, 5000)) < 0.05 and psi(base, rng.normal(1.5, 1, 5000)) > 0.25,
    )
    ev.check(
        "drift_flag_needs_a_full_window_and_a_sustained_excess",
        not drift_flag([9.0] * 5, 1.0)
        and drift_flag([9.0] * 12, 1.0)
        and not drift_flag([1.0] * 12, 1.0),
    )
    card = json.loads((PACKAGE_DIR / "model_card.json").read_text(encoding="utf-8"))
    ev.check(
        "model_card_records_limitations_drift_and_held_out_results",
        len(card["limitations"]) >= 4
        and "monitor" in card["drift"]
        and len(card["held_out_test_opened_once"]) == 9,
    )
    ev.metrics["held_out_test_summary"] = {
        k: {
            "validation_decision": v["validation_decision"],
            "verdict": v["test_verdict_model_vs_baseline"],
            "model_mae": v["model_test_mae"],
            "baseline_mae": v["baseline_test_mae"],
            "coverage": v["model_interval_coverage"],
        }
        for k, v in card["held_out_test_opened_once"].items()
    }
    ev.metrics["drift"] = card["drift"]["monitor"]

    asyncio.run(api_checks())
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
