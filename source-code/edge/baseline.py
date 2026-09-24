"""P04.02: transparent rule baseline for road-blockage detection.

This is both the correctness floor the trained model must beat (ADR-0005)
and the runtime fallback when a model artifact is absent, invalid or
degraded (P04.05/P04.08). Every alarm carries the list of conditions that
fired, so an operator can see exactly why.

Parameters are fitted offline on the training split only
(source-code/models/baselines/fit_evaluate.py) and loaded from a versioned
JSON artifact; nothing here learns at runtime.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

BASELINE_FORMAT = "edge-rule-baseline/1"
VARIANTS = ("occupancy_threshold", "occupancy_persistence", "occupancy_flow_speed")


class BaselineError(ValueError):
    """The baseline artifact is missing, malformed or incompatible."""


@dataclass(frozen=True)
class RuleParams:
    variant: str
    occ_last_min: float
    occ_mean_min: float | None = None
    count_last_max: float | None = None
    speed_last_max: float | None = None

    def __post_init__(self) -> None:
        if self.variant not in VARIANTS:
            raise BaselineError(f"unknown baseline variant {self.variant!r}")
        if self.variant == "occupancy_persistence" and self.occ_mean_min is None:
            raise BaselineError("occupancy_persistence needs occ_mean_min")
        if self.variant == "occupancy_flow_speed" and (
            self.count_last_max is None or self.speed_last_max is None
        ):
            raise BaselineError("occupancy_flow_speed needs count_last_max and speed_last_max")


@dataclass(frozen=True)
class Decision:
    alarm: bool
    score: float  # evidence strength (occupancy); NOT a calibrated probability
    fired: tuple[str, ...]


class RuleBaseline:
    def __init__(
        self,
        params: RuleParams,
        feature_names: Sequence[str],
        baseline_id: str = "rules/unversioned",
        feature_version: str = "",
    ) -> None:
        self.params = params
        self.baseline_id = baseline_id
        self.feature_version = feature_version
        self._names = tuple(feature_names)
        needed = {"occ_last", "occ_mean", "count_last", "speed_last"}
        missing = needed - set(self._names)
        if missing:
            raise BaselineError(f"feature set lacks {sorted(missing)}")
        self._ix = {name: i for i, name in enumerate(self._names)}

    @classmethod
    def load(cls, path: Path, expected_feature_version: str | None = None) -> RuleBaseline:
        try:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BaselineError(f"cannot read baseline artifact: {exc}") from exc
        if doc.get("format") != BASELINE_FORMAT:
            raise BaselineError(f"unsupported baseline format {doc.get('format')!r}")
        if expected_feature_version and doc.get("feature_version") != expected_feature_version:
            raise BaselineError(
                f"baseline expects features {doc.get('feature_version')!r}, "
                f"runtime provides {expected_feature_version!r}"
            )
        try:
            return cls(
                RuleParams(**doc["params"]),
                doc["feature_names"],
                baseline_id=doc["baseline_id"],
                feature_version=doc["feature_version"],
            )
        except (KeyError, TypeError) as exc:
            raise BaselineError(f"malformed baseline artifact: {exc}") from exc

    def evaluate(self, values: Sequence[float]) -> Decision:
        if len(values) != len(self._names):
            raise BaselineError(f"expected {len(self._names)} features, got {len(values)}")
        p, ix = self.params, self._ix
        occ_last = float(values[ix["occ_last"]])
        fired: list[str] = []
        if occ_last >= p.occ_last_min:
            fired.append(f"occ_last>={p.occ_last_min:g}")
            if p.variant == "occupancy_threshold":
                return Decision(True, occ_last, tuple(fired))
            if p.variant == "occupancy_persistence":
                if float(values[ix["occ_mean"]]) >= p.occ_mean_min:
                    fired.append(f"occ_mean>={p.occ_mean_min:g}")
                    return Decision(True, occ_last, tuple(fired))
            else:
                if (
                    float(values[ix["count_last"]]) <= p.count_last_max
                    and float(values[ix["speed_last"]]) <= p.speed_last_max
                ):
                    fired.append(f"count_last<={p.count_last_max:g}")
                    fired.append(f"speed_last<={p.speed_last_max:g}")
                    return Decision(True, occ_last, tuple(fired))
        return Decision(False, occ_last, ())

    def predict_batch(self, X: np.ndarray) -> np.ndarray:
        p, ix = self.params, self._ix
        alarm = X[:, ix["occ_last"]] >= p.occ_last_min
        if p.variant == "occupancy_persistence":
            alarm &= X[:, ix["occ_mean"]] >= p.occ_mean_min
        elif p.variant == "occupancy_flow_speed":
            alarm &= X[:, ix["count_last"]] <= p.count_last_max
            alarm &= X[:, ix["speed_last"]] <= p.speed_last_max
        return alarm.astype(np.int8)
