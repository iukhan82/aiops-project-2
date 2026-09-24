"""P06.04: choose, calibrate and evaluate the congestion/spillback detector.

    python source-code/backend/analytics/evaluate_congestion.py [--dry-run]

Grid of 18 rule settings is scored on TRAIN and VALIDATION; the setting with
the best validation episode-level F1 wins; per-severity precision measured on
validation becomes the candidate's `confidence`; TEST is scored once
(ledgered as a non-selecting `detector_report`). Precision and recall are
reported separately per scenario class, never blended (ACC-03).
"""

from __future__ import annotations

import itertools
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.calibration import load_lane_shares  # noqa: E402
from backend.analytics.congestion import (  # noqa: E402
    SEVERITIES,
    Params,
    SpillParams,
    detect_congestion,
    detect_spillback,
    match_spans,
    truth_episodes,
    truth_spillbacks,
    wilson,
)
from backend.analytics.forecast_data import read_events  # noqa: E402
from backend.analytics.kpis import parse_time  # noqa: E402
from backend.analytics.topology import apply_lane_share, load_segments_json  # noqa: E402
from backend.analytics.truth_validation import load_truth  # noqa: E402
from models.evaluation.test_gate import record_opening  # noqa: E402

DATASET = SOURCE_ROOT / "models" / "intelligence_dataset" / "output" / "run-a"
ANCHOR = parse_time("2026-09-18T09:00:00Z")
REGISTRY = SOURCE_ROOT / "models" / "registry" / "congestion-detector"
ARTIFACT = SOURCE_ROOT / "backend" / "analytics" / "artifacts" / "congestion_params.json"
FEATURE_VERSION = "congestion-rule/1"
GRID = [
    Params(o, s, n, 2) for o, s, n in itertools.product((0.2, 0.3, 0.4), (4.0, 8.0, 12.0), (2, 3))
]


def load_runs(splits=("train", "validation", "test")) -> dict:
    segments = apply_lane_share(load_segments_json(DATASET / "segments.json"), load_lane_shares())
    runs: dict = {"segments": segments}
    for split in splits:
        runs[split] = []
        for d in sorted(p for p in (DATASET / split).iterdir() if p.is_dir()):
            runs[split].append(
                {
                    "name": d.name,
                    "spec": d.name.split("-", 3)[3],
                    "events": read_events(d / "events.jsonl"),
                    "truth": load_truth(d / "truth.jsonl"),
                }
            )
    return runs


SPILL_GRID = [
    SpillParams(d, o) for d, o in itertools.product((120.0, 180.0, 240.0, 300.0), (0.4, 0.6, 0.8))
]


