"""Host-side loader: turns the edge dataset's event files into a leakage-safe
feature table using the *same* `edge.validation` / `edge.features` code the
runtime uses, so training and serving cannot drift apart.

Ground truth (labels) is joined in only after features are computed, and is
never an input to feature construction.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np

from edge.features import FEATURE_NAMES, FEATURE_VERSION, FeatureBuilder, FeatureConfig
from edge.timeutil import parse_ts
from edge.topology import loop_topology
from edge.validation import DeviceRegistry, EdgeValidator, ValidationConfig

DEFAULT_ROOT = Path(__file__).resolve().parent / "output" / "run-a"
GEOMETRY_VERSION = "2026-09-18.1"
SPLITS = ("train", "validation", "test")


@dataclass
class FeatureTable:
    names: tuple[str, ...]
    X: np.ndarray
    y: np.ndarray
    meta: list[dict]
    dropped_insufficient: int
    feature_version: str

    def __len__(self) -> int:
        return len(self.y)

    def mask(self, **conditions) -> np.ndarray:
        return np.array(
            [all(m[k] == v for k, v in conditions.items()) for m in self.meta], dtype=bool
        )


def load_manifest(root: Path = DEFAULT_ROOT) -> dict:
    return json.loads((root / "dataset_manifest.json").read_text(encoding="utf-8"))


def verify_manifest(root: Path = DEFAULT_ROOT) -> list[str]:
    manifest = load_manifest(root)
    problems = []
    for relative, expected in manifest["files"].items():
        path = root / relative
        if not path.is_file():
            problems.append(f"missing: {relative}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            problems.append(f"hash mismatch: {relative}")
    digest = hashlib.sha256(
        "\n".join(f"{k}:{v}" for k, v in sorted(manifest["files"].items())).encode()
    ).hexdigest()
    if digest != manifest["dataset_sha256"]:
        problems.append("dataset_sha256 mismatch")
    return problems


def run_dirs(root: Path, split: str) -> list[Path]:
    return sorted(p for p in (root / split).iterdir() if p.is_dir())


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def build_feature_table(
    root: Path = DEFAULT_ROOT,
    splits: tuple[str, ...] = SPLITS,
    feature_config: FeatureConfig | None = None,
) -> FeatureTable:
    registry = DeviceRegistry.from_jsonl(root / "devices.jsonl")
    topology = loop_topology(registry)
    rows: list[list[float]] = []
    labels: list[int] = []
    meta: list[dict] = []
    dropped = 0

    for split in splits:
        for run_dir in run_dirs(root, split):
            events = _read_jsonl(run_dir / "events.jsonl")
            label_by_event = {r["event_id"]: r for r in _read_jsonl(run_dir / "labels.jsonl")}
            validator = EdgeValidator(
                registry, ValidationConfig(expected_geometry_version=GEOMETRY_VERSION)
            )
            builder = FeatureBuilder(feature_config, topology)
            # Decision-tick semantics (must match the runtime): every event of an
            # interval boundary is ingested first, then features are evaluated
            # for that tick, so neighbor context never depends on arrival order
            # within the tick.
            for _, group in itertools.groupby(events, key=lambda e: e["observation_time"]):
                tick = list(group)
                for event in tick:
                    now = parse_ts(event["ingest_time"]) + timedelta(seconds=0.5)
                    builder.update(validator.validate(event, now))
                for event in tick:
                    result = builder.build(event["device_id"], parse_ts(event["observation_time"]))
                    if result.values is None:
                        dropped += 1
                        continue
                    label = label_by_event[event["event_id"]]  # joined AFTER features exist
                    rows.append(list(result.values))
                    labels.append(label["label"])
                    meta.append(
                        {
                            "run_id": label["run_id"],
                            "split": label["split"],
                            "seed": label["seed"],
                            "scenario": label["scenario"],
                            "run_spec": label["run_spec"],
                            "demand_scale": label["demand_scale"],
                            "stall_distance_from_loop_m": label["stall_distance_from_loop_m"],
                            "device_id": event["device_id"],
                            "interval_end": event["observation_time"],
                            "incident_id": label["incident_id"],
                            "quality": result.quality,
                            "event_id": event["event_id"],
                        }
                    )
    return FeatureTable(
        names=FEATURE_NAMES,
        X=np.asarray(rows, dtype=np.float32),
        y=np.asarray(labels, dtype=np.int8),
        meta=meta,
        dropped_insufficient=dropped,
        feature_version=FEATURE_VERSION,
    )


def load_incidents(root: Path = DEFAULT_ROOT, splits: tuple[str, ...] = SPLITS) -> dict[str, dict]:
    """Measured ground-truth incident windows keyed by incident_id."""
    incidents: dict[str, dict] = {}
    for split in splits:
        for run_dir in run_dirs(root, split):
            for record in _read_jsonl(run_dir / "incidents.jsonl"):
                incidents[record["incident_id"]] = record
    return incidents
