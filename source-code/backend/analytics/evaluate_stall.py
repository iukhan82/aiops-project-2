"""P06.05: choose and evaluate the stalled-vehicle candidate fusion.

    python source-code/backend/analytics/evaluate_stall.py [--dry-run]

Ground truth is SUMO's own measured blockage windows (`incidents.jsonl`, from
stop-output) - real physical incidents the P04 model never saw (different
demand, 3 h runs). Nine fusion settings are scored on TRAIN and VALIDATION,
the best validation setting wins, TEST is scored once (ledgered as a
non-selecting `stall_report`). Reported at incident level: precision, recall
(Wilson 95%), detection delay, false alarms per incident-free hour, and recall
by blockage distance from the loop.
"""

from __future__ import annotations

import itertools
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.congestion import Params, detect_congestion, match_spans, wilson  # noqa: E402
from backend.analytics.evaluate_congestion import ANCHOR, DATASET, load_runs  # noqa: E402
from backend.analytics.stall_candidates import FusionParams, alarm_spans, fuse, run_edge_runtime  # noqa: E402
from backend.analytics.topology import corridor_directions  # noqa: E402
from models.evaluation.test_gate import record_opening  # noqa: E402

REGISTRY = SOURCE_ROOT / "models" / "registry" / "stall-fusion"
ARTIFACT = SOURCE_ROOT / "backend" / "analytics" / "artifacts" / "stall_fusion.json"
EDGE_MODEL = SOURCE_ROOT / "models" / "registry" / "traffic-safety-blockage" / "1.0.0"
EDGE_BASELINE = SOURCE_ROOT / "models" / "registry" / "baseline" / "baseline_v1.json"
FEATURE_VERSION = "stall-fusion/1"
GRID = [FusionParams(k, False, 0.9) for k in (1, 2, 3)] + [
    FusionParams(k, True, s) for k, s in itertools.product((1, 2, 3), (0.9, 0.97))
]
RUN_HOURS = 3.0


def congestion_params() -> Params:
    return Params(
        **json.loads(
            (
                SOURCE_ROOT / "backend" / "analytics" / "artifacts" / "congestion_params.json"
            ).read_text()
        )["params"]
    )


def prepare(runs: dict, splits=("train", "validation", "test")) -> dict:
    """Per run: edge alarm spans, congestion episodes and measured incidents (computed once)."""
    cong = congestion_params()
    out: dict = {}
    for split in splits:
        out[split] = []
        for r in runs[split]:
            d = DATASET / split / r["name"]
            derived = run_edge_runtime(
                r["events"], DATASET / "devices.jsonl", EDGE_BASELINE, EDGE_MODEL, "p06"
            )
            incidents = []
            if (d / "incidents.jsonl").is_file():
                for line in (d / "incidents.jsonl").read_text(encoding="utf-8").splitlines():
                    i = json.loads(line)
                    if i["valid"]:
                        incidents.append(
                            (
                                i["edge_id"],
                                ANCHOR + timedelta(seconds=i["onset_s"]),
                                ANCHOR + timedelta(seconds=i["end_s"]),
                                i["stall_distance_from_loop_m"],
                            )
                        )
            out[split].append(
                {
                    "name": r["name"],
                    "spec": r["spec"],
                    "spans": alarm_spans(derived),
                    "episodes": detect_congestion(r["events"], runs["segments"], cong),
                    "incidents": incidents,
                    "abstains": sum(
                        1
                        for e in derived
                        if any(
                            m["name"] == "decision" and m["value"] == "abstain"
                            for m in e["measurements"]
                        )
                    ),
                }
            )
    return out


