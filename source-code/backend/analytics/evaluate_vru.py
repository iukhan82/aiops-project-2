"""P06.06: choose, train and evaluate the pedestrian/cyclist conflict indicator.

    python source-code/backend/analytics/evaluate_vru.py [--dry-run]

Truth is *realized* post-encroachment time from SUMO's clean 4 Hz trajectories
(models/vru_dataset/truth.py); the detector only sees a degraded 1 Hz tracker
view (position noise, missed detections). Candidate settings - raw geometric
indicators and a logistic pair scorer at several thresholds - are scored on
TRAIN and VALIDATION; the best validation event-level F1 wins; TEST is scored
once (ledgered non-selecting `conflict_report`), then probed for robustness
and bias (non-selecting analyses of the frozen choice).

Reported, never blended: pedestrians and cyclists separately; the risk-weighted
truth (PET <= 3 s at vehicle speed >= 4 m/s) and the plain PET <= 3 s; event
level (a pair) and window level (site x 5 min counts, what actually leaves the
edge); with and without the k-anonymity floor.
"""

from __future__ import annotations

import itertools
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.congestion import wilson  # noqa: E402
from backend.analytics.vru_data import (  # noqa: E402
    DATASET,
    Run,
    Tracker,
    detect,
    load_runs,
    match,
    plain_pet,
    risk_weighted,
    tracker_frames,
)  # noqa: E402
from edge.vru_conflict import (  # noqa: E402
    DEFAULT_MIN_COHORT,
    DEFAULT_WINDOW_S,
    FEATURE_NAMES,
    ConflictParams,
    PairScorer,
)  # noqa: E402
from models.evaluation.test_gate import record_opening  # noqa: E402

REGISTRY = SOURCE_ROOT / "models" / "registry" / "vru-conflict"
ARTIFACT = SOURCE_ROOT / "backend" / "analytics" / "artifacts" / "vru_conflict.json"
FEATURE_VERSION = "vru-conflict/1"
TRACKER = Tracker()
PERMISSIVE = ConflictParams(
    pet_pred_s=4.0, min_vehicle_speed=1.0, min_sin_angle=0.2, vehicle_model="constant_turn_rate"
)
RAW_GRID = [
    ConflictParams(
        pet_pred_s=pet, vehicle_model=model, use_acceleration=accel, min_vehicle_speed=speed
    )
    for pet, model, accel, speed in itertools.product(
        (1.5, 2.0, 3.0), ("constant_velocity", "constant_turn_rate"), (False, True), (2.0, 4.0)
    )
]
SCORER_THRESHOLDS = (0.05, 0.075, 0.1, 0.15, 0.2, 0.3)


def f1(precision: float | None, recall: float | None) -> float:
    p, r = precision or 0.0, recall or 0.0
    return 2 * p * r / (p + r) if p + r else 0.0


def score_runs(
    runs: list[Run],
    params: ConflictParams,
    scorer: PairScorer | None = None,
    is_positive=risk_weighted,
    tracker: Tracker = TRACKER,
) -> dict:
    events = tp = near = fp = truth = 0
    lead: list[float] = []
    per_mode: dict = defaultdict(lambda: {"events": 0, "tp": 0, "fp": 0})
    for run in runs:
        ev = detect(run, tracker, params, scorer)
        m = match(run, ev, is_positive)
        events += len(ev)
        tp += len(m.tp)
        near += len(m.near)
        fp += len(m.fp)
        truth += m.truth_total
        lead += [i.t_first - e.first_alert_t for e, i in m.tp]
        for e in ev:
            per_mode[e.mode]["events"] += 1
        for e, _ in m.tp:
            per_mode[e.mode]["tp"] += 1
        for e in m.fp:
            per_mode[e.mode]["fp"] += 1
    precision = tp / events if events else None
    recall = tp / truth if truth else None
    lead.sort()
    return {
        "truth_conflicts": truth,
        "events": events,
        "matched": tp,
        "interacted_but_not_positive": near,
        "never_interacted": fp,
        "precision": None if precision is None else round(precision, 3),
        "precision_ci95": wilson(tp, events),
        "recall": None if recall is None else round(recall, 3),
        "recall_ci95": wilson(tp, truth),
        "f1": round(f1(precision, recall), 3),
        "lead_time_s_median": statistics.median(lead) if lead else None,
        "share_alerted_before_the_first_user_reaches_the_conflict_point": round(
            sum(1 for x in lead if x >= 0) / len(lead), 3
        )
        if lead
        else None,
        "events_by_mode": {k: dict(v) for k, v in per_mode.items()},
    }