def score_runs(
    runs: list[dict], segments, params: Params, spill: SpillParams | None = None
) -> dict:
    per_spec: dict = defaultdict(lambda: defaultdict(list))
    for run in runs:
        det = detect_congestion(run["events"], segments, params)
        tru = truth_episodes(run["truth"], segments, ANCHOR)
        pairs = match_spans(
            [(e.segment, e.onset, e.clear) for e in det], [(t.segment, t.onset, t.end) for t in tru]
        )
        s = per_spec[run["spec"]]
        s["det"].append(len(det))
        s["truth"].append(len(tru))
        s["tp"].append(len(pairs))
        for i, j in pairs:
            s["onset_delay_s"].append((det[i].detected_at - tru[j].onset).total_seconds())
            s["severity_gap"].append(
                abs(SEVERITIES.index(det[i].severity) - SEVERITIES.index(tru[j].severity))
            )
        s["detected_severity_hits"].append(
            [(det[i].severity, i in {p[0] for p in pairs}) for i in range(len(det))]
        )
        for j, t in enumerate(tru):
            s["truth_sev_total_" + t.severity].append(1)
            if j in {p[1] for p in pairs}:
                s["truth_sev_matched_" + t.severity].append(1)
        sb_det = detect_spillback(det, segments, spill)
        sb_tru = truth_spillbacks(tru, segments)
        sp = match_spans(
            [((b.origin_segment, b.upstream_segment), b.onset, b.clear) for b in sb_det],
            [((o, u), t_up[0], t_up[1]) for o, u, t_up, _ in sb_tru],
        )
        s["sb_det"].append(len(sb_det))
        s["sb_truth"].append(len(sb_tru))
        s["sb_tp"].append(len(sp))
    out: dict = {}
    for spec, s in per_spec.items():
        det, truth, tp = sum(s["det"]), sum(s["truth"]), sum(s["tp"])
        delays = sorted(s["onset_delay_s"])
        out[spec] = {
            "runs": len(s["det"]),
            "truth_episodes": truth,
            "detected_episodes": det,
            "matched": tp,
            "precision": round(tp / det, 3) if det else None,
            "precision_ci95": wilson(tp, det),
            "recall": round(tp / truth, 3) if truth else None,
            "recall_ci95": wilson(tp, truth),
            "detection_delay_s_median": statistics.median(delays) if delays else None,
            "detection_delay_s_p90": delays[int(0.9 * (len(delays) - 1))] if delays else None,
            "severity_within_one_level": (
                round(sum(1 for g in s["severity_gap"] if g <= 1) / len(s["severity_gap"]), 3)
                if s["severity_gap"]
                else None
            ),
            "recall_by_truth_severity": {
                sev: {
                    "truth": len(s["truth_sev_total_" + sev]),
                    "matched": len(s["truth_sev_matched_" + sev]),
                }
                for sev in SEVERITIES
            },
            "spillback": {
                "truth": sum(s["sb_truth"]),
                "detected": sum(s["sb_det"]),
                "matched": sum(s["sb_tp"]),
                "precision": round(sum(s["sb_tp"]) / sum(s["sb_det"]), 3)
                if sum(s["sb_det"])
                else None,
                "recall": round(sum(s["sb_tp"]) / sum(s["sb_truth"]), 3)
                if sum(s["sb_truth"])
                else None,
            },
            "_severity_hits": [h for hs in s["detected_severity_hits"] for h in hs],
        }
    return out


def pooled_f1(scores: dict) -> tuple[float, int, int]:
    det = sum(v["detected_episodes"] for v in scores.values())
    truth = sum(v["truth_episodes"] for v in scores.values())
    tp = sum(v["matched"] for v in scores.values())
    p, r = (tp / det if det else 0.0), (tp / truth if truth else 0.0)
    return (2 * p * r / (p + r) if p + r else 0.0), det - tp, truth - tp


def strip(scores: dict) -> dict:
    return {
        k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} for k, v in scores.items()
    }


