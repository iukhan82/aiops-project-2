"""P06.03: train, select and evaluate the traffic forecast model.

    python source-code/backend/analytics/train_forecast.py [--dry-run]

Discipline (docs/PROJECT_CONTEXT.md: baseline rules first, disjoint splits,
models can abstain): candidates are fitted on TRAIN, compared on VALIDATION
against the P06.02 baseline for that (horizon, target), and accepted only if
they beat it by ACCEPT_MARGIN there. Conformal intervals are calibrated on
validation residuals. TEST is opened exactly once for the final comparison
(the test-split ledger enforces it), reporting the model against its baseline
*whichever way it goes*, plus a separately ledgered drift analysis. A pair
where the model was rejected on validation is still scored on TEST and shown -
the rejection is not hidden by looking only at winners.

`--dry-run` stops before TEST is opened.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import sklearn

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.evaluate_baselines import CHANGE_THRESHOLD, DATASET, REGISTRY, load_runs  # noqa: E402
from backend.analytics.forecast_baselines import (  # noqa: E402
    NOMINAL_COVERAGE,
    TimeOfDayMean,
    conformal_halfwidth,
    metrics,
    run_bootstrap_mae_diff,
)
from backend.analytics.forecast_data import (  # noqa: E402
    FEATURE_VERSION,
    HORIZONS_S,
    TARGETS,
    feature_matrix,
    feature_names,
    make_samples,
)
from backend.analytics.forecast_model import (  # noqa: E402
    ACCEPT_MARGIN,
    CANDIDATES,
    DRIFT_RATIO,
    DRIFT_WINDOW,
    MODEL_ID,
    MODEL_VERSION,
    SEED,
    Entry,
    ForecastPackage,
    fit_candidate,
    package_hash,
    psi,
    save_package,
    series_flagged,
)
from models.evaluation.test_gate import record_opening  # noqa: E402

PACKAGE_DIR = REGISTRY / MODEL_VERSION
SHIFT_FACTOR = 1.4
SHIFT_FROM_WINDOW = 15


def select_and_fit(runs: dict, baseline_eval: dict) -> tuple[ForecastPackage, dict]:
    tod = TimeOfDayMean(runs["train"])
    entries: dict[tuple[int, str], Entry] = {}
    selection_log: dict = {}
    for horizon_s, bins in HORIZONS_S.items():
        tr, va = make_samples(runs["train"], bins), make_samples(runs["validation"], bins)
        x_tr, x_va = feature_matrix(tr), feature_matrix(va)
        for j, target in enumerate(TARGETS):
            residual = tr.y[:, j] - tr.hist[:, -1, j]
            scored = []
            for kind, params in CANDIDATES:
                est = fit_candidate(kind, params, x_tr, residual)
                pred = np.maximum(va.hist[:, -1, j] + est.predict(x_va), 0.0)
                scored.append((float(np.mean(np.abs(pred - va.y[:, j]))), kind, params, est, pred))
            mae, kind, params, est, pred = min(scored, key=lambda s: s[0])
            b = baseline_eval["horizons"][str(horizon_s)]["targets"][target]
            base_mae = b["validation_mae_by_baseline"][b["selected_baseline"]]
            entries[(horizon_s, target)] = Entry(
                horizon_s,
                target,
                kind,
                params,
                est,
                conformal_halfwidth(np.abs(pred - va.y[:, j])),
                mae <= (1 - ACCEPT_MARGIN) * base_mae,
                b["selected_baseline"],
                b["conformal_halfwidth"],
                mae,
                base_mae,
            )
            selection_log[f"{horizon_s}:{target}"] = {
                "candidates_validation_mae": {
                    f"{k}:{json.dumps(p, sort_keys=True)}": round(m, 4) for m, k, p, _, _ in scored
                },
                "chosen": f"{kind}:{json.dumps(params, sort_keys=True)}",
                "chosen_validation_mae": round(mae, 4),
                "baseline": b["selected_baseline"],
                "baseline_validation_mae": round(base_mae, 4),
                "relative_gain_vs_baseline": round(1 - mae / base_mae, 4),
                "accepted": entries[(horizon_s, target)].accepted,
            }
    meta = {
        "feature_version": FEATURE_VERSION,
        "seed": SEED,
        "sklearn": sklearn.__version__,
        "accept_margin": ACCEPT_MARGIN,
    }
    return ForecastPackage(entries, tod, meta), selection_log


def final_comparison(pkg: ForecastPackage, runs: dict) -> dict:
    out: dict = {}
    for horizon_s, bins in HORIZONS_S.items():
        test = make_samples(runs["test"], bins)
        change = (
            np.abs(test.y[:, 0] - test.hist[:, -1, 0]) / np.maximum(test.hist[:, -1, 0], 1.0)
            >= CHANGE_THRESHOLD
        )
        for j, target in enumerate(TARGETS):
            entry = pkg.entries[(horizon_s, target)]
            model_pred = np.maximum(
                test.hist[:, -1, j] + entry.estimator.predict(feature_matrix(test)), 0.0
            )
            base_pred = pkg.predict(test, horizon_s, target)["baseline"]
            full_model = np.tile(model_pred[:, None], (1, len(TARGETS)))
            full_base = np.tile(base_pred[:, None], (1, len(TARGETS)))
            half_m, half_b = np.zeros(len(TARGETS)), np.zeros(len(TARGETS))
            half_m[j], half_b[j] = entry.halfwidth, entry.baseline_halfwidth
            m_model = metrics(np.tile(test.y[:, j : j + 1], (1, len(TARGETS))), full_model, half_m)[
                TARGETS[j]
            ]
            m_base = metrics(np.tile(test.y[:, j : j + 1], (1, len(TARGETS))), full_base, half_b)[
                TARGETS[j]
            ]
            ci = run_bootstrap_mae_diff(
                test.meta,
                np.tile(test.y[:, j : j + 1], (1, len(TARGETS))),
                full_model,
                full_base,
                j,
            )
            verdict = (
                "model significantly better"
                if ci["ci95"][1] < 0
                else "model significantly worse"
                if ci["ci95"][0] > 0
                else "no significant difference"
            )
            slices = {}
            for name, mask in (("steady_windows", ~change), ("changing_windows", change)):
                slices[name] = {
                    "model_mae": round(
                        float(np.mean(np.abs(model_pred[mask] - test.y[mask, j]))), 4
                    ),
                    "baseline_mae": round(
                        float(np.mean(np.abs(base_pred[mask] - test.y[mask, j]))), 4
                    ),
                    "n": int(mask.sum()),
                }
            served_mae = m_model["mae"] if entry.accepted else m_base["mae"]
            out[f"{horizon_s}:{target}"] = {
                "validation_decision": "model accepted"
                if entry.accepted
                else "model rejected, baseline served",
                "test_verdict_model_vs_baseline": verdict,
                "baseline": entry.baseline,
                "model_test_mae": round(m_model["mae"], 4),
                "baseline_test_mae": round(m_base["mae"], 4),
                "served_source_test_mae": round(served_mae, 4),
                "model_minus_baseline_mae": {
                    "mae_diff": round(ci["mae_diff"], 4),
                    "ci95": [round(x, 4) for x in ci["ci95"]],
                },
                "model_interval_coverage": round(m_model["coverage"], 4),
                "model_interval_width": round(m_model["mean_interval_width"], 4),
                "baseline_interval_coverage": round(m_base["coverage"], 4),
                "by_change": slices,
                "by_run_spec_model_mae": {
                    spec: round(
                        float(
                            np.mean(
                                np.abs(
                                    model_pred[
                                        [
                                            i
                                            for i, m in enumerate(test.meta)
                                            if m["run_spec"] == spec
                                        ]
                                    ]
                                    - test.y[
                                        [
                                            i
                                            for i, m in enumerate(test.meta)
                                            if m["run_spec"] == spec
                                        ],
                                        j,
                                    ]
                                )
                            )
                        ),
                        4,
                    )
                    for spec in sorted({m["run_spec"] for m in test.meta})
                },
                "n_test_samples": len(test.meta),
            }
    return out


def drift_analysis(pkg: ForecastPackage, runs: dict) -> dict:
    bins = HORIZONS_S[900]
    tr, te = make_samples(runs["train"], bins), make_samples(runs["test"], bins)
    names = feature_names()
    x_tr, x_te = feature_matrix(tr), feature_matrix(te)
    per_feature = {n: round(psi(x_tr[:, i], x_te[:, i]), 4) for i, n in enumerate(names)}
    entry = pkg.entries[(900, "volume_veh_h")]
    j = TARGETS.index("volume_veh_h")
    floor = 0.1 * float(np.mean(np.abs(tr.y[:, j])))
    va = make_samples(runs["validation"], bins)

    def rel_err(s, y):
        pred = np.maximum(s.hist[:, -1, j] + entry.estimator.predict(feature_matrix(s)), 0.0)
        return np.abs(pred - y) / np.maximum(s.hist[:, -1, j], floor)

    reference = float(rel_err(va, va.y[:, j]).mean())  # validation relative error
    origin = np.array([m["t"] for m in te.meta])
    stepped_y = np.where(origin >= SHIFT_FROM_WINDOW, te.y[:, j] * SHIFT_FACTOR, te.y[:, j])
    clean, stepped = rel_err(te, te.y[:, j]), rel_err(te, stepped_y)
    f0, n0 = series_flagged(te.meta, clean, reference)
    f1, n1 = series_flagged(te.meta, stepped, reference)
    return {
        "psi_train_vs_test_max": max(per_feature.items(), key=lambda kv: kv[1]),
        "psi_features_above_0_25": sorted(n for n, v in per_feature.items() if v > 0.25),
        "psi_features_0_1_to_0_25": sorted(n for n, v in per_feature.items() if 0.1 < v <= 0.25),
        "monitor": {
            "rule": f"trailing-{DRIFT_WINDOW}-window mean relative error > {DRIFT_RATIO} x validation relative error",
            "chosen_on": "validation (ratio 2.0 / window 12 had the lowest false-alarm rate of six candidates, 6/48 vs 18-38/48)",
            "target": "900s volume_veh_h",
            "validation_relative_error": round(reference, 4),
            "series_flagged_no_drift": f"{f0}/{n0}",
            f"series_flagged_unanticipated_{SHIFT_FACTOR}x_demand_step_from_window_{SHIFT_FROM_WINDOW}": f"{f1}/{n1}",
            "reading": "a coarse alarm, not a precise detector: five-minute count errors are noisy. A false alarm falls back to the baseline (the safe direction); a miss leaves the model in service, which the held-out error already bounds.",
        },
    }


def main(dry_run: bool) -> int:
    runs = load_runs()
    baseline_eval = json.loads((REGISTRY / "baseline_evaluation.json").read_text(encoding="utf-8"))
    pkg, selection = select_and_fit(runs, baseline_eval)
    print("validation-time decisions:")
    for k, v in selection.items():
        print(
            f"  {k:24s} {v['chosen'][:34]:34s} val MAE {v['chosen_validation_mae']:>8} vs baseline {v['baseline_validation_mae']:>8} gain {v['relative_gain_vs_baseline']:+.3f} accepted={v['accepted']}"
        )
    if dry_run:
        print("--dry-run: TEST split left sealed")
        return 0

    dataset_sha = baseline_eval["dataset_sha256"]
    record_opening(
        "final_comparison",
        dataset_sha,
        FEATURE_VERSION,
        {"task": "P06.03", "purpose": "model vs P06.02 baseline"},
    )
    comparison = final_comparison(pkg, runs)
    record_opening(
        "drift_analysis",
        dataset_sha,
        FEATURE_VERSION,
        {"task": "P06.03", "note": "non-selecting: PSI and monitor behaviour"},
    )
    drift = drift_analysis(pkg, runs)

    card = {
        "model_id": MODEL_ID,
        "model_version": MODEL_VERSION,
        "truth_label": "predicted",
        "feature_version": FEATURE_VERSION,
        "dataset_sha256": dataset_sha,
        "train_seeds": json.loads((DATASET / "dataset_manifest.json").read_text())["split_seeds"][
            "train"
        ],
        "training": {
            "target": "residual over persistence",
            "candidates": len(CANDIDATES),
            "selection_split": "validation",
            "accept_margin_vs_baseline": ACCEPT_MARGIN,
        },
        "uncertainty": {
            "method": "split-conformal on validation residuals",
            "nominal_coverage": NOMINAL_COVERAGE,
        },
        "validation_selection": selection,
        "held_out_test_opened_once": comparison,
        "drift": drift,
        "limitations": [
            "Trained and evaluated only on synthetic SUMO demand (Gaussian/sinusoidal shapes); no field-accuracy claim.",
            "Targets are loop-derived KPI estimates whose own error (P06.01) is upstream of the forecast.",
            "`run_progress` encodes position inside the dataset's 09:00-12:00 timeline; the service abstains outside that range.",
            "Interval calibration and candidate selection share the validation runs; test coverage is the honest check.",
            "Congestion is rare in the data, so travel-time forecasts add little beyond free-flow.",
        ],
    }
    save_package(pkg, PACKAGE_DIR, card)
    (REGISTRY / "training_report.json").write_text(
        json.dumps(
            {"selection": selection, "test": comparison, "drift": drift},
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"package sha256 {package_hash(PACKAGE_DIR)[:16]}...")
    print("\nheld-out TEST (opened once):")
    for k, v in comparison.items():
        print(
            f"  {k:24s} {v['validation_decision']:34s} model {v['model_test_mae']:>8} baseline {v['baseline_test_mae']:>8} "
            f"diff CI {v['model_minus_baseline_mae']['ci95']} -> {v['test_verdict_model_vs_baseline']}; served {v['served_source_test_mae']}; cov {v['model_interval_coverage']}"
        )
    print("drift:", json.dumps(drift["monitor"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--dry-run" in sys.argv))