def train_rows(
    runs: list[Run], params: ConflictParams
) -> tuple[np.ndarray, np.ndarray, list[tuple]]:
    X: list[list[float]] = []
    y: list[int] = []
    keys: list[tuple] = []
    for run in runs:
        trace: list = []
        detect(run, TRACKER, params, trace=trace)
        positive = {(i.site, i.vru_id, i.veh_id) for i in run.truth if i.is_conflict}
        for frame, site, vru, veh, feats in trace:
            X.append([feats[n] for n in FEATURE_NAMES])
            y.append(int((site, vru, veh) in positive))
            keys.append((run.name, frame, site, vru, veh))
    return np.array(X), np.array(y), keys


def fit_scorer(X: np.ndarray, y: np.ndarray) -> dict:
    mean, scale = X.mean(axis=0), X.std(axis=0)
    scale[scale == 0] = 1.0
    lr = LogisticRegression(max_iter=2000, C=1.0).fit((X - mean) / scale, y)
    return {
        "features": list(FEATURE_NAMES),
        "mean": [round(float(v), 6) for v in mean],
        "scale": [round(float(v), 6) for v in scale],
        "coef": [round(float(v), 6) for v in lr.coef_[0]],
        "intercept": round(float(lr.intercept_[0]), 6),
    }