def main(dry_run: bool) -> int:
    runs = load_runs()
    segments = runs["segments"]
    grid_report = []
    for params in GRID:
        tr, va = (
            score_runs(runs["train"], segments, params),
            score_runs(runs["validation"], segments, params),
        )
        f_tr, fa_tr, _ = pooled_f1(tr)
        f_va, fa_va, miss_va = pooled_f1(va)
        grid_report.append(
            {
                "params": asdict(params),
                "train_f1": round(f_tr, 3),
                "validation_f1": round(f_va, 3),
                "validation_false_alarms": fa_va,
                "validation_missed": miss_va,
            }
        )
    best_i = max(
        range(len(GRID)),
        key=lambda i: (grid_report[i]["validation_f1"], -grid_report[i]["validation_false_alarms"]),
    )
    best = GRID[best_i]

    def spill_f1(scores: dict) -> tuple[float, int]:
        det = sum(v["spillback"]["detected"] for v in scores.values())
        truth = sum(v["spillback"]["truth"] for v in scores.values())
        tp = sum(v["spillback"]["matched"] for v in scores.values())
        p, r = (tp / det if det else 0.0), (tp / truth if truth else 0.0)
        return (2 * p * r / (p + r) if p + r else 0.0), det - tp

    spill_report = []
    for sp in SPILL_GRID:
        f_tr, _ = spill_f1(score_runs(runs["train"], segments, best, sp))
        f_va, fa_va = spill_f1(score_runs(runs["validation"], segments, best, sp))
        spill_report.append(
            {
                "params": asdict(sp),
                "train_f1": round(f_tr, 3),
                "validation_f1": round(f_va, 3),
                "validation_false_alarms": fa_va,
            }
        )
    best_spill_i = max(
        range(len(SPILL_GRID)),
        key=lambda i: (
            spill_report[i]["validation_f1"],
            -spill_report[i]["validation_false_alarms"],
        ),
    )
    best_spill = SPILL_GRID[best_spill_i]
    val = score_runs(runs["validation"], segments, best, best_spill)
    hits = [h for v in val.values() for h in v["_severity_hits"]]
    overall = sum(1 for _, ok in hits if ok) / len(hits) if hits else 0.0
    severity_precision = {}
    for sev in SEVERITIES:
        pool = [ok for s, ok in hits if s == sev]
        severity_precision[sev] = (
            round(sum(pool) / len(pool), 3) if len(pool) >= 5 else round(overall, 3)
        )
    print(
        "selected",
        asdict(best),
        "validation F1",
        grid_report[best_i]["validation_f1"],
        "severity precision",
        severity_precision,
    )
    print("spillback", asdict(best_spill), spill_report[best_spill_i])
    if dry_run:
        return 0

    dataset_sha = json.loads((DATASET / "dataset_manifest.json").read_text(encoding="utf-8"))[
        "dataset_sha256"
    ]
    record_opening(
        "detector_report",
        dataset_sha,
        FEATURE_VERSION,
        {"task": "P06.04", "note": "params chosen on validation; test scored once"},
    )
    test = score_runs(runs["test"], segments, best, best_spill)
    report = {
        "schema": "congestion-detector-evaluation-v1",
        "detector": "rule:congestion/1",
        "dataset_sha256": dataset_sha,
        "selected_params": asdict(best),
        "grid_search": grid_report,
        "selected_spillback_params": asdict(best_spill),
        "spillback_grid_search": spill_report,
        "confidence_calibration": {
            "method": "precision of validation detections by severity (overall precision where <5 detections)",
            "severity_precision": severity_precision,
        },
        "truth_definition": "standing queue >= 30 m (SUMO waiting vehicles x 3.5 m/lane), >= 2 consecutive 30 s intervals, gaps <= 60 s merged; "
        "spillback = upstream segment's episode starting 0-180 s after its downstream neighbour's, overlapping",
        "validation": strip(val),
        "test": strip(test),
        "limitations": [
            "Loops sit 10 m past the upstream stop line: a queue is invisible until it grows back to the loop, so recall is bounded by physics, not tuning.",
            "Congestion is rare in the synthetic data; episode counts are small and confidence intervals wide (Wilson 95%).",
            "Truth is SUMO's edge-wide waiting-vehicle queue, an independent sensor but the same simulation.",
            "No claim about real roads.",
        ],
    }
    REGISTRY.mkdir(parents=True, exist_ok=True)
    (REGISTRY / "evaluation.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    ARTIFACT.write_text(
        json.dumps(
            {
                "detector": "rule:congestion/1",
                "params": asdict(best),
                "spillback_params": asdict(best_spill),
                "dataset_sha256": dataset_sha,
                "severity_precision": severity_precision,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    for spec, v in strip(test).items():
        print(
            f"TEST {spec:18s} truth {v['truth_episodes']:>3} det {v['detected_episodes']:>3} P {v['precision']} R {v['recall']} "
            f"delay(med) {v['detection_delay_s_median']} sev<=1 {v['severity_within_one_level']} spill {v['spillback']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--dry-run" in sys.argv))
