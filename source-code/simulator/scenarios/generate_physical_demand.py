"""P03.05: demand shaping for the four physically-simulated scenarios
(normal, peak, event, stall). Reuses P03.02's route/edge tables
(`source-code/simulator/demand/generate_demand.py`) rather than duplicating
network topology, but each scenario needs a demand shape P03.02's fixed
module-level counts don't provide, so this module writes its own entities
and route file instead of calling P03.02's `generate()` directly (P03.02 is
DONE/frozen; this does not modify it).

- normal: identical demand shape to P03.02's baseline (40/12/16/4).
- peak: all road-user counts doubled.
- event: baseline demand plus a localized pedestrian surge on one edge in a
  short window (a documented stand-in for "a concert/sports event just let
  out"), not a calibrated real event-demand model.
- stall: baseline demand with one deterministically chosen vehicle given a
  scheduled 120s stop mid-route, blocking a general lane.
"""

from __future__ import annotations

import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "demand"))
from generate_demand import (  # noqa: E402
    CYCLIST_ROUTES,
    PEDESTRIAN_WALKS,
    TRANSIT_LINES,
    VEHICLE_ROUTES,
    DemandEntity,
    write_route_file,
)

BASE_VEHICLE_COUNT = 40
BASE_CYCLIST_COUNT = 12
BASE_PEDESTRIAN_COUNT = 16


def generate_scaled_entities(
    seed: int, run_id: str, sim_end: int, scale: float
) -> list[DemandEntity]:
    """Same generation order/style as P03.02's generate_entities, with counts scaled."""
    rng = random.Random(seed)
    entities: list[DemandEntity] = []

    vehicle_count = round(BASE_VEHICLE_COUNT * scale)
    cyclist_count = round(BASE_CYCLIST_COUNT * scale)
    pedestrian_count = round(BASE_PEDESTRIAN_COUNT * scale)

    for i in range(vehicle_count):
        route_id, _ = rng.choice(VEHICLE_ROUTES)
        depart = rng.randint(0, sim_end - 1)
        entities.append(
            DemandEntity("vehicle", f"{run_id}-veh-{i:04d}", depart, route_id, None, None)
        )

    for i in range(cyclist_count):
        route_id, _ = rng.choice(CYCLIST_ROUTES)
        depart = rng.randint(0, sim_end - 1)
        entities.append(
            DemandEntity("cyclist", f"{run_id}-cyc-{i:04d}", depart, route_id, None, None)
        )

    for i in range(pedestrian_count):
        walk = rng.choice(PEDESTRIAN_WALKS)
        depart = rng.randint(0, sim_end - 1)
        entities.append(
            DemandEntity("pedestrian", f"{run_id}-ped-{i:04d}", depart, None, walk, None)
        )

    transit_index = 0
    for line in TRANSIT_LINES:
        for departure in line["departures"]:  # type: ignore[union-attr]
            entities.append(
                DemandEntity(
                    "transit",
                    f"{run_id}-{line['line_id']}-{transit_index:02d}",
                    int(departure),  # type: ignore[arg-type]
                    str(line["route_id"]),
                    None,
                    list(line["stops"]),  # type: ignore[arg-type]
                )
            )
            transit_index += 1

    entities.sort(key=lambda e: (e.depart, e.entity_id))
    return entities


EVENT_VENUE_EDGE = "int-b2_int-b3"
EVENT_SURGE_COUNT = 20
EVENT_WINDOW_S = (200, 260)


def add_event_surge(entities: list[DemandEntity], seed: int, run_id: str) -> list[DemandEntity]:
    rng = random.Random(f"{seed}:{run_id}:event-surge")
    surge = [
        DemandEntity(
            "pedestrian",
            f"{run_id}-event-ped-{i:04d}",
            rng.randint(*EVENT_WINDOW_S),
            None,
            (EVENT_VENUE_EDGE,),
            None,
        )
        for i in range(EVENT_SURGE_COUNT)
    ]
    merged = entities + surge
    merged.sort(key=lambda e: (e.depart, e.entity_id))
    return merged


@dataclass(frozen=True)
class StallPlan:
    vehicle_id: str
    edge_id: str
    lane_id: str
    start_pos: int
    end_pos: int
    duration_s: int


def choose_stall_vehicle(entities: list[DemandEntity]) -> StallPlan:
    """Deterministically pick the 5th-departing vehicle (by sorted order) and
    a mid-route corridor edge for it to block."""
    vehicles = [e for e in entities if e.kind == "vehicle"]
    chosen = vehicles[4]
    route_edges = dict(VEHICLE_ROUTES)[chosen.route_id]
    block_edge = route_edges[len(route_edges) // 2]
    return StallPlan(
        vehicle_id=chosen.entity_id,
        edge_id=block_edge,
        lane_id=f"{block_edge}_2",
        start_pos=50,
        end_pos=60,
        duration_s=120,
    )


def inject_stops(path: Path, plans: list[StallPlan]) -> None:
    """Rewrite each planned vehicle's self-closing `<vehicle .../>` line in an
    already-written route file into an open/close pair with a `<stop>` child
    - a deliberate, narrowly-scoped text edit against the exact fixed format
    `write_route_file` emits, not a general XML mutation.
    """
    text = path.read_text(encoding="utf-8")
    for stall in plans:
        pattern = re.compile(rf'(    <vehicle id="{re.escape(stall.vehicle_id)}"[^/]*)/>')
        replacement = (
            f'\\1>\n        <stop lane="{stall.lane_id}" startPos="{stall.start_pos}" '
            f'endPos="{stall.end_pos}" duration="{stall.duration_s}"/>\n    </vehicle>'
        )
        text, count = pattern.subn(replacement, text)
        if count != 1:
            raise SystemExit(
                f"expected exactly one stall vehicle line for {stall.vehicle_id}, found {count}"
            )
    path.write_text(text, encoding="utf-8", newline="\n")


def write_route_file_with_stall(entities: list[DemandEntity], stall: StallPlan, path: Path) -> None:
    """Writes the route file via P03.02's writer, then injects the stall stop."""
    write_route_file(entities, path)
    inject_stops(path, [stall])
