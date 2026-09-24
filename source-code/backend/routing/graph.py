"""P07.02: the directed road graph routes are computed on.

One `Segment` (backend/analytics/topology.py) is already one directed edge of
the real P03.01 SUMO network - opposite travel directions are separate edge
ids with separate geometry, so a plain directed graph over segments is the
correct representation; no synthetic "both ways" edge is invented.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from backend.analytics.topology import Segment


@dataclass(frozen=True)
class EdgeState:
    """What is known about a segment right now, independent of any one vehicle's route."""

    closed: bool = False
    closed_reason: str | None = None  # "<kind> on <element>"
    hazard: bool = False
    hazard_reason: str | None = None
    travel_time_s: float | None = None  # from live corridor KPIs, when available
    buffer_index: float | None = (
        None  # planning-time reliability margin, when available (P06.01 add_reliability)
    )


class RoadGraph:
    def __init__(self, segments: list[Segment]) -> None:
        self.segments = {s.edge_id: s for s in segments}
        self.out: dict[str, list[Segment]] = defaultdict(list)
        for s in segments:
            self.out[s.from_node].append(s)

    def neighbors(self, node: str) -> list[Segment]:
        return self.out.get(node, [])
