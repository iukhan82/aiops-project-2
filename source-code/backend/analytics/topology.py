"""Corridor/segment topology from the real P03.01 SUMO network file.

Pure standard library on purpose: the SUMO container's Python (which builds
datasets) and the host venv (which serves KPIs) both import this module, so
there is exactly one definition of "which edges make up corridor-a eastbound".

P05.06 documented `segment` as unsupported because P05.04 had no segment
topology; this module (plus migration 0012's `network_segments`) is that
topology. A segment is one SUMO edge between two adjacent intersections.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path

# P03.01 corridor edges carry four lanes: 0 sidewalk, 1 bicycle, 2-3 general
# traffic (the two lanes P04's blockages stop, and the pair a loop instruments
# one of). Vehicle KPIs scale the instrumented lane by this count.
GENERAL_LANES = 2


@dataclass(frozen=True)
class Segment:
    edge_id: str
    from_node: str
    to_node: str
    corridor_id: str | None  # None for cross streets
    direction: str  # east | west | cross
    order: int | None  # position along the corridor direction, 1-based
    length_m: float
    free_flow_speed_m_s: float
    # Share of the segment's traffic that crosses the instrumented lane. Default
    # assumes an even split over the general lanes; calibration.py replaces it
    # with a per-segment value estimated on the TRAIN split only.
    lane_share: float = 1.0 / GENERAL_LANES

    @property
    def is_corridor(self) -> bool:
        return self.corridor_id is not None


def edge_of_lane(lane_id: str) -> str:
    return lane_id.rsplit("_", 1)[0]


def _segment_from_dict(d: dict) -> Segment:
    lane = d["lanes"][0]
    return Segment(
        edge_id=d["edge_id"],
        from_node=d["from_node"],
        to_node=d["to_node"],
        corridor_id=d["corridor_id"],
        direction=d["direction"],
        order=d["order"],
        length_m=float(lane["length_m"]),
        free_flow_speed_m_s=float(lane["speed_m_s"]),
    )


def parse_net_segments(net_xml: Path) -> list[Segment]:
    root = ET.parse(net_xml).getroot()
    out = []
    for edge in root.findall("edge"):
        edge_id = edge.get("id")
        if edge_id.startswith(":") or edge.get("function") == "internal":
            continue
        f, t = edge.get("from"), edge.get("to")
        lane = edge.find("lane")
        same = f[4] == t[4]
        east = f[5:] < t[5:]
        out.append(
            Segment(
                edge_id=edge_id,
                from_node=f,
                to_node=t,
                corridor_id=f"corridor-{f[4]}" if same else None,
                direction=("east" if east else "west") if same else "cross",
                order=((int(f[5:]) if east else 5 - int(f[5:])) if same else None),
                length_m=float(lane.get("length")),
                free_flow_speed_m_s=float(lane.get("speed")),
            )
        )
    return sorted(out, key=lambda s: s.edge_id)


def load_segments_json(path: Path) -> list[Segment]:
    return sorted(
        (_segment_from_dict(d) for d in json.loads(path.read_text(encoding="utf-8"))),
        key=lambda s: s.edge_id,
    )


def apply_lane_share(segments: list[Segment], shares: dict[str, float]) -> list[Segment]:
    return [
        replace(s, lane_share=shares[s.edge_id]) if s.edge_id in shares else s for s in segments
    ]


def corridor_directions(segments: list[Segment]) -> dict[tuple[str, str], list[Segment]]:
    """(corridor_id, direction) -> segments ordered along the direction of travel."""
    groups: dict[tuple[str, str], list[Segment]] = {}
    for seg in segments:
        if seg.is_corridor:
            groups.setdefault((seg.corridor_id, seg.direction), []).append(seg)
    return {k: sorted(v, key=lambda s: s.order) for k, v in sorted(groups.items())}


def controlled_intersections(net_xml: Path) -> set[str]:
    """Junction ids the real network's own tlLogic program actually controls (P03.01) - the
    intersections pre-emption/signal actions may legally target."""
    root = ET.parse(net_xml).getroot()
    return {tl.get("id") for tl in root.findall("tlLogic")}
