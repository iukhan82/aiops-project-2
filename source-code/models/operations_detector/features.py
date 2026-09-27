"""P10.06: loading the operational dataset and the causal features the detectors score.

A feature row describes one 5 s step of one run using ONLY that run's own past: the eight signals at this step, their mean
over the last 20 s, their standard deviation over the last 40 s, and how far the signal has moved compared with the 20 s
before that. Nothing looks ahead, nothing crosses a run boundary, and truth (fault windows, severity, fault type) is
loaded separately and is never part of a feature - it is used only to train the supervised candidate, to choose
thresholds on the validation split and to score.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from models.operations_dataset.load import WARMUP_SECONDS  # noqa: E402
from models.operations_dataset.signals import SIGNALS  # noqa: E402

FEATURE_VERSION = "ops-window/1"
DATASET_ROOT = SOURCE_ROOT / "models" / "operations_dataset" / "output"
SIGNAL_NAMES = tuple(SIGNALS)
STEP_S = 5
# A signal is meaningful from warm-up + its 30 s rate window; a feature also needs 40 s of history.
HISTORY = 8  # steps of history (40 s)
RECENT = 4  # steps (20 s)
FIRST_STEP = math.ceil((WARMUP_SECONDS + 30) / STEP_S) + HISTORY - 1

# Latencies and rates span orders of magnitude; ratios and shares already sit in [0, 1].
LOG_SIGNALS = {"ingest_rate", "ingest_latency_p95_ms", "api_request_rate", "api_latency_p95_ms"}


@dataclass
class Run:
    run_id: str
    split: str
    seed: int
    scenario: str
    replicate: int
    raw: np.ndarray  # steps x signals, NaN where Prometheus had no value
    fault: np.ndarray  # bool per step
    burst: np.ndarray  # bool per step
    severity: float
    plan: dict

    @property
    def steps(self) -> int:
        return self.raw.shape[0]

    @property
    def fault_span(self) -> tuple[int, int] | None:
        idx = np.flatnonzero(self.fault)
        return (int(idx[0]), int(idx[-1])) if idx.size else None


def load_split(split: str, label: str = "run-a") -> list[Run]:
    root = DATASET_ROOT / label / split
    runs = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        meta = json.loads((directory / "run.json").read_text(encoding="utf-8"))
        signals = [
            json.loads(line)
            for line in (directory / "signals.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        truth = [
            json.loads(line)
            for line in (directory / "truth.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        raw = np.array(
            [
                [np.nan if r["values"][n] is None else r["values"][n] for n in SIGNAL_NAMES]
                for r in signals
            ],
            dtype=float,
        )
        runs.append(
            Run(
                run_id=meta["run_id"],
                split=split,
                seed=meta["seed"],
                scenario=meta["scenario"],
                replicate=meta["replicate"],
                raw=raw,
                fault=np.array([t["fault_active"] for t in truth]),
                burst=np.array([t["benign_burst"] for t in truth]),
                severity=float(meta["plan"]["severity"]),
                plan=meta["plan"],
            )
        )
    return runs


def transform(raw: np.ndarray) -> np.ndarray:
    """Log-scale the wide-range signals; forward-fill gaps within the run (causal). Leading gaps stay NaN."""
    out = raw.copy()
    for j, name in enumerate(SIGNAL_NAMES):
        if name in LOG_SIGNALS:
            out[:, j] = np.log1p(np.clip(out[:, j], 0.0, None))
    for j in range(out.shape[1]):
        last = np.nan
        for i in range(out.shape[0]):
            if math.isnan(out[i, j]):
                out[i, j] = last
            else:
                last = out[i, j]
    return out


def window_features(
    transformed: np.ndarray, fill: np.ndarray, first_step: int = FIRST_STEP
) -> tuple[np.ndarray, np.ndarray]:
    """Feature rows for every step from `first_step` on (a whole run starts at FIRST_STEP; a short live window at
    HISTORY - 1). `fill` (per signal) replaces values still missing after the forward fill, taken from the TRAINING
    normal data only."""
    x = np.where(np.isnan(transformed), fill[None, :], transformed)
    rows, index = [], []
    for i in range(first_step, x.shape[0]):
        window = x[i - HISTORY + 1 : i + 1]
        recent = window[-RECENT:]
        earlier = window[:RECENT]
        rows.append(
            np.concatenate(
                [x[i], recent.mean(axis=0), window.std(axis=0), x[i] - earlier.mean(axis=0)]
            )
        )
        index.append(i)
    return np.array(rows), np.array(index)


FEATURE_NAMES = tuple(
    f"{prefix}:{name}"
    for prefix in ("now", "mean20s", "std40s", "delta_vs_20s_earlier")
    for name in SIGNAL_NAMES
)
