"""P06.06 evaluation plumbing: clean SUMO trajectories -> what an edge tracker
would observe -> the edge conflict detector -> comparison with realized PET.

The tracker model is the *degradation* between truth and detector: frames at
1 Hz, Gaussian position noise, and random missed detections. Truth (realized
PET from the clean 4 Hz trajectories) is computed independently and never
reaches the detector. Local track ids passed to the detector are SUMO object
ids because the harness has to match pairs to truth; the production path
(edge/vru_conflict.py `ConflictAggregator`) rehashes them every window and the
exporter never sees them.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from edge.vru_conflict import ConflictEvent, ConflictParams, PairScorer, TrackSample, run_site  # noqa: E402
from models.vru_dataset.truth import Interaction, Piece, load_pieces, realized_interactions  # noqa: E402

DATASET = SOURCE_ROOT / "models" / "vru_dataset" / "output" / "run-a"
CLASS_NAME = {"ped": "pedestrian", "cyc": "cyclist", "veh": "vehicle"}
SPLITS = ("train", "validation", "test")


@dataclass(frozen=True)
class Tracker:
    """The observation model between the physical scene and the detector."""

    frame_s: float = 1.0
    position_sigma_m: float = 0.4
    dropout: float = 0.05


@dataclass
class Run:
    name: str
    split: str
    spec: str
    pieces: list[Piece]
    truth: list[Interaction]
    hours: float = 0.5
    frames_cache: dict = field(default_factory=dict)


def load_runs(splits: tuple[str, ...] = SPLITS, label_dir: Path = DATASET) -> dict[str, list[Run]]:
    out: dict[str, list[Run]] = {}
    for split in splits:
        out[split] = []
        for run_dir in sorted((label_dir / split).iterdir()):
            meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            pieces = load_pieces(run_dir / "tracks.csv.gz")
            out[split].append(
                Run(
                    meta["run_id"],
                    split,
                    meta["spec"],
                    pieces,
                    realized_interactions(pieces),
                    meta["sim_end_s"] / 3600.0,
                )
            )
    return out


def _rng(run: Run, tracker: Tracker) -> np.random.Generator:
    digest = hashlib.sha256(f"{run.name}:{tracker}".encode()).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


def tracker_frames(run: Run, tracker: Tracker) -> dict[str, dict[float, list[TrackSample]]]:
    """site -> frame time -> samples. Cached per (run, tracker)."""
    key = tracker
    if key in run.frames_cache:
        return run.frames_cache[key]
    rng = _rng(run, tracker)
    frames: dict[str, dict[float, list[TrackSample]]] = defaultdict(lambda: defaultdict(list))
    stride = round(tracker.frame_s / 0.25)
    for piece in run.pieces:
        keep = np.flatnonzero(np.round(piece.t / 0.25).astype(int) % stride == 0)
        if len(keep) == 0:
            continue
        keep = keep[rng.random(len(keep)) >= tracker.dropout]
        noise = rng.normal(0.0, tracker.position_sigma_m, size=(len(keep), 2))
        cls = CLASS_NAME[piece.cls]
        for k, (dx, dy) in zip(keep, noise, strict=True):
            t = float(piece.t[k])
            frames[piece.site][t].append(
                TrackSample(
                    t, piece.obj_id, cls, float(piece.xy[k, 0] + dx), float(piece.xy[k, 1] + dy)
                )
            )
    run.frames_cache[key] = {s: dict(f) for s, f in frames.items()}
    return run.frames_cache[key]


def detect(
    run: Run,
    tracker: Tracker,
    params: ConflictParams,
    scorer: PairScorer | None = None,
    trace: list | None = None,
) -> list[ConflictEvent]:
    events: list[ConflictEvent] = []
    for site, frames in tracker_frames(run, tracker).items():
        events.extend(run_site(site, frames, params, scorer, trace).events)
    return events


@dataclass
class Match:
    tp: list[tuple[ConflictEvent, Interaction]] = field(default_factory=list)
    near: list[tuple[ConflictEvent, Interaction]] = field(
        default_factory=list
    )  # realized, but PET above the alert threshold
    fp: list[ConflictEvent] = field(default_factory=list)
    missed: list[Interaction] = field(default_factory=list)
    truth_total: int = 0


def plain_pet(i: Interaction) -> bool:
    return i.pet_s <= 3.0


def risk_weighted(i: Interaction) -> bool:
    return i.is_conflict


def match(run: Run, events: list[ConflictEvent], is_positive=risk_weighted) -> Match:
    """`tp`: the event's pair really was a positive; `near`: the pair did interact (PET <= 5 s) but is not a positive;
    `fp`: the pair never interacted within 5 s."""
    truth = {(i.site, i.vru_id, i.veh_id): i for i in run.truth}
    result = Match()
    hit = set()
    for e in events:
        i = truth.get((e.site, e.vru_track, e.vehicle_track))
        if i is not None and is_positive(i):
            result.tp.append((e, i))
            hit.add((i.site, i.vru_id, i.veh_id))
        elif i is not None:
            result.near.append((e, i))
        else:
            result.fp.append(e)
    for key, i in truth.items():
        if is_positive(i):
            result.truth_total += 1
            if key not in hit:
                result.missed.append(i)
    return result
