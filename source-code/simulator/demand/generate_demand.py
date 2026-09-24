"""P03.02: deterministic demand and clock generator for the P03.01 district.

Given a seed and run_id, deterministically generates the full road-user
population (vehicles, cyclists, pedestrians, transit) and a simulation-time
to UTC clock mapping. Pure Python: no SUMO invocation, no randomTrips.py
dependency. Uses only the corridor/cross-street edge IDs authored in
source-code/simulator/network/plain/district.edg.xml, so it has no runtime
dependency on the built network file either.

Determinism contract: generate(seed, run_id, ...) called twice with the same
arguments must produce byte-identical output files. All randomness comes
from a single random.Random(seed) instance consumed in a fixed order;
run_id only affects entity ID prefixes and the manifest, never the random
sequence.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Route templates: (route_id, mode, edge_id_tuple). Edge IDs match
# source-code/simulator/network/plain/district.edg.xml exactly.
VEHICLE_ROUTES: list[tuple[str, tuple[str, ...]]] = [
    ("route-a-east", ("int-a1_int-a2", "int-a2_int-a3", "int-a3_int-a4")),
    ("route-a-west", ("int-a4_int-a3", "int-a3_int-a2", "int-a2_int-a1")),
    ("route-b-east", ("int-b1_int-b2", "int-b2_int-b3", "int-b3_int-b4")),
    ("route-b-west", ("int-b4_int-b3", "int-b3_int-b2", "int-b2_int-b1")),
    ("route-c-east", ("int-c1_int-c2", "int-c2_int-c3", "int-c3_int-c4")),
    ("route-c-west", ("int-c4_int-c3", "int-c3_int-c2", "int-c2_int-c1")),
    (
        "route-a-to-b-west-cross",
        (
            "int-a4_int-a3",
            "int-a3_int-a2",
            "int-a2_int-a1",
            "int-a1_int-b1",
            "int-b1_int-b2",
            "int-b2_int-b3",
            "int-b3_int-b4",
        ),
    ),
    (
        "route-a-to-b-east-cross",
        (
            "int-a1_int-a2",
            "int-a2_int-a3",
            "int-a3_int-a4",
            "int-a4_int-b4",
            "int-b4_int-b3",
            "int-b3_int-b2",
            "int-b2_int-b1",
        ),
    ),
    (
        "route-c-to-b-east-cross",
        (
            "int-c1_int-c2",
            "int-c2_int-c3",
            "int-c3_int-c4",
            "int-c4_int-b4",
            "int-b4_int-b3",
            "int-b3_int-b2",
            "int-b2_int-b1",
        ),
    ),
    (
        "route-b-to-c-east-cross",
        (
            "int-b1_int-b2",
            "int-b2_int-b3",
            "int-b3_int-b4",
            "int-b4_int-c4",
            "int-c4_int-c3",
            "int-c3_int-c2",
            "int-c2_int-c1",
        ),
    ),
]

# Cyclists are confined to the 6 single-corridor routes: cross-street edges
# carry no dedicated bike lane, so netconvert generates no bicycle-legal
# connection from a corridor's bike lane onto a cross-street at any junction.
CYCLIST_ROUTES: list[tuple[str, tuple[str, ...]]] = VEHICLE_ROUTES[:6]

# Single-edge pedestrian sidewalk crossings; every corridor and cross-street
# edge carries a sidewalk (source-code/simulator/network/plain/district.typ.xml).
PEDESTRIAN_WALKS: list[tuple[str, ...]] = [(edges[0],) for _, edges in VEHICLE_ROUTES[:6]] + [
    ("int-a1_int-b1",),
    ("int-b1_int-c1",),
    ("int-a4_int-b4",),
    ("int-b4_int-c4",),
]

TRANSIT_LINES: list[dict[str, object]] = [
    {
        "route_id": "route-a-to-b-east-cross",
        "line_id": "line-1",
        "stops": [
            {"busStop": "stop-line1-a", "lane": "int-a3_int-a4_2", "duration": 20},
            {"busStop": "stop-line1-b", "lane": "int-b4_int-b3_2", "duration": 20},
        ],
        "departures": [0, 300],
    },
    {
        "route_id": "route-b-to-c-east-cross",
        "line_id": "line-2",
        "stops": [
            {"busStop": "stop-line2-b", "lane": "int-b3_int-b4_2", "duration": 20},
            {"busStop": "stop-line2-c", "lane": "int-c4_int-c3_2", "duration": 20},
        ],
        "departures": [0, 300],
    },
]

VEHICLE_COUNT = 40
CYCLIST_COUNT = 12
PEDESTRIAN_COUNT = 16


@dataclass(frozen=True)
class DemandEntity:
    kind: str  # vehicle | cyclist | pedestrian | transit
    entity_id: str
    depart: int
    route_id: str | None
    walk_edges: tuple[str, ...] | None
    stops: list[dict[str, object]] | None


def generate_entities(seed: int, run_id: str, sim_end: int) -> list[DemandEntity]:
    rng = random.Random(seed)
    entities: list[DemandEntity] = []

    for i in range(VEHICLE_COUNT):
        route_id, _ = rng.choice(VEHICLE_ROUTES)
        depart = rng.randint(0, sim_end - 1)
        entities.append(
            DemandEntity("vehicle", f"{run_id}-veh-{i:04d}", depart, route_id, None, None)
        )

    for i in range(CYCLIST_COUNT):
        route_id, _ = rng.choice(CYCLIST_ROUTES)
        depart = rng.randint(0, sim_end - 1)
        entities.append(
            DemandEntity("cyclist", f"{run_id}-cyc-{i:04d}", depart, route_id, None, None)
        )

    for i in range(PEDESTRIAN_COUNT):
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


def write_route_file(entities: list[DemandEntity], path: Path) -> None:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<routes>"]
    for route_id, edges in VEHICLE_ROUTES:
        lines.append(f'    <route id="{route_id}" edges="{" ".join(edges)}"/>')

    for entity in entities:
        if entity.kind == "vehicle":
            lines.append(
                f'    <vehicle id="{entity.entity_id}" type="passenger" '
                f'route="{entity.route_id}" depart="{entity.depart}"/>'
            )
        elif entity.kind == "cyclist":
            lines.append(
                f'    <vehicle id="{entity.entity_id}" type="cyclist" '
                f'route="{entity.route_id}" depart="{entity.depart}"/>'
            )
        elif entity.kind == "pedestrian":
            edges = " ".join(entity.walk_edges or ())
            lines.append(f'    <person id="{entity.entity_id}" depart="{entity.depart}">')
            lines.append(f'        <walk edges="{edges}"/>')
            lines.append("    </person>")
        elif entity.kind == "transit":
            lines.append(
                f'    <vehicle id="{entity.entity_id}" type="transit-bus" '
                f'route="{entity.route_id}" depart="{entity.depart}">'
            )
            for stop in entity.stops or []:
                lines.append(
                    f'        <stop busStop="{stop["busStop"]}" duration="{stop["duration"]}"/>'
                )
            lines.append("    </vehicle>")

    lines.append("</routes>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def write_manifest(
    entities: list[DemandEntity], seed: int, run_id: str, anchor_utc: str, sim_end: int, path: Path
) -> dict[str, object]:
    anchor = datetime.fromisoformat(anchor_utc.replace("Z", "+00:00"))
    counts = {
        kind: sum(1 for e in entities if e.kind == kind)
        for kind in ("vehicle", "cyclist", "pedestrian", "transit")
    }
    first = entities[0]
    last = entities[-1]
    manifest = {
        "seed": seed,
        "run_id": run_id,
        "anchor_utc": anchor_utc,
        "sim_end_seconds": sim_end,
        "entity_counts": counts,
        "total_entities": len(entities),
        "clock_samples": [
            {
                "entity_id": e.entity_id,
                "depart_s": e.depart,
                "observation_time": (anchor + timedelta(seconds=e.depart))
                .astimezone(timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            for e in (first, last)
        ],
    }
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def generate(
    seed: int, run_id: str, anchor_utc: str, sim_end: int, output_dir: Path
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    entities = generate_entities(seed, run_id, sim_end)
    write_route_file(entities, output_dir / f"{run_id}.rou.xml")
    return write_manifest(
        entities, seed, run_id, anchor_utc, sim_end, output_dir / f"{run_id}.manifest.json"
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260918)
    parser.add_argument("--run-id", default="p03-02-demo")
    parser.add_argument("--anchor-utc", default="2026-09-18T09:00:00Z")
    parser.add_argument("--sim-end", type=int, default=600)
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).resolve().parent / "output"
    )
    args = parser.parse_args()

    result = generate(args.seed, args.run_id, args.anchor_utc, args.sim_end, args.output_dir)
    print(json.dumps(result, indent=2))
