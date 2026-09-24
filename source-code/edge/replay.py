"""Deterministic tick-based replay of recorded telemetry through an EdgeRuntime.

Same decision-tick semantics the dataset loader used to build training rows
(every event of an interval boundary is ingested, then that tick is
evaluated), so runtime output can be compared row-for-row with the offline
evaluation (P04.09 parity).
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterable, Iterator
from datetime import timedelta
from pathlib import Path

from edge.runtime import EdgeRuntime, Inference
from edge.timeutil import parse_ts

INGEST_LAG_S = 0.5


def read_jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def replay_events(runtime: EdgeRuntime, events: Iterable[dict]) -> list[Inference]:
    """Feed time-ordered events tick by tick; return every inference made."""
    inferences: list[Inference] = []
    for _, group in itertools.groupby(events, key=lambda e: e["observation_time"]):
        tick = list(group)
        for event in tick:
            runtime.ingest(event, parse_ts(event["ingest_time"]) + timedelta(seconds=INGEST_LAG_S))
        tick_time = parse_ts(tick[0]["observation_time"])
        due = tick_time + timedelta(seconds=runtime.config.eval_delay_s + 0.1)
        inferences.extend(runtime.evaluate_due(due))
    return inferences
