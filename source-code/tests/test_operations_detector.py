"""P10.06: the operational detector's building blocks on small synthetic runs (no dataset needed).

The dataset-level claims (splits, leakage, the served detector's numbers) are checked by `verify_operations_detector.py` against the
real captured data; these tests pin the logic that check relies on.
"""

import numpy as np
import pytest

from models.operations_detector import detectors as d
from models.operations_detector import evaluate as e
from models.operations_detector import features as f
from models.operations_detector.runtime import OperationsDetector

RNG = np.random.default_rng(7)
N_SIGNALS = len(f.SIGNAL_NAMES)


def _raw(steps: int, fault: tuple[int, int] | None = None) -> np.ndarray:
    """Plausible healthy signals (rates, ratios, latencies) with an optional degradation of two signals."""
    base = np.array([20.0, 0.004, 180.0, 0.99, 15.0, 0.004, 120.0, 0.98])
    assert len(base) == N_SIGNALS
    raw = base[None, :] * (1.0 + RNG.normal(0, 0.03, (steps, N_SIGNALS)))
    if fault:
        a, b = fault
        raw[a:b, f.SIGNAL_NAMES.index("ingest_latency_p95_ms")] = 4200.0
        raw[a:b, f.SIGNAL_NAMES.index("api_error_ratio")] = 0.3
    return raw


def _run(name: str, fault: tuple[int, int] | None = None, steps: int = 90) -> f.Run:
    flags = np.zeros(steps, dtype=bool)
    if fault:
        flags[fault[0] : fault[1]] = True
    return f.Run(
        run_id=name, split="train", seed=1, scenario="x" if fault else "normal", replicate=0,
        raw=_raw(steps, fault), fault=flags, burst=np.zeros(steps, dtype=bool),
        severity=0.9 if fault else 0.0, plan={},
    )  # fmt: skip


def test_the_alarm_rule_needs_the_score_to_persist():
    scores = np.array([0.1, 2, 2, 0.1, 2, 2, 2, 2, 0.1])
    flags = e.alarms(scores, 1.0, persistence=3)
    assert flags.tolist() == [False, False, False, False, False, False, True, True, False]


def test_episodes_are_contiguous_blocks():
    assert e.episodes(np.array([0, 1, 1, 0, 1, 0, 1, 1], dtype=bool)) == [(1, 2), (4, 4), (6, 7)]
    assert e.episodes(np.zeros(5, dtype=bool)) == []


def test_intervals_are_sane():
    low, high = e.wilson(0, 10)
    assert low == 0.0 and 0.2 < high < 0.4
    low, high = e.wilson(10, 10)
    assert 0.6 < low < 0.8 and high == pytest.approx(1.0)
    assert e.wilson(0, 0) == (0.0, 1.0)
    low, high = e.poisson_rate_interval(0, 2.0)
    assert low == 0.0 and high > 1.0
    low, high = e.poisson_rate_interval(12, 2.0)
    assert low < 6.0 < high


def test_features_use_only_the_past():
    transformed = f.transform(_raw(60))
    fill = np.nanmedian(transformed, axis=0)
    before, index = f.window_features(transformed, fill)
    changed = transformed.copy()
    changed[40:] = 999.0  # rewrite the future of step 39
    after, _ = f.window_features(changed, fill)
    upto = int(np.flatnonzero(index == 39)[0])
    assert np.allclose(before[: upto + 1], after[: upto + 1])
    assert not np.allclose(before[upto + 1 :], after[upto + 1 :])


def test_transform_logs_wide_range_signals_and_forward_fills_only_backwards_in_time():
    raw = _raw(30)
    raw[10, 2] = np.nan
    out = f.transform(raw)
    assert out[10, 2] == out[9, 2]  # carried forward from the past
    assert out[5, f.SIGNAL_NAMES.index("ingest_latency_p95_ms")] == pytest.approx(
        np.log1p(raw[5, 2])
    )
    leading = _raw(30)
    leading[:3, 0] = np.nan
    assert np.isnan(f.transform(leading)[0, 0])  # nothing earlier to carry: stays missing


