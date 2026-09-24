"""P06.02: fit/select forecast baselines and evaluate them on held-out runs.

    python source-code/backend/analytics/evaluate_baselines.py

Per (horizon, target): every baseline is scored on VALIDATION runs, the best
becomes *the* baseline P06.03's model must beat, its split-conformal 80%
interval is calibrated on the same validation residuals, and the TEST runs
are then scored once. The TEST opening is logged in the test-split ledger as
a non-selecting `baseline_report`; the model-vs-baseline decision (P06.03) is
the ledger's one `final_comparison`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.calibration import load_lane_shares  # noqa: E402
from backend.analytics.forecast_baselines import (  # noqa: E402
    BASELINES,
    NOMINAL_COVERAGE,
    TimeOfDayMean,
    by_run_spec,
    conformal_halfwidth,
    metrics,
    predict_baseline,
    run_bootstrap_mae_diff,
)
from backend.analytics.forecast_data import (  # noqa: E402
    FEATURE_VERSION,
    HORIZONS_S,
    TARGETS,
    load_split,
    make_samples,
)
from backend.analytics.topology import apply_lane_share, load_segments_json  # noqa: E402
from models.evaluation.test_gate import record_opening  # noqa: E402

DATASET = SOURCE_ROOT / "models" / "intelligence_dataset" / "output" / "run-a"
REGISTRY = SOURCE_ROOT / "models" / "registry" / "traffic-forecast"
CHANGE_THRESHOLD = 0.20


def load_runs() -> dict:
    segments = apply_lane_share(load_segments_json(DATASET / "segments.json"), load_lane_shares())
    return {
        split: load_split(DATASET, split, segments) for split in ("train", "validation", "test")
    }


def evaluate(runs: dict) -> dict:
    tod = TimeOfDayMean(runs["train"])
    manifest = json.loads((DATASET / "dataset_manifest.json").read_text(encoding="utf-8"))
    report: dict = {
        "schema": "traffic-forecast-baseline-evaluation-v1",
        "feature_version": FEATURE_VERSION,
        "dataset_sha256": manifest["dataset_sha256"],
        "nominal_interval_coverage": NOMINAL_COVERAGE,
        "runs": {s: len(r) for s, r in runs.items()},
        "horizons": {},
    }
    selection: dict = {}
    for horizon_s, bins in HORIZONS_S.items():
        val = make_samples(runs["validation"], bins)
        test = make_samples(runs["test"], bins)
        hz: dict = {
            "horizon_seconds": horizon_s,
            "validation_samples": len(val.meta),
            "test_samples": len(test.meta),
            "targets": {},
        }
        val_pred = {b: predict_baseline(b, val, tod) for b in BASELINES}
        test_pred = {b: predict_baseline(b, test, tod) for b in BASELINES}
        for j, target in enumerate(TARGETS):
            val_mae = {
                b: float(np.mean(np.abs(val_pred[b][:, j] - val.y[:, j]))) for b in BASELINES
            }
            best = min(val_mae, key=val_mae.get)
            half = np.zeros(len(TARGETS))
            half[j] = conformal_halfwidth(np.abs(val_pred[best][:, j] - val.y[:, j]))
            m = metrics(test.y, test_pred[best], half)[target]
            change = (
                np.abs(test.y[:, 0] - test.hist[:, -1, 0]) / np.maximum(test.hist[:, -1, 0], 1.0)
                >= CHANGE_THRESHOLD
            )
            slices = {}
            for name, mask in (("steady_windows", ~change), ("changing_windows", change)):
                sub = metrics(test.y[mask], test_pred[best][mask], half)[target]
                slices[name] = {k: sub[k] for k in ("n", "mae", "relative_mae", "coverage")}
            persistence_ci = run_bootstrap_mae_diff(
                test.meta, test.y, test_pred[best], test_pred["persistence"], j
            )
            hz["targets"][target] = {
                "selected_baseline": best,
                "validation_mae_by_baseline": {b: round(v, 4) for b, v in val_mae.items()},
                "conformal_halfwidth": round(float(half[j]), 4),
                "test": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()},
                "test_mae_all_baselines": {
                    b: round(float(np.mean(np.abs(test_pred[b][:, j] - test.y[:, j]))), 4)
                    for b in BASELINES
                },
                "test_vs_persistence_mae_diff_ci95": {
                    "mae_diff": round(persistence_ci["mae_diff"], 4),
                    "ci95": [round(x, 4) for x in persistence_ci["ci95"]],
                },
                "test_by_change": slices,
                "test_by_run_spec": {
                    spec: v[target]
                    for spec, v in by_run_spec(test.meta, test.y, test_pred[best], half).items()
                },
            }
            selection[f"{horizon_s}:{target}"] = best
        report["horizons"][str(horizon_s)] = hz
    report["selected_baselines"] = selection
    report["limitations"] = [
        "Targets are the platform's own KPI estimates (loop-derived), not SUMO truth; KPI error (P06.01) is upstream of any forecast error.",
        "Congestion is rare in the dataset, so travel-time forecasts are dominated by near-constant free-flow values.",
        "Conformal coverage assumes test residuals are exchangeable with validation residuals; changing windows (demand ramps) violate that.",
        "Synthetic demand shapes (Gaussian peaks); no claim about real-world forecast accuracy.",
    ]
    return report


def main() -> int:
    report = evaluate(load_runs())
    REGISTRY.mkdir(parents=True, exist_ok=True)
    (REGISTRY / "baseline_evaluation.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    record_opening(
        "baseline_report",
        report["dataset_sha256"],
        FEATURE_VERSION,
        {"task": "P06.02", "note": "selection on validation; test scored once, non-selecting"},
    )
    for hz in report["horizons"].values():
        for target, t in hz["targets"].items():
            print(
                f"{hz['horizon_seconds']:>5}s {target:16s} baseline={t['selected_baseline']:20s} test MAE={t['test']['mae']:.3f} "
                f"rel={t['test']['relative_mae']:.3f} coverage={t['test']['coverage']:.2f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
