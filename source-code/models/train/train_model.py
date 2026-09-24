"""P04.03: train and honestly evaluate the traffic/safety (road-blockage)
edge model.

Discipline enforced in code, not just documented:

- fit on the TRAIN split only;
- choose hyperparameters, the alarm threshold and the abstention band on the
  VALIDATION split only;
- open the TEST split exactly once (models/evaluation/test_gate.py), for the
  final comparison of the selected model against the P04.02 rule baseline;
- report the comparison whether or not the model wins (ACC-01).

    python source-code/models/train/train_model.py --dry-run   # test split sealed
    python source-code/models/train/train_model.py             # the one real opening

Writes committed reports under source-code/models/registry/<model_id>/<ver>/
and the fitted estimator to the gitignored models/train/output/ for the
ONNX export step (P04.04, models/export/export_onnx.py).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import platform
import sys
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))

from edge.baseline import RuleBaseline  # noqa: E402
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
    row_metrics,
    streams_of,
)
from models.evaluation.test_gate import record_opening  # noqa: E402

SEED = 20260918
MODEL_ID = "traffic-safety-blockage"
MODEL_VERSION = "1.0.0"
REGISTRY_DIR = SOURCE / "models" / "registry"
BASELINE_ARTIFACT = REGISTRY_DIR / "baseline" / "baseline_v1.json"
DRY_RUN = "--dry-run" in sys.argv
LOCAL_OUTPUT = Path(__file__).resolve().parent / "output"
OUT_DIR = LOCAL_OUTPUT / "dry-run" if DRY_RUN else REGISTRY_DIR / MODEL_ID / MODEL_VERSION


def _candidates() -> list[tuple[str, dict]]:
    grid: list[tuple[str, dict]] = []
    for c, cw in itertools.product((0.1, 1.0, 10.0), (None, "balanced")):
        grid.append(("logistic_regression", {"C": c, "class_weight": cw}))
    for depth, leaf, cw in itertools.product((6, 10), (5, 20), (None, "balanced_subsample")):
        grid.append(
            (
                "random_forest",
                {
                    "n_estimators": 150,
                    "max_depth": depth,
                    "min_samples_leaf": leaf,
                    "class_weight": cw,
                },
            )
        )
    for n, depth, lr, pos_w in itertools.product((60, 150), (2, 3), (0.05, 0.1), (1.0, 8.0)):
        grid.append(
            (
                "gradient_boosting",
                {
                    "n_estimators": n,
                    "max_depth": depth,
                    "learning_rate": lr,
                    "positive_weight": pos_w,
                },
            )
        )
    return grid


def fit_candidate(kind: str, params: dict, X: np.ndarray, y: np.ndarray):
    if kind == "logistic_regression":
        model = make_pipeline(
            StandardScaler(),
            LogisticRegression(
                C=params["C"], class_weight=params["class_weight"], max_iter=2000, random_state=SEED
            ),
        )
        return model.fit(X, y)
    if kind == "random_forest":
        model = RandomForestClassifier(random_state=SEED, n_jobs=1, **params)
        return model.fit(X, y)
    model = GradientBoostingClassifier(
        n_estimators=params["n_estimators"],
        max_depth=params["max_depth"],
        learning_rate=params["learning_rate"],
        subsample=0.8,
        random_state=SEED,
    )
    weights = np.where(y == 1, params["positive_weight"], 1.0)
    return model.fit(X, y, sample_weight=weights)


def positive_proba(model, X: np.ndarray) -> np.ndarray:
    return model.predict_proba(X)[:, 1]


def best_threshold(y, p, meta, incidents, streams) -> tuple[float, dict, dict]:
    """Highest row-F1 threshold whose alarm episodes are at most
    MAX_FALSE_ALARM_EPISODE_SHARE false (an operating constraint, not just an
    F1 maximum: row F1 alone happily buys recall with false-alarm storms).
    If no threshold is feasible, fall back to the strictest one."""
    candidates = np.unique(np.quantile(p, np.linspace(0.90, 0.9995, 200)))
    best_key, best_t, best_ep = None, float(candidates[-1]), None
    for t in candidates:
        pred = (p >= t).astype(int)
        ep = episode_metrics(meta, y, pred, incidents, streams)
        if ep["false_alarm_episode_share"] > MAX_FALSE_ALARM_EPISODE_SHARE:
            continue
        m = prf(confusion(y, pred))
        key = (m["f1"], m["precision"])
        if best_key is None or key > best_key:
            best_key, best_t, best_ep = key, float(t), ep
    pred = (p >= best_t).astype(int)
    return (
        best_t,
        prf(confusion(y, pred)),
        best_ep or episode_metrics(meta, y, pred, incidents, streams),
    )


def reliability(y: np.ndarray, p: np.ndarray) -> dict:
    edges = [0.0, 0.05, 0.1, 0.2, 0.4, 0.6, 0.8, 1.0000001]
    bins, ece = [], 0.0
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        mask = (p >= lo) & (p < hi)
        if not mask.any():
            continue
        conf, obs = float(p[mask].mean()), float(y[mask].mean())
        ece += mask.mean() * abs(conf - obs)
        bins.append(
            {
                "range": [lo, min(hi, 1.0)],
                "n": int(mask.sum()),
                "mean_predicted": conf,
                "observed_rate": obs,
            }
        )
    return {"brier": float(brier_score_loss(y, p)), "ece": float(ece), "bins": bins}


def decisions(p: np.ndarray, t_alarm: float, t_low: float) -> np.ndarray:
    """+1 alarm, 0 clear, -1 abstain (uncertain band [t_low, t_alarm))."""
    out = np.zeros(len(p), dtype=int)
    out[(p >= t_low) & (p < t_alarm)] = -1
    out[p >= t_alarm] = 1
    return out


def paired_delta_ci(meta, y, pred_a, pred_b, samples=400, seed=SEED) -> dict:
    """Cluster bootstrap over runs of (model - baseline) row-F1 / precision / recall."""
    runs = sorted({m["run_id"] for m in meta})
    counts = {}
    for run in runs:
        idx = np.array([i for i, m in enumerate(meta) if m["run_id"] == run])
        ca, cb = confusion(y[idx], pred_a[idx]), confusion(y[idx], pred_b[idx])
        counts[run] = (np.array(list(ca.values())), np.array(list(cb.values())))
    rng = np.random.default_rng(seed)
    deltas = {"f1": [], "precision": [], "recall": []}
    for _ in range(samples):
        chosen = rng.integers(0, len(runs), len(runs))
        a = sum(counts[runs[k]][0] for k in chosen)
        b = sum(counts[runs[k]][1] for k in chosen)
        ma = prf(dict(zip(("tp", "fp", "fn", "tn"), map(int, a), strict=True)))
        mb = prf(dict(zip(("tp", "fp", "fn", "tn"), map(int, b), strict=True)))
        for key in deltas:
            deltas[key].append(ma[key] - mb[key])
    return {
        k: {
            "mean": float(np.mean(v)),
            "ci95": [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))],
        }
        for k, v in deltas.items()
    }


def main() -> None:
    problems = verify_manifest()
    if problems:
        raise SystemExit(f"dataset failed integrity check: {problems}")
    manifest = load_manifest()
    table = build_feature_table()
    incidents = load_incidents()

    def split_mask(name: str) -> np.ndarray:
        return np.array([m["split"] == name for m in table.meta])

    tr, va, te = (np.flatnonzero(split_mask(n)) for n in ("train", "validation", "test"))
    if DRY_RUN:  # exercise every code path on VALIDATION; the test split stays sealed
        te = va
    Xtr, ytr = table.X[tr], table.y[tr]
    Xva, yva = table.X[va], table.y[va]
    meta_va = [table.meta[i] for i in va]
    streams_va = streams_of(meta_va)

    # ---- 1. fit every candidate on TRAIN, score on VALIDATION --------------------------
    results = []
    fitted = {}
    for index, (kind, params) in enumerate(_candidates()):
        model = fit_candidate(kind, params, Xtr, ytr)
        p_va = positive_proba(model, Xva)
        threshold, at_best, at_best_ep = best_threshold(yva, p_va, meta_va, incidents, streams_va)
        entry = {
            "index": index,
            "kind": kind,
            "params": params,
            "validation_average_precision": float(average_precision_score(yva, p_va)),
            "validation_best_threshold": threshold,
            "validation_at_best_threshold": at_best,
            "validation_alarm_episodes": {
                k: at_best_ep[k]
                for k in (
                    "incidents_detected",
                    "incidents",
                    "false_alarm_episodes",
                    "true_alarm_episodes",
                    "false_alarm_episode_share",
                )
            },
        }
        results.append(entry)
        fitted[index] = (model, p_va)

    winner = max(
        results,
        key=lambda r: (r["validation_at_best_threshold"]["f1"], r["validation_average_precision"]),
    )
    model, p_va = fitted[winner["index"]]
    t_alarm = winner["validation_best_threshold"]
    t_low = t_alarm / 2.0  # v1 policy: below half the alarm threshold is 'clear'

    # ---- 2. validation-side policy evidence (abstention, calibration) -------------------
    dec_va = decisions(p_va, t_alarm, t_low)
    abstained_va = dec_va == -1
    validation_policy = {
        "alarm_threshold": t_alarm,
        "abstain_lower_threshold": t_low,
        "abstain_rate": float(abstained_va.mean()),
        "positive_rate_among_abstained": float(yva[abstained_va].mean())
        if abstained_va.any()
        else None,
        "reliability": reliability(yva, p_va),
    }

    # ---- 3. the ONE test-split opening: model vs rule baseline --------------------------
    baseline = RuleBaseline.load(BASELINE_ARTIFACT, expected_feature_version=table.feature_version)
    if not DRY_RUN:
        record_opening(
            "final_comparison",
            manifest["dataset_sha256"],
            table.feature_version,
            {
                "model_id": MODEL_ID,
                "model_version": MODEL_VERSION,
                "selected_candidate": winner["index"],
            },
        )
    meta_te = [table.meta[i] for i in te]
    Xte, yte = table.X[te], table.y[te]
    p_te = positive_proba(model, Xte)
    dec_te = decisions(p_te, t_alarm, t_low)
    pred_model = (dec_te == 1).astype(int)
    pred_baseline = baseline.predict_batch(Xte)

    abstained_te = dec_te == -1
    test_report = {
        "model": full_report(meta_te, yte, pred_model, incidents),
        "baseline": full_report(meta_te, yte, pred_baseline, incidents),
        "model_abstention": {
            "abstain_rate": float(abstained_te.mean()),
            "positive_rate_among_abstained": float(yte[abstained_te].mean())
            if abstained_te.any()
            else None,
            "row_metrics_if_abstain_counted_as_alarm": row_metrics(yte, (dec_te != 0).astype(int)),
        },
        "model_reliability": reliability(yte, p_te),
        "paired_delta_model_minus_baseline_cluster_bootstrap": paired_delta_ci(
            meta_te, yte, pred_model, pred_baseline
        ),
        "failure_cases_model": failure_cases(meta_te, Xte, table.names, yte, pred_model, incidents),
        "failure_cases_baseline": failure_cases(
            meta_te, Xte, table.names, yte, pred_baseline, incidents
        ),
        "average_precision_model": float(average_precision_score(yte, p_te)),
    }
    m_f1 = test_report["model"]["rows"]["f1"]
    b_f1 = test_report["baseline"]["rows"]["f1"]
    m_rec = test_report["model"]["episodes"]["incident_recall"]
    b_rec = test_report["baseline"]["episodes"]["incident_recall"]
    test_report["acc01_verdict"] = {
        "model_beats_baseline_on_row_f1": bool(m_f1 > b_f1),
        "model_beats_baseline_on_incident_recall": bool(m_rec > b_rec),
        "model_f1": m_f1,
        "baseline_f1": b_f1,
        "model_incident_recall": m_rec,
        "baseline_incident_recall": b_rec,
        "note": "Reported as measured; the comparison stands whether or not the model wins.",
    }

    # ---- 4. reproducibility: refit from scratch and compare ----------------------------
    again = fit_candidate(winner["kind"], winner["params"], Xtr, ytr)
    reproducible = bool(np.allclose(positive_proba(again, Xva), p_va, atol=0, rtol=0))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_OUTPUT.mkdir(exist_ok=True)
    joblib.dump(model, LOCAL_OUTPUT / "model.joblib")
    model_sha = hashlib.sha256((LOCAL_OUTPUT / "model.joblib").read_bytes()).hexdigest()

    training_report = {
        "model_id": MODEL_ID,
        "model_version": MODEL_VERSION,
        "seed": SEED,
        "reproducible_refit_bitwise_identical": reproducible,
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scikit_learn": sklearn.__version__,
        },
        "data": {
            "dataset_sha256": manifest["dataset_sha256"],
            "feature_version": table.feature_version,
            "feature_names": list(table.names),
            "split_seeds": manifest["split_seeds"],
            "rows": {
                n: int(len(ix)) for n, ix in (("train", tr), ("validation", va), ("test", te))
            },
            "positives": {
                "train": int(ytr.sum()),
                "validation": int(yva.sum()),
                "test": int(yte.sum()),
            },
            "dropped_insufficient_rows": table.dropped_insufficient,
        },
        "procedure": {
            "fit": "train split only",
            "tuning": "hyperparameters, alarm threshold and abstention band on validation only",
            "operating_constraint": f"alarm-episode false share <= {MAX_FALSE_ALARM_EPISODE_SHARE} on validation (mirrors FA-01)",
            "test": "opened once for the final model-vs-baseline comparison (test_split_ledger.json)",
            "candidates_tried": len(results),
        },
        "selected": {k: winner[k] for k in ("index", "kind", "params")},
        "candidates_validation": results,
        "decision_policy": {
            "alarm_if_probability_at_least": t_alarm,
            "abstain_if_probability_in": [t_low, t_alarm],
            "clear_below": t_low,
            "validation": validation_policy,
        },
        "estimator_joblib_sha256_local_only": model_sha,
    }
    (OUT_DIR / "training_report.json").write_text(
        json.dumps(training_report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (OUT_DIR / "comparison_test.json").write_text(
        json.dumps(test_report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    def brief(report: dict) -> dict:
        r, e = report["rows"], report["episodes"]
        return {
            "P": round(r["precision"], 3),
            "R": round(r["recall"], 3),
            "F1": round(r["f1"], 3),
            "incidents": f"{e['incidents_detected']}/{e['incidents']}",
            "median_delay_s": e["median_detection_delay_s"],
            "false_alarm_eps": e["false_alarm_episodes"],
            "fa_per_dev_hr": round(e["false_alarm_episodes_per_device_hour"], 4),
            "fa_share": round(e["false_alarm_episode_share"], 3),
        }

    print(
        json.dumps(
            {
                "selected": {k: winner[k] for k in ("kind", "params")},
                "validation_f1": round(winner["validation_at_best_threshold"]["f1"], 3),
                "validation_AP": round(winner["validation_average_precision"], 3),
                "policy": {
                    "alarm": round(t_alarm, 4),
                    "abstain_from": round(t_low, 4),
                    "val_abstain_rate": round(validation_policy["abstain_rate"], 4),
                },
                "TEST_model": brief(test_report["model"]),
                "TEST_baseline": brief(test_report["baseline"]),
                "delta": test_report["paired_delta_model_minus_baseline_cluster_bootstrap"],
                "abstain_rate_test": round(test_report["model_abstention"]["abstain_rate"], 4),
                "reproducible": reproducible,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