def score(runs: list[dict], params: FusionParams) -> dict:
    inc_total = inc_hit = cand_total = cand_tp = free_cands = related = 0
    free_hours = 0.0
    delays: list[float] = []
    by_distance: dict = defaultdict(lambda: [0, 0])
    confidences = {"tp": [], "fp": []}
    for run in runs:
        cands = fuse(run["spans"], run["episodes"], params)
        span_c = [(c["network_element_id"], c["onset_time"], parse_last(c)) for c in cands]
        span_t = [(e, on, end) for e, on, end, _ in run["incidents"]]
        pairs = match_spans(span_c, span_t)
        matched_c, matched_t = {p[0] for p in pairs}, {p[1] for p in pairs}
        inc_total += len(span_t)
        inc_hit += len(pairs)
        cand_total += len(cands)
        cand_tp += len(pairs)
        for i, j in pairs:
            delays.append((cands[i]["detected_at"] - span_t[j][1]).total_seconds())
        for j, (_, _, _, dist) in enumerate(run["incidents"]):
            by_distance[dist][0] += 1
            by_distance[dist][1] += j in matched_t
        for i, c in enumerate(cands):
            confidences["tp" if i in matched_c else "fp"].append(c["confidence"])
            if i not in matched_c and run["incidents"]:
                c_on, c_end = span_c[i][1], span_c[i][2]
                tol = timedelta(seconds=60)
                if any(
                    inc_edge in ADJACENT.get(c["network_element_id"], ())
                    and c_on <= t_end + tol
                    and t_on - tol <= c_end
                    for inc_edge, t_on, t_end in span_t
                ):
                    related += 1
        if not run["incidents"]:
            free_cands += len(cands)
            free_hours += RUN_HOURS
    delays.sort()
    return {
        "incidents": inc_total,
        "incidents_detected": inc_hit,
        "candidates": cand_total,
        "candidates_matched": cand_tp,
        "recall": round(inc_hit / inc_total, 3) if inc_total else None,
        "recall_ci95": wilson(inc_hit, inc_total),
        "precision": round(cand_tp / cand_total, 3) if cand_total else None,
        "precision_ci95": wilson(cand_tp, cand_total),
        "candidates_on_an_adjacent_segment_during_an_incident": related,
        "precision_if_adjacent_segment_counts": round((cand_tp + related) / cand_total, 3)
        if cand_total
        else None,
        "false_alarms_in_incident_free_runs": free_cands,
        "incident_free_hours": free_hours,
        "false_alarms_per_incident_free_hour": round(free_cands / free_hours, 3)
        if free_hours
        else None,
        "detection_delay_s_median": statistics.median(delays) if delays else None,
        "detection_delay_s_p90": delays[int(0.9 * (len(delays) - 1))] if delays else None,
        "recall_by_blockage_distance_m": {
            str(d): {"incidents": v[0], "detected": v[1]} for d, v in sorted(by_distance.items())
        },
        "mean_confidence_true_vs_false": {
            "matched": round(statistics.fmean(confidences["tp"]), 3) if confidences["tp"] else None,
            "unmatched": round(statistics.fmean(confidences["fp"]), 3)
            if confidences["fp"]
            else None,
        },
    }


def parse_last(c: dict):
    from backend.analytics.kpis import parse_time

    return parse_time(c["attributes"]["last_alarm_at"])


def f1(s: dict) -> float:
    p, r = s["precision"] or 0.0, s["recall"] or 0.0
    return 2 * p * r / (p + r) if p + r else 0.0


ADJACENT: dict[str, set[str]] = {}


def init_adjacency(segments) -> None:
    for segs in corridor_directions(segments).values():
        for a, b in itertools.pairwise(segs):
            ADJACENT.setdefault(a.edge_id, set()).add(b.edge_id)
            ADJACENT.setdefault(b.edge_id, set()).add(a.edge_id)


def main(dry_run: bool) -> int:
    runs = load_runs()
    init_adjacency(runs["segments"])
    data = prepare(runs)
    grid = []
    for p in GRID:
        tr, va = score(data["train"], p), score(data["validation"], p)
        grid.append(
            {
                "params": asdict(p),
                "train_f1": round(f1(tr), 3),
                "validation_f1": round(f1(va), 3),
                "validation_false_alarms_per_free_hour": va["false_alarms_per_incident_free_hour"],
                "train_recall": tr["recall"],
                "validation_recall": va["recall"],
            }
        )
    best_i = max(
        range(len(GRID)),
        key=lambda i: (
            grid[i]["validation_f1"],
            -(grid[i]["validation_false_alarms_per_free_hour"] or 0),
        ),
    )
    best = GRID[best_i]
    print("selected", asdict(best), grid[best_i])
    if dry_run:
        for g in grid:
            print(g)
        return 0
    sha = json.loads((DATASET / "dataset_manifest.json").read_text(encoding="utf-8"))[
        "dataset_sha256"
    ]
    record_opening(
        "stall_report",
        sha,
        FEATURE_VERSION,
        {"task": "P06.05", "note": "fusion params chosen on validation; test scored once"},
    )
    val, test = score(data["validation"], best), score(data["test"], best)
    report = {
        "schema": "stall-fusion-evaluation-v1",
        "detector": "fusion:stall/1",
        "dataset_sha256": sha,
        "selected_params": asdict(best),
        "grid_search": grid,
        "validation": val,
        "test": test,
        "post_hoc_analyses": [
            "adjacent-segment precision was added after the first TEST scoring (parameters and TEST candidates unchanged); "
            "TEST was scored again, non-selecting, to report it"
        ],
        "edge_model": "traffic-safety-blockage/1.0.0 (P04.03/04, trained on the P04 dataset; every run here is unseen by it)",
        "truth": "SUMO stop-output measured blockage windows (incidents.jsonl); a candidate matches an incident on the same segment overlapping it +-60 s",
        "limitations": [
            "Only 33 measured incidents across TRAIN+VALIDATION+TEST blockage runs (6 in TEST): intervals are wide.",
            "Recall is bounded by loop placement: a blockage 70 m past the loop shows only once the queue grows back to it (see recall by distance).",
            "The edge model was trained on 900 s uniform-demand runs; these are 180 min peak-shaped runs (a real distribution shift).",
            "Synthetic physics only; no claim about real roads.",
        ],
    }
    REGISTRY.mkdir(parents=True, exist_ok=True)
    (REGISTRY / "evaluation.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    ARTIFACT.write_text(
        json.dumps(
            {"detector": "fusion:stall/1", "params": asdict(best), "dataset_sha256": sha}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    print("VALIDATION", json.dumps(val, default=str))
    print("TEST", json.dumps(test, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--dry-run" in sys.argv))
