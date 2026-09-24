"""P04.02: fit transparent rule baselines on the TRAIN split only, select the
variant on VALIDATION, and report held-out (validation) results with concrete
failure cases. The test split is not touched here: it is opened exactly once,
for the final model-vs-baseline comparison in P04.03
(models/evaluation/test_gate.py).

    python source-code/models/baselines/fit_evaluate.py

Needs the dataset from source-code/models/dataset/run_container.sh.
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import numpy as np

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))

from edge.baseline import BASELINE_FORMAT, RuleBaseline, RuleParams  # noqa: E402
from models.dataset.loader import (  # noqa: E402
    build_feature_table,
    load_incidents,
    load_manifest,
    verify_manifest,
)
from models.evaluation.metrics import (  # noqa: E402
    MAX_FALSE_ALARM_EPISODE_SHARE,
    confusion,
    episode_metrics,
    failure_cases,
    full_report,
    prf,
    streams_of,
)

REGISTRY = Path(__file__).resolve().parents[1] / "registry" / "baseline"
BASELINE_ID = "rules-blockage/1.0.0"

OCC_GRID = [round(x, 3) for x in np.arange(0.01, 0.31, 0.01)] + [0.35, 0.4, 0.5, 0.6, 0.7, 0.8]
OCC_MEAN_GRID = [0.005, 0.01, 0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3]
COUNT_MAX_GRID = [0, 1, 2, 3, 5]
SPEED_MAX_GRID = [1.0, 2.0, 4.0, 6.0, 8.0, 10.0, 13.9]


def fit_variants(
    names: tuple[str, ...],
    X: np.ndarray,
    y: np.ndarray,
    meta: list[dict],
    incidents: dict[str, dict],
) -> dict[str, RuleParams]:
    """Exhaustive grid search on the TRAIN rows: maximise row F1 subject to the
    shared operating constraint (alarm-episode false share <= 10%); tie-break
    on precision. If no grid point is feasible, keep the least false-alarming."""
    streams = streams_of(meta)
    best: dict[str, tuple[tuple, RuleParams]] = {}

    def consider(params: RuleParams) -> None:
        pred = RuleBaseline(params, names).predict_batch(X)
        share = episode_metrics(meta, y, pred, incidents, streams)["false_alarm_episode_share"]
        m = prf(confusion(y, pred))
        feasible = share <= MAX_FALSE_ALARM_EPISODE_SHARE and pred.any()
        key = (1, m["f1"], m["precision"]) if feasible else (0, -share, m["f1"])
        if params.variant not in best or key > best[params.variant][0]:
            best[params.variant] = (key, params)

    for occ in OCC_GRID:
        consider(RuleParams("occupancy_threshold", occ))
    for occ, mean in itertools.product(OCC_GRID, OCC_MEAN_GRID):
        consider(RuleParams("occupancy_persistence", occ, occ_mean_min=mean))
    for occ, count, speed in itertools.product(OCC_GRID, COUNT_MAX_GRID, SPEED_MAX_GRID):
        consider(
            RuleParams("occupancy_flow_speed", occ, count_last_max=count, speed_last_max=speed)
        )
    return {variant: params for variant, (_, params) in best.items()}


def main() -> None:
    problems = verify_manifest()
    if problems:
        raise SystemExit(f"dataset failed integrity check: {problems}")
    manifest = load_manifest()
    table = build_feature_table()
    incidents = load_incidents()

    train = np.array([m["split"] == "train" for m in table.meta])
    valid = np.array([m["split"] == "validation" for m in table.meta])

    def subset(mask: np.ndarray):
        idx = np.flatnonzero(mask)
        return [table.meta[i] for i in idx], table.X[idx], table.y[idx]

    meta_tr, Xtr, ytr = subset(train)
    meta_va, Xva, yva = subset(valid)

    fitted = fit_variants(table.names, Xtr, ytr, meta_tr, incidents)
    candidates = {}
    for variant, params in fitted.items():
        baseline = RuleBaseline(params, table.names)
        candidates[variant] = {
            "params": params.__dict__,
            "train_rows": full_row(ytr, baseline.predict_batch(Xtr)),
            "validation": full_report(meta_va, yva, baseline.predict_batch(Xva), incidents),
        }
    never = np.zeros_like(yva)
    reference = {"never_alarm": full_row(yva, never)}

    winner = max(
        candidates,
        key=lambda v: (
            candidates[v]["validation"]["rows"]["f1"],
            candidates[v]["validation"]["episodes"]["incident_recall"],
        ),
    )
    params = RuleParams(**candidates[winner]["params"])
    baseline = RuleBaseline(params, table.names)
    pred_va = baseline.predict_batch(Xva)

    artifact = {
        "format": BASELINE_FORMAT,
        "baseline_id": BASELINE_ID,
        "feature_version": table.feature_version,
        "feature_names": list(table.names),
        "variant": winner,
        "params": params.__dict__,
        "provenance": {
            "fit_split": "train",
            "selection_split": "validation",
            "dataset_sha256": manifest["dataset_sha256"],
            "train_seeds": manifest["split_seeds"]["train"],
            "validation_seeds": manifest["split_seeds"]["validation"],
            "fit_objective": (
                "row-level F1 on training rows subject to alarm-episode false share <= "
                f"{MAX_FALSE_ALARM_EPISODE_SHARE}, tie-break on precision"
            ),
            "test_split_touched": False,
        },
    }
    evaluation = {
        "baseline_id": BASELINE_ID,
        "selected_variant": winner,
        "validation_held_out": candidates[winner]["validation"],
        "all_variants": candidates,
        "reference": reference,
        "failure_cases_validation": failure_cases(
            meta_va, Xva, table.names, yva, pred_va, incidents
        ),
        "notes": [
            "Labels are ground truth (blockage active), not 'detectable yet'; misses at low "
            "flow / long blockage-to-loop distance are largely physical (queue has not reached "
            "the loop), see recall_by_stall_distance_m and by_demand_scale.",
            "Episode metrics: an incident counts as detected if any alarm fires in its window; "
            "a false-alarm episode is a contiguous alarm run never overlapping a positive row.",
        ],
    }
    REGISTRY.mkdir(parents=True, exist_ok=True)
    (REGISTRY / "baseline_v1.json").write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (REGISTRY / "baseline_v1_evaluation.json").write_text(
        json.dumps(evaluation, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    val = candidates[winner]["validation"]
    print(
        json.dumps(
            {
                "selected": winner,
                "params": params.__dict__,
                "validation_rows": {
                    k: round(val["rows"][k], 4) for k in ("precision", "recall", "f1", "fpr")
                },
                "validation_episodes": {
                    k: val["episodes"][k]
                    for k in (
                        "incidents",
                        "incidents_detected",
                        "incident_recall",
                        "median_detection_delay_s",
                        "false_alarm_episodes",
                        "false_alarm_episodes_per_device_hour",
                    )
                },
                "variants_val_f1": {
                    v: round(c["validation"]["rows"]["f1"], 4) for v, c in candidates.items()
                },
            },
            indent=2,
            sort_keys=True,
        )
    )


def full_row(y: np.ndarray, pred: np.ndarray) -> dict:
    c = confusion(y, pred)
    return {**c, **prf(c), "n": int(len(y)), "positives": int(y.sum())}


if __name__ == "__main__":
    main()