def test_the_slo_baseline_scores_one_at_the_limit_and_never_alarms_on_a_missing_value():
    prepared = d.Prepared(_run("a"), np.nanmedian(f.transform(_raw(60)), axis=0))
    prepared.raw = np.tile(np.array([20.0, 0.05, 2000.0, 0.99, 15.0, 0.004, 120.0, 0.98]), (5, 1))
    scores = d.SloThresholds().score(prepared)
    assert scores == pytest.approx(np.full(5, 1.0))
    prepared.raw = np.full((5, N_SIGNALS), np.nan)
    assert d.SloThresholds().score(prepared).max() <= 1.0


def _prepared(runs):
    fill = d.fit_fill(runs)
    return fill, [d.Prepared(r, fill) for r in runs]


def test_threshold_selection_uses_the_false_alarm_budget_and_the_detectors_find_a_gross_fault():
    train = [_run(f"t{i}") for i in range(4)]
    fill, prepared = _prepared(train)
    val = [_run("v-normal"), _run("v-fault", fault=(40, 70))]
    val_prepared = [d.Prepared(r, fill) for r in val]
    model = d.RobustZScore().fit(prepared)
    scores = [model.score(p) for p in val_prepared]
    threshold, summary = e.select_threshold(val_prepared, scores)
    assert summary["false_alarms_per_hour"] <= e.FALSE_ALARM_BUDGET_PER_HOUR
    assert summary["event_recall"] == 1.0  # a 20x latency jump and 30% errors are found
    assert summary["median_delay_s"] is not None and summary["median_delay_s"] <= 30


def test_live_scoring_of_a_window_matches_batch_scoring_of_the_same_data():
    train = [_run(f"t{i}") for i in range(4)]
    fill, prepared = _prepared(train)
    model = d.IsolationForestDetector(trees=30).fit(prepared)
    scale = d.fit_scale(train, fill)
    live = OperationsDetector(
        {"detector": model, "fill": fill, "scale": scale, "threshold": 0.0, "feature_version": f.FEATURE_VERSION},
        {},
    )  # fmt: skip
    test = _run("x", fault=(45, 70))
    batch = model.score(d.Prepared(test, fill))
    index = d.Prepared(test, fill).index
    for k in (0, 10, 25, len(index) - 1):
        i = int(index[k])
        window = test.raw[i - f.HISTORY + 1 : i + 1]
        assert live.score(window) == pytest.approx(batch[k], abs=1e-9)


def test_the_deviation_names_the_signal_that_moved():
    train = [_run(f"t{i}") for i in range(4)]
    fill = d.fit_fill(train)
    scale = d.fit_scale(train, fill)
    live = OperationsDetector(
        {"detector": None, "fill": fill, "scale": scale, "threshold": 0.0, "feature_version": f.FEATURE_VERSION},
        {},
    )  # fmt: skip
    rows = _raw(20, fault=(15, 20))
    deviations = live.deviations(rows)
    worst = int(np.argmax(deviations[-1]))
    assert f.SIGNAL_NAMES[worst] in ("ingest_latency_p95_ms", "api_error_ratio")
    assert (
        deviations[2].max() < 5.0
    )  # a healthy step deviates little from the training-normal median


def test_a_package_without_a_scale_cannot_attribute_but_still_scores():
    live = OperationsDetector(
        {"detector": None, "fill": np.zeros(N_SIGNALS), "threshold": 1.0, "feature_version": f.FEATURE_VERSION}, {}
    )  # fmt: skip
    with pytest.raises(ValueError):
        live.deviations(_raw(10))
    assert live.alarm([1.0, 1.0, 1.0]) is True
    assert live.alarm([1.0, 0.5, 1.0]) is False
    assert live.alarm([1.0, 1.0]) is False  # fewer than the persistence
