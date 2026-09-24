"""P06.02: forecast sample construction and baselines on tiny hand-made series.
Held-out results and the ledger entry come from
backend/analytics/evaluate_baselines.py (docs/evidence/p06_02_baselines.json)."""

import numpy as np
import pytest

from backend.analytics.forecast_baselines import (
    TimeOfDayMean,
    conformal_halfwidth,
    predict_baseline,
    run_bootstrap_mae_diff,
)
from backend.analytics.forecast_data import (
    LAGS,
    WINDOWS_PER_RUN,
    RunSeries,
    feature_matrix,
    feature_names,
    make_samples,
)


def make_run(run_id: str, offset: float = 0.0, split: str = "train") -> RunSeries:
    t = np.arange(WINDOWS_PER_RUN, dtype=float)
    arr = np.stack(
        [100 + 10 * t + offset, 5 + t, 60 + 0 * t, 14 + 0 * t, 0 * t], axis=1
    )  # volume, density, tt, speed, queue
    series = {
        (c, d): arr + i for i, (c, d) in enumerate([("corridor-a", "east"), ("corridor-a", "west")])
    }
    return RunSeries(run_id, split, "am-peak", series, [f"w{i}" for i in range(WINDOWS_PER_RUN)])


def test_features_use_only_windows_up_to_the_origin_and_target_is_h_ahead() -> None:
    run = make_run("r1")
    s = make_samples([run], horizon_bins=3)
    i = next(
        k
        for k, m in enumerate(s.meta)
        if m["corridor_id"] == "corridor-a" and m["direction"] == "east" and m["t"] == 10
    )
    arr = run.series[("corridor-a", "east")]
    assert np.array_equal(s.hist[i], arr[10 - LAGS + 1 : 11])
    assert np.array_equal(s.y[i], arr[13, :3])


def test_origins_never_reach_past_the_end_of_the_run() -> None:
    s = make_samples([make_run("r1")], horizon_bins=6)
    assert max(m["t"] for m in s.meta) == WINDOWS_PER_RUN - 1 - 6
    assert min(m["t"] for m in s.meta) == LAGS - 1


def test_feature_matrix_width_matches_names() -> None:
    s = make_samples([make_run("r1")], horizon_bins=1)
    assert feature_matrix(s).shape[1] == len(feature_names())


def test_baseline_arithmetic() -> None:
    s = make_samples([make_run("r1")], horizon_bins=3)
    last = s.hist[:, -1, :3]
    assert np.array_equal(predict_baseline("persistence", s), last)
    assert np.allclose(predict_baseline("moving_average_3", s), s.hist[:, -3:, :3].mean(axis=1))
    # volume rises 10 per window: the trend extrapolation is exact for a linear series
    assert np.allclose(predict_baseline("trend_extrapolation", s)[:, 0], s.y[:, 0])


def test_time_of_day_mean_is_fitted_on_the_runs_it_is_given_only() -> None:
    tod = TimeOfDayMean([make_run("a", 0.0), make_run("b", 20.0)])
    s = make_samples([make_run("c", 500.0, "test")], horizon_bins=1)
    pred = tod.predict(s)
    i = next(
        k
        for k, m in enumerate(s.meta)
        if m["corridor_id"] == "corridor-a" and m["direction"] == "east" and m["t"] == 8
    )
    assert pred[i, 0] == pytest.approx(
        100 + 10 * 9 + 10.0
    )  # train mean of offsets 0 and 20, not the test run's 500


def test_conformal_interval_reaches_its_nominal_coverage() -> None:
    rng = np.random.default_rng(1)
    calibration, fresh = np.abs(rng.normal(0, 3, 2000)), np.abs(rng.normal(0, 3, 20000))
    half = conformal_halfwidth(calibration, 0.8)
    assert 0.78 <= float(np.mean(fresh <= half)) <= 0.83


def test_bootstrap_interval_straddles_zero_for_identical_forecasts() -> None:
    s = make_samples([make_run("a"), make_run("b", 5.0), make_run("c", -5.0)], horizon_bins=1)
    pred = predict_baseline("persistence", s)
    out = run_bootstrap_mae_diff(s.meta, s.y, pred, pred, 0, samples=200)
    assert out["mae_diff"] == 0.0 and out["ci95"] == [0.0, 0.0]
