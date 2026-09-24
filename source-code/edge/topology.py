"""Corridor topology for loop detectors, derived from the device registry.

An edge site covers a corridor (ADR-0006), so it legitimately sees the loop
on the road segment upstream and downstream of each detector. Neighbors are
derived purely from device `lane_id`s (`<from-node>_<to-node>_<lane-index>`)
- no separate topology file that could drift from the registry.
"""

from __future__ import annotations

from dataclasses import dataclass

from edge.validation import DeviceRegistry


@dataclass(frozen=True)
class Neighbors:
    upstream: str | None
    downstream: str | None


def _edge_nodes(lane_id: str) -> tuple[str, str] | None:
    parts = lane_id.rsplit("_", 1)[0].split("_")
    return (parts[0], parts[1]) if len(parts) == 2 else None


def loop_topology(registry: DeviceRegistry) -> dict[str, Neighbors]:
    by_edge: dict[tuple[str, str], str] = {}
    for device in registry.all():
        if device["device_type"] != "inductive_loop":
            continue
        nodes = _edge_nodes(device["location"].get("lane_id", ""))
        if nodes:
            by_edge[nodes] = device["device_id"]

    topology: dict[str, Neighbors] = {}
    for (src, dst), device_id in sorted(by_edge.items()):
        downstream = sorted(d for (s, t), d in by_edge.items() if s == dst and t != src)
        upstream = sorted(d for (s, t), d in by_edge.items() if t == src and s != dst)
        topology[device_id] = Neighbors(
            upstream=upstream[0] if upstream else None,
            downstream=downstream[0] if downstream else None,
        )
    return topology