def exposure(
    run: Run, tracker: Tracker, window_s: float = DEFAULT_WINDOW_S
) -> dict[tuple[str, str, int], set[str]]:
    seen: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    for site, frames in tracker_frames(run, tracker).items():
        for t, samples in frames.items():
            for s in samples:
                if s.object_class in ("pedestrian", "cyclist"):
                    seen[(site, s.object_class, int(t // window_s))].add(s.track_id)
    return seen


def window_level(
    runs: list[Run],
    params: ConflictParams,
    scorer: PairScorer | None,
    tracker: Tracker = TRACKER,
    window_s: float = DEFAULT_WINDOW_S,
    min_cohort: int = DEFAULT_MIN_COHORT,
) -> dict:
    """What leaves the edge: per (site, pedestrian, window) conflict counts, with and without the k-anonymity floor."""
    det_counts: list[int] = []
    truth_counts: list[int] = []
    suppressed_truth = total_truth = suppressed_events = total_events = 0
    suppressed_windows = windows = 0
    by_spec: dict = defaultdict(
        lambda: [0, 0, 0, 0]
    )  # truth in suppressed, truth total, windows suppressed, windows
    for run in runs:
        ev = detect(run, tracker, params, scorer)
        exp = exposure(run, tracker, window_s)
        truth_w: dict = defaultdict(int)
        det_w: dict = defaultdict(int)
        for i in run.truth:
            if i.is_conflict and i.vru_cls == "ped":
                truth_w[(i.site, "pedestrian", int(i.t_first // window_s))] += 1
        for e in ev:
            if e.mode == "pedestrian":
                det_w[(e.site, "pedestrian", int(e.first_alert_t // window_s))] += 1
        keys = {k for k in exp if k[1] == "pedestrian"} | set(truth_w) | set(det_w)
        for k in keys:
            cohort = len(exp.get(k, ()))
            below = cohort < min_cohort
            windows += 1
            suppressed_windows += below
            by_spec[run.spec][2] += below
            by_spec[run.spec][3] += 1
            det_counts.append(det_w.get(k, 0))
            truth_counts.append(truth_w.get(k, 0))
            total_truth += truth_w.get(k, 0)
            total_events += det_w.get(k, 0)
            if below:
                suppressed_truth += truth_w.get(k, 0)
                suppressed_events += det_w.get(k, 0)
                by_spec[run.spec][0] += truth_w.get(k, 0)
            by_spec[run.spec][1] += truth_w.get(k, 0)
    d, t = np.array(det_counts), np.array(truth_counts)
    rho = spearmanr(d, t).statistic if len(d) > 2 and d.std() > 0 and t.std() > 0 else None
    pearson = float(np.corrcoef(d, t)[0, 1]) if len(d) > 2 and d.std() > 0 and t.std() > 0 else None
    return {
        "windows": windows,
        "window_s": window_s,
        "spearman_detected_vs_true_conflicts_per_window": None
        if rho is None
        else round(float(rho), 3),
        "pearson_detected_vs_true_conflicts_per_window": None
        if pearson is None
        else round(pearson, 3),
        "windows_with_a_true_conflict": int((t > 0).sum()),
        "windows_with_a_true_conflict_that_also_have_a_detected_one": int(
            ((t > 0) & (d > 0)).sum()
        ),
        "windows_without_a_true_conflict_that_have_a_detected_one": int(((t == 0) & (d > 0)).sum()),
        "k_anonymity_floor": {
            "min_cohort": min_cohort,
            "windows_suppressed": suppressed_windows,
            "true_conflicts_in_suppressed_windows": suppressed_truth,
            "true_conflicts_total": total_truth,
            "share_of_true_conflicts_lost_to_the_floor": round(suppressed_truth / total_truth, 3)
            if total_truth
            else None,
            "detected_events_lost_to_the_floor": suppressed_events,
            "detected_events_total": total_events,
            "by_run_spec": {
                s: {
                    "true_conflicts_suppressed": v[0],
                    "true_conflicts": v[1],
                    "windows_suppressed": v[2],
                    "windows": v[3],
                }
                for s, v in by_spec.items()
            },
        },
    }


def window_precision(w: dict) -> float | None:
    """Of the site x window cells the detector flagged, the share that really had a conflict."""
    hit = w["windows_with_a_true_conflict_that_also_have_a_detected_one"]
    false = w["windows_without_a_true_conflict_that_have_a_detected_one"]
    return round(hit / (hit + false), 3) if hit + false else None


def refresh_artifact() -> int:
    """Adds validation window-level precision to the stored report and artifact (TEST untouched, no ledger entry)."""
    runs = load_runs(("validation",))
    report = json.loads((REGISTRY / "evaluation.json").read_text(encoding="utf-8"))
    params = ConflictParams(**report["selected_params"])
    scorer = PairScorer(report["scorer"]) if report["scorer"] else None
    val_window = window_level(runs["validation"], params, scorer)
    report["validation_window_level"] = val_window
    (REGISTRY / "evaluation.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    artifact.update(
        {
            "event_precision": report["validation"]["precision"],
            "event_precision_ci95": report["validation"]["precision_ci95"],
            "window_precision": window_precision(val_window),
            "window_precision_basis": "validation site x 5-min windows flagged by the detector that contained a true conflict",
            "cyclist_mode_validated": False,
        }
    )
    ARTIFACT.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(
        "validation window-level",
        json.dumps(val_window["k_anonymity_floor"]["by_run_spec"]),
        "window_precision",
        window_precision(val_window),
    )
    return 0


def bias_breakdown(runs: list[Run], params: ConflictParams, scorer: PairScorer | None) -> dict:
    """Recall/precision by attributes the detector never sees (truth-side only)."""
    strata: dict = defaultdict(lambda: [0, 0])  # hit, total
    fp_by: dict = defaultdict(int)
    for run in runs:
        ev = detect(run, TRACKER, params, scorer)
        m = match(run, ev)
        hit = {(i.site, i.vru_id, i.veh_id) for _, i in m.tp}
        for i in run.truth:
            if not i.is_conflict:
                continue
            got = (i.site, i.vru_id, i.veh_id) in hit
            band = (
                "4-6 m/s"
                if i.vehicle_speed_m_s < 6
                else ("6-9 m/s" if i.vehicle_speed_m_s < 9 else ">=9 m/s")
            )
            for name, value in (
                ("vru_speed_class", i.vru_sub),
                ("driver_type", i.veh_sub),
                ("vehicle_speed_at_crossing", band),
                ("who_reaches_the_conflict_point_first", "vru" if i.vru_first else "vehicle"),
                ("run_spec", run.spec),
                ("pet_band", "<=1.5 s" if i.pet_s <= 1.5 else "1.5-3 s"),
            ):
                strata[(name, value)][1] += 1
                strata[(name, value)][0] += got
        for e in m.fp:
            fp_by[("vru_class", e.mode)] += 1
    out: dict = defaultdict(dict)
    for (name, value), (hit_n, total) in sorted(strata.items()):
        out[name][value] = {
            "conflicts": total,
            "detected": hit_n,
            "recall": round(hit_n / total, 3) if total else None,
            "recall_ci95": wilson(hit_n, total),
        }
    out["false_alarm_events_by_vru_class"] = {v: n for (_, v), n in fp_by.items()}
    return dict(out)


def noise_sensitivity(
    runs: list[Run], params: ConflictParams, scorer: PairScorer | None
) -> list[dict]:
    out = []
    for tracker in (
        Tracker(1.0, 0.2, 0.05),
        Tracker(1.0, 0.4, 0.05),
        Tracker(1.0, 0.8, 0.05),
        Tracker(1.0, 1.2, 0.05),
        Tracker(1.0, 0.4, 0.0),
        Tracker(1.0, 0.4, 0.15),
        Tracker(1.0, 0.4, 0.30),
        Tracker(2.0, 0.4, 0.05),
    ):
        s = score_runs(runs, params, scorer, tracker=tracker)
        out.append(
            {
                "tracker": asdict(tracker),
                **{k: s[k] for k in ("events", "matched", "precision", "recall", "f1")},
            }
        )
    return out


def cyclist_specificity(runs: list[Run], params: ConflictParams, scorer: PairScorer | None) -> dict:
    cyclists = alerts = truth = 0
    for run in runs:
        cyclists += sum(1 for p in run.pieces if p.cls == "cyc")
        alerts += sum(1 for e in detect(run, TRACKER, params, scorer) if e.mode == "cyclist")
        truth += sum(1 for i in run.truth if i.vru_cls == "cyc" and i.pet_s <= 3.0)
    return {
        "cyclist_site_visits": cyclists,
        "real_cyclist_vehicle_interactions_with_pet_up_to_3s": truth,
        "cyclist_alerts": alerts,
    }


def main(dry_run: bool) -> int:
    runs = load_runs()
    dataset_sha = json.loads((DATASET / "dataset_manifest.json").read_text(encoding="utf-8"))[
        "dataset_sha256"
    ]
    Xtr, ytr, _ = train_rows(runs["train"], PERMISSIVE)
    Xva, yva, _ = train_rows(runs["validation"], PERMISSIVE)
    weights = fit_scorer(Xtr, ytr)
    scorer = PairScorer(weights)
    lr_prob = np.array([scorer.predict(dict(zip(FEATURE_NAMES, row, strict=True))) for row in Xva])
    hgb = HistGradientBoostingClassifier(
        max_depth=3, max_iter=150, learning_rate=0.1, random_state=0
    ).fit(Xtr, ytr)
    ceiling = {
        "candidate_rows_train": int(len(ytr)),
        "positive_rate_train": round(float(ytr.mean()), 4),
        "candidate_rows_validation": int(len(yva)),
        "validation_frame_level_average_precision": {
            "base_rate": round(float(yva.mean()), 4),
            "logistic_deployed": round(float(average_precision_score(yva, lr_prob)), 3),
            "gradient_boosting_ceiling_not_deployed": round(
                float(average_precision_score(yva, hgb.predict_proba(Xva)[:, 1])), 3
            ),
        },
    }
    print("scorer diagnostics", json.dumps(ceiling))

    candidates: list[tuple[str, ConflictParams, PairScorer | None]] = [
        (f"raw:{i}", p, None) for i, p in enumerate(RAW_GRID)
    ]
    candidates += [
        (f"scorer@{th}", replace(PERMISSIVE, min_score=th), scorer) for th in SCORER_THRESHOLDS
    ]
    grid = []
    for name, params, sc in candidates:
        tr, va = score_runs(runs["train"], params, sc), score_runs(runs["validation"], params, sc)
        grid.append(
            {
                "name": name,
                "params": asdict(params),
                "scorer": sc is not None,
                "train": {k: tr[k] for k in ("events", "matched", "precision", "recall", "f1")},
                "validation": {
                    k: va[k] for k in ("events", "matched", "precision", "recall", "f1")
                },
            }
        )
        print(
            name,
            "train f1",
            tr["f1"],
            "validation",
            {k: va[k] for k in ("events", "matched", "precision", "recall", "f1")},
        )
    best_i = max(
        range(len(grid)),
        key=lambda i: (grid[i]["validation"]["f1"], grid[i]["validation"]["precision"] or 0),
    )
    best_name, best_params, best_scorer = candidates[best_i]
    print("selected", best_name, grid[best_i]["validation"])
    if dry_run:
        return 0

    record_opening(
        "conflict_report",
        dataset_sha,
        FEATURE_VERSION,
        {"task": "P06.06", "note": "indicator chosen on validation; test scored once"},
    )
    val = score_runs(runs["validation"], best_params, best_scorer)
    test = score_runs(runs["test"], best_params, best_scorer)
    test_plain = score_runs(runs["test"], best_params, best_scorer, is_positive=plain_pet)
    truth_mix = {"validation": defaultdict(int), "test": defaultdict(int)}
    for split in ("validation", "test"):
        for run in runs[split]:
            for i in run.truth:
                truth_mix[split][i.severity] += 1
    report = {
        "schema": "vru-conflict-evaluation-v1",
        "detector": "edge:vru_conflict/1",
        "dataset_sha256": dataset_sha,
        "selected": best_name,
        "selected_params": asdict(best_params),
        "scorer": weights if best_scorer else None,
        "tracker": asdict(TRACKER),
        "grid_search": grid,
        "scorer_diagnostics": ceiling,
        "truth": {
            "definition": "realized PET (both actual paths cross; |t_vru - t_vehicle|) from clean 4 Hz SUMO trajectories; a conflict is PET <= 3 s AND vehicle speed at the crossing >= 4 m/s",
            "risk_weighting_chosen_after_inspecting_validation": True,
            "interactions_by_severity": {k: dict(v) for k, v in truth_mix.items()},
        },
        "validation": val,
        "test": test,
        "test_plain_pet_3s_truth": test_plain,
        "validation_window_level": window_level(runs["validation"], best_params, best_scorer),
        "test_window_level": window_level(runs["test"], best_params, best_scorer),
        "test_bias": bias_breakdown(runs["test"], best_params, best_scorer),
        "test_noise_sensitivity": noise_sensitivity(runs["test"], best_params, best_scorer),
        "test_cyclists": cyclist_specificity(runs["test"], best_params, best_scorer),
        "post_hoc_analyses": [
            "the risk-weighted truth (vehicle speed >= 4 m/s) was fixed after inspecting the VALIDATION PET/speed distribution, before any TEST scoring",
            "bias, noise-sensitivity and window-level analyses were run on TEST for the frozen choice (non-selecting)",
        ],
        "limitations": [
            "Truth is SUMO's behaviour model: drivers yield to pedestrians on crossings by rule, so realized conflicts are yield-and-go interplay plus the assertive-driver share (25%, a modelling assumption); real near-miss rates differ.",
            "SUMO's separated bike lanes give cyclists >= 2.35 m clearance and no path crossing with any vehicle: there are NO real cyclist-vehicle conflicts in this world, so cyclist recall is unmeasurable and only cyclist specificity is reported.",
            "Positions are 2-D tracks with Gaussian noise and random dropout; no occlusion, night, weather or camera-geometry effects are simulated.",
            "Pedestrians are single-speed SUMO walkers (normal 1.39 m/s, slow 0.8 m/s); children, wheelchair users, groups and running are not modelled.",
            "Constant-velocity/turn-rate prediction cannot anticipate a pedestrian who waits at the kerb or a driver who decides late; that is the dominant miss and false-alarm mechanism.",
        ],
    }
    REGISTRY.mkdir(parents=True, exist_ok=True)
    (REGISTRY / "evaluation.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    ARTIFACT.write_text(
        json.dumps(
            {
                "detector": "edge:vru_conflict/1",
                "params": asdict(best_params),
                "scorer": weights if best_scorer else None,
                "dataset_sha256": dataset_sha,
                "selected": best_name,
                "event_precision": val["precision"],
                "event_precision_ci95": val["precision_ci95"],
                "cyclist_mode_validated": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("VALIDATION", json.dumps(val, default=str))
    print("TEST", json.dumps(test, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(
        refresh_artifact() if "--refresh-artifact" in sys.argv else main("--dry-run" in sys.argv)
    )
