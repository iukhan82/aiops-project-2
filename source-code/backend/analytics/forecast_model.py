"""P06.03: the trained forecast package - training-time fitting helpers,
hash-verified packaging, inference with interval and fallback, and the
drift measures (PSI, rolling-error monitor).

A model here predicts the *residual over persistence* (y[t+h] - y[t]); the
interval is split-conformal on validation residuals. Per (horizon, target) the
package records whether the model earned its place against the P06.02 baseline
on VALIDATION (>= ACCEPT_MARGIN lower MAE, decided before TEST is opened); if
it did not, the package serves the baseline instead and says so in every
forecast it emits.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from backend.analytics.forecast_baselines import TimeOfDayMean, predict_baseline
from backend.analytics.forecast_data import (
    TARGETS,
    SampleSet,
    feature_matrix,
)

MODEL_ID = "traffic-forecast"
MODEL_VERSION = "1.0.0"
ACCEPT_MARGIN = 0.03
DRIFT_RATIO = 2.0
DRIFT_WINDOW = 12
PACKAGE_FILE = "models.joblib"
MANIFEST_FILE = "artifact_manifest.json"
SEED = 20260918

CANDIDATES: list[tuple[str, dict]] = [("ridge", {"alpha": a}) for a in (1.0, 10.0, 100.0)] + [
    (
        "hgb",
        {
            "max_depth": d,
            "learning_rate": lr,
            "max_iter": 150,
            "min_samples_leaf": 30,
            "l2_regularization": 1.0,
        },
    )
    for d in (3, None)
    for lr in (0.05, 0.1)
]


class IntegrityError(RuntimeError):
    pass


def fit_candidate(kind: str, params: dict, X: np.ndarray, residual: np.ndarray):
    if kind == "ridge":
        est = make_pipeline(StandardScaler(), Ridge(**params))
    else:
        est = HistGradientBoostingRegressor(random_state=SEED, **params)
    return est.fit(X, residual)


@dataclass
class Entry:
    horizon_seconds: int
    target: str
    kind: str
    params: dict
    estimator: object
    halfwidth: float
    accepted: bool
    baseline: str
    baseline_halfwidth: float
    val_mae: float
    baseline_val_mae: float


class ForecastPackage:
    def __init__(
        self, entries: dict[tuple[int, str], Entry], tod: TimeOfDayMean, meta: dict
    ) -> None:
        self.entries, self.tod, self.meta = entries, tod, meta

    def predict(self, s: SampleSet, horizon_s: int, target: str) -> dict:
        """Forecast for every row of `s`: value, interval half-width, source."""
        entry = self.entries[(horizon_s, target)]
        j = TARGETS.index(target)
        baseline = predict_baseline(entry.baseline, s, self.tod)[:, j]
        if entry.accepted:
            value = s.hist[:, -1, j] + entry.estimator.predict(feature_matrix(s))
            return {
                "value": np.maximum(value, 0.0),
                "halfwidth": entry.halfwidth,
                "source": "model",
                "baseline": baseline,
            }
        return {
            "value": baseline,
            "halfwidth": entry.baseline_halfwidth,
            "source": f"baseline:{entry.baseline}",
            "baseline": baseline,
        }


def package_hash(path: Path) -> str:
    return hashlib.sha256((path / PACKAGE_FILE).read_bytes()).hexdigest()


def save_package(pkg: ForecastPackage, directory: Path, card: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {"entries": pkg.entries, "tod": pkg.tod, "meta": pkg.meta}, directory / PACKAGE_FILE
    )
    (directory / MANIFEST_FILE).write_text(
        json.dumps(
            {
                "artifact": PACKAGE_FILE,
                "sha256": package_hash(directory),
                "model_id": MODEL_ID,
                "model_version": MODEL_VERSION,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (directory / "model_card.json").write_text(
        json.dumps(card, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def load_package(directory: Path) -> ForecastPackage:
    """Refuses a package whose bytes do not match its manifest hash: a
    tampered or half-written artifact must never be deserialized."""
    manifest_path, artifact = directory / MANIFEST_FILE, directory / PACKAGE_FILE
    if not manifest_path.is_file() or not artifact.is_file():
        raise IntegrityError(f"forecast package incomplete in {directory}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if package_hash(directory) != manifest["sha256"]:
        raise IntegrityError("forecast package hash does not match its manifest")
    blob = joblib.load(artifact)
    return ForecastPackage(blob["entries"], blob["tod"], blob["meta"])


def psi(expected: np.ndarray, actual: np.ndarray, bins: int = 10) -> float:
    """Population stability index of `actual` against `expected`'s quantile
    bins (<0.1 stable, 0.1-0.25 moderate shift, >0.25 major shift)."""
    edges = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    e = np.histogram(expected, edges)[0] / len(expected)
    a = np.histogram(actual, edges)[0] / len(actual)
    e, a = np.clip(e, 1e-4, None), np.clip(a, 1e-4, None)
    return float(np.sum((a - e) * np.log(a / e)))


def drift_flag(
    recent_errors: list[float],
    reference_error: float,
    ratio: float = DRIFT_RATIO,
    window: int = DRIFT_WINDOW,
) -> bool:
    """True once the trailing `window` relative errors average more than
    `ratio` x the validation relative error - the signal to stop trusting the
    model and serve the baseline instead."""
    return (
        len(recent_errors) >= window
        and float(np.mean(recent_errors[-window:])) > ratio * reference_error
    )


def series_flagged(
    meta: list[dict], rel_err: np.ndarray, reference_error: float
) -> tuple[int, int]:
    """(series flagged, series total) over (run, corridor, direction) sequences."""
    groups: dict[tuple, list[tuple[int, float]]] = {}
    for m, e in zip(meta, rel_err, strict=True):
        groups.setdefault((m["run_id"], m["corridor_id"], m["direction"]), []).append(
            (m["t"], float(e))
        )
    flagged = 0
    for seq in groups.values():
        errs = [e for _, e in sorted(seq)]
        if any(drift_flag(errs[: i + 1], reference_error) for i in range(len(errs))):
            flagged += 1
    return flagged, len(groups)
