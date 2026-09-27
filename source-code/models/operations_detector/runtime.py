"""P10.06: score one live window with a packaged detector - the interface P10.07 uses on real Prometheus signals.

A caller keeps the most recent `HISTORY` rows of the eight signals (5 s apart, `None`/NaN for a missing value), in the order
of `features.SIGNAL_NAMES`, and asks for the score of the newest step. The same feature code as training runs on that
window, so a live score and a batch score of the same data agree (`train_evaluate.py` measures the difference).
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np

from models.operations_detector.features import (
    FEATURE_VERSION,
    HISTORY,
    SIGNAL_NAMES,
    transform,
    window_features,
)
from models.operations_detector.evaluate import PERSISTENCE


class OperationsDetector:
    def __init__(self, bundle: dict, card: dict) -> None:
        self.detector = bundle["detector"]
        self.fill = bundle["fill"]
        self.threshold = float(bundle["threshold"])
        self.scale = bundle.get("scale")
        self.card = card

    @classmethod
    def load(cls, package_dir: Path) -> OperationsDetector:
        bundle = joblib.load(package_dir / "model.joblib")
        card = json.loads((package_dir / "model_card.json").read_text(encoding="utf-8"))
        if bundle["feature_version"] != FEATURE_VERSION:
            raise ValueError("the package was trained with a different feature version")
        return cls(bundle, card)

    def score(self, history: np.ndarray) -> float:
        """`history`: at least HISTORY rows x len(SIGNAL_NAMES), oldest first. Returns the newest step's score."""
        if history.shape[1] != len(SIGNAL_NAMES) or history.shape[0] < HISTORY:
            raise ValueError(f"need at least {HISTORY} rows of {len(SIGNAL_NAMES)} signals")
        return float(self.score_all(history[-HISTORY:])[-1])

    def score_all(self, history: np.ndarray) -> np.ndarray:
        """Scores for every step of `history` that has enough past (used to compare with batch scoring)."""
        from models.operations_detector.features import Run

        run = Run(
            run_id="live", split="live", seed=0, scenario="live", replicate=0, raw=history.astype(float),
            fault=np.zeros(history.shape[0], dtype=bool), burst=np.zeros(history.shape[0], dtype=bool),
            severity=0.0, plan={},
        )  # fmt: skip
        prepared = _prepare_from_window(run, self.fill)
        return self.detector.score(prepared)

    def deviations(self, history: np.ndarray) -> np.ndarray:
        """How far each signal is from the training-normal median, in robust standard deviations, for every step of
        `history` (steps x signals). Says which signal moved; the score alone says only that something did."""
        if self.scale is None:
            raise ValueError("this package carries no per-signal scale")
        transformed = transform(history.astype(float))
        filled = np.where(np.isnan(transformed), self.fill[None, :], transformed)
        return np.abs(filled - self.fill[None, :]) / self.scale[None, :]

    def alarm(self, recent_scores: list[float]) -> bool:
        """The alarm rule: the last `PERSISTENCE` scores are all at or above the threshold."""
        return len(recent_scores) >= PERSISTENCE and all(
            s >= self.threshold for s in recent_scores[-PERSISTENCE:]
        )


def _prepare_from_window(run, fill):
    """Like `Prepared`, for a window that may be as short as HISTORY rows: the first scored step is the first with a
    full history inside the window."""
    from types import SimpleNamespace

    transformed = transform(run.raw)
    features, index = window_features(transformed, fill, first_step=HISTORY - 1)
    zeros = np.zeros(len(features), dtype=bool)
    return SimpleNamespace(
        features=features,
        raw=run.raw[index],
        now=np.where(np.isnan(transformed[index]), fill[None, :], transformed[index]),
        fault=zeros,
        burst=zeros,
        run=run,
    )
