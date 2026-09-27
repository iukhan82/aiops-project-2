"""P10.06: the detectors. Every one turns a run's causal feature rows into one anomaly score per 5 s step (higher = more
anomalous); the alarm rule and the threshold are separate (`evaluate.py`, chosen on the validation split only).

- `slo_thresholds`   transparent baseline A: the SLO limits the P10.04 alert rules use, as a "how many times over the
                     limit" score (1.0 = at the limit). Needs no data. This is what an operator gets without a model.
- `robust_zscore`    transparent baseline B: the largest robust z-score of any signal against the training-normal
                     distribution (median and MAD). Unsupervised and simple.
- `isolation_forest` candidate model, unsupervised: fitted on training NORMAL steps only, so it can flag a degradation
                     nobody labelled.
- `gradient_boosting` candidate model, supervised: fitted on the labelled training steps; can only be as good as the fault
                     types it was shown.
"""

from __future__ import annotations

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier, IsolationForest
from sklearn.preprocessing import RobustScaler

from models.operations_detector.features import (
    FIRST_STEP,
    SIGNAL_NAMES,
    Run,
    transform,
    window_features,
)

# signal -> (limit, direction). "up": bad when above; "down": bad when below. The same numbers as backend/slos.json.
SLO_LIMITS = {
    "ingest_reject_ratio": (0.05, "up"),
    "ingest_latency_p95_ms": (2000.0, "up"),
    "gateway_delivery_ratio": (0.5, "down"),
    "api_error_ratio": (0.01, "up"),
    "api_latency_p95_ms": (2000.0, "up"),
    "state_fresh_share": (0.95, "down"),
}


class Prepared:
    """One run, prepared once: raw and transformed signals at the scored steps, and the feature rows."""

    def __init__(self, run: Run, fill: np.ndarray) -> None:
        self.run = run
        transformed = transform(run.raw)
        self.features, self.index = window_features(transformed, fill)
        self.raw = np.where(np.isnan(run.raw[self.index]), np.nan, run.raw[self.index])
        self.now = np.where(
            np.isnan(transformed[self.index]), fill[None, :], transformed[self.index]
        )
        self.fault = run.fault[self.index]
        self.burst = run.burst[self.index]


def fit_fill(train_runs: list[Run]) -> np.ndarray:
    """Per-signal median of the transformed TRAINING normal steps: what stands in for a still-missing value."""
    stacks = []
    for run in train_runs:
        t = transform(run.raw)[FIRST_STEP:]
        stacks.append(t[~run.fault[FIRST_STEP:]])
    data = np.vstack(stacks)
    return np.nanmedian(data, axis=0)


def fit_scale(train_runs: list[Run], fill: np.ndarray) -> np.ndarray:
    """Per-signal robust spread (MAD, scaled to a standard deviation) of the TRAINING normal steps, with a floor so a
    signal that barely moves cannot make any change look enormous. Used only to say WHICH signal moved, never to decide
    whether anything is wrong."""
    stacks = []
    for run in train_runs:
        t = transform(run.raw)[FIRST_STEP:]
        stacks.append(t[~run.fault[FIRST_STEP:]])
    data = np.vstack(stacks)
    mad = np.nanmedian(np.abs(data - fill[None, :]), axis=0) * 1.4826
    return np.maximum(mad, 1e-3 + 0.02 * np.abs(fill))


class SloThresholds:
    name = "slo_thresholds"
    supervised = False

    def fit(self, train: list[Prepared]) -> SloThresholds:
        return self

    def score(self, p: Prepared) -> np.ndarray:
        ratios = []
        for signal, (limit, direction) in SLO_LIMITS.items():
            x = p.raw[:, SIGNAL_NAMES.index(signal)]
            x = np.where(
                np.isnan(x), limit if direction == "up" else 1.0, x
            )  # a missing value never alarms
            ratios.append(x / limit if direction == "up" else limit / np.maximum(x, 1e-6))
        return np.max(np.vstack(ratios), axis=0)


class RobustZScore:
    name = "robust_zscore"
    supervised = False

    def fit(self, train: list[Prepared]) -> RobustZScore:
        normal = np.vstack([p.now[~p.fault] for p in train])
        self.median = np.median(normal, axis=0)
        mad = np.median(np.abs(normal - self.median), axis=0) * 1.4826
        self.scale = np.maximum(mad, 1e-3 + 0.02 * np.abs(self.median))
        return self

    def score(self, p: Prepared) -> np.ndarray:
        return np.max(np.abs(p.now - self.median) / self.scale, axis=1)


class IsolationForestDetector:
    name = "isolation_forest"
    supervised = False

    def __init__(self, seed: int = 20260925, trees: int = 200) -> None:
        self.seed, self.trees = seed, trees

    def fit(self, train: list[Prepared]) -> IsolationForestDetector:
        normal = np.vstack([p.features[~p.fault] for p in train])
        self.scaler = RobustScaler().fit(normal)
        self.model = IsolationForest(
            n_estimators=self.trees, max_samples=512, random_state=self.seed, n_jobs=1
        ).fit(self.scaler.transform(normal))
        return self

    def score(self, p: Prepared) -> np.ndarray:
        return -self.model.decision_function(self.scaler.transform(p.features))


class GradientBoostingDetector:
    name = "gradient_boosting"
    supervised = True

    def __init__(self, seed: int = 20260925) -> None:
        self.seed = seed

    def fit(self, train: list[Prepared]) -> GradientBoostingDetector:
        x = np.vstack([p.features for p in train])
        y = np.concatenate([p.fault for p in train])
        self.scaler = RobustScaler().fit(x)
        self.model = GradientBoostingClassifier(
            n_estimators=150, max_depth=3, learning_rate=0.1, subsample=0.8, random_state=self.seed
        ).fit(self.scaler.transform(x), y)
        return self

    def score(self, p: Prepared) -> np.ndarray:
        return self.model.predict_proba(self.scaler.transform(p.features))[:, 1]


def make_detectors() -> list:
    return [SloThresholds(), RobustZScore(), IsolationForestDetector(), GradientBoostingDetector()]
