"""P06.02: transparent forecast baselines, split-conformal intervals and the
run-level metrics/bootstrap every forecaster (baseline or model) is judged by.

Baselines precede any learned model (docs/PROJECT_CONTEXT.md). The baseline a
model must beat is chosen per (target, horizon) on VALIDATION only.
"""

from __future__ import annotations

import math

import numpy as np

from backend.analytics.forecast_data import TARGETS, RunSeries, SampleSet

BASELINES = ("persistence", "moving_average_3", "trend_extrapolation", "time_of_day_mean")
NOMINAL_COVERAGE = 0.8


class TimeOfDayMean:
    """Mean of the TRAIN runs' KPI at the target window, per corridor and
    direction. Fitted on train runs only; knows nothing about today's demand."""

    def __init__(self, train_runs: list[RunSeries]) -> None:
        keys = train_runs[0].series.keys()
        self.mean = {
            k: np.mean([r.series[k][:, : len(TARGETS)] for r in train_runs], axis=0) for k in keys
        }

    def predict(self, s: SampleSet) -> np.ndarray:
        return np.array(
            [self.mean[(m["corridor_id"], m["direction"])][m["t"] + s.horizon_bins] for m in s.meta]
        )


def predict_baseline(name: str, s: SampleSet, tod: TimeOfDayMean | None = None) -> np.ndarray:
    h = s.hist[:, :, : len(TARGETS)]
    last = h[:, -1, :]
    if name == "persistence":
        return last
    if name == "moving_average_3":
        return h[:, -3:, :].mean(axis=1)
    if name == "trend_extrapolation":
        slope = (h[:, -1, :] - h[:, -3, :]) / 2.0
        return np.maximum(last + slope * s.horizon_bins, 0.0)
    if name == "time_of_day_mean":
        assert tod is not None, "time_of_day_mean needs a TimeOfDayMean fitted on train runs"
        return tod.predict(s)
    raise KeyError(name)


def conformal_halfwidth(abs_residuals: np.ndarray, coverage: float = NOMINAL_COVERAGE) -> float:
    """Split-conformal: the ceil((n+1)*coverage)-th smallest calibration
    residual gives an interval with >= `coverage` marginal coverage if test
    residuals are exchangeable with calibration ones (they are not perfectly -
    a regime shift breaks it, which the evaluation reports rather than hides)."""
    n = len(abs_residuals)
    k = math.ceil((n + 1) * coverage)
    ordered = np.sort(abs_residuals)
    return float(ordered[min(k, n) - 1])


def metrics(y: np.ndarray, pred: np.ndarray, half: np.ndarray | None = None) -> dict:
    out = []
    for j, name in enumerate(TARGETS):
        err = pred[:, j] - y[:, j]
        m = {
            "target": name,
            "n": len(err),
            "mae": float(np.mean(np.abs(err))),
            "rmse": float(np.sqrt(np.mean(err**2))),
            "bias": float(np.mean(err)),
            "mean_abs_target": float(np.mean(np.abs(y[:, j]))),
        }
        m["relative_mae"] = m["mae"] / m["mean_abs_target"] if m["mean_abs_target"] else None
        if half is not None:
            m["coverage"] = float(np.mean(np.abs(err) <= half[j]))
            m["mean_interval_width"] = float(2 * half[j])
        out.append(m)
    return {m["target"]: m for m in out}


def run_bootstrap_mae_diff(
    meta: list[dict],
    y: np.ndarray,
    pred_a: np.ndarray,
    pred_b: np.ndarray,
    j: int,
    samples: int = 2000,
    seed: int = 20260918,
) -> dict:
    """95% interval for MAE(a) - MAE(b), resampling whole runs (the unit that
    shares a demand profile, so windows within a run are not independent)."""
    runs = sorted({m["run_id"] for m in meta})
    idx = {r: np.array([i for i, m in enumerate(meta) if m["run_id"] == r]) for r in runs}
    ea = {r: np.abs(pred_a[idx[r], j] - y[idx[r], j]) for r in runs}
    eb = {r: np.abs(pred_b[idx[r], j] - y[idx[r], j]) for r in runs}
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(samples):
        pick = [runs[k] for k in rng.integers(0, len(runs), len(runs))]
        n = sum(len(ea[r]) for r in pick)
        diffs.append(sum(ea[r].sum() for r in pick) / n - sum(eb[r].sum() for r in pick) / n)
    point = float(
        np.mean(np.concatenate(list(ea.values()))) - np.mean(np.concatenate(list(eb.values())))
    )
    return {
        "mae_diff": point,
        "ci95": [float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))],
    }


def by_run_spec(
    meta: list[dict], y: np.ndarray, pred: np.ndarray, half: np.ndarray | None = None
) -> dict:
    out = {}
    for spec in sorted({m["run_spec"] for m in meta}):
        idx = np.array([i for i, m in enumerate(meta) if m["run_spec"] == spec])
        out[spec] = {
            t: {k: v for k, v in mt.items() if k in ("n", "mae", "relative_mae", "coverage")}
            for t, mt in metrics(y[idx], pred[idx], half).items()
        }
    return out
