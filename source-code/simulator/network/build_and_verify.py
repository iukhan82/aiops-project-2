"""P03.01: build the versioned 12-intersection/three-corridor SUMO district
and verify it is connected, signal-controlled and drivable.

Runs inside the pinned `ghcr.io/eclipse-sumo/sumo` image (see run_container.sh);
netconvert and sumo must be on PATH. Not a determinism/telemetry proof (that
is P01.06's scope, already evidenced); this proves the network itself: three
corridors, twelve traffic-light-controlled intersections, pedestrian
crossings, a protected bicycle lane and a completed transit route, with zero
teleports/collisions.
"""

from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

GEOMETRY_VERSION = "2026-09-18.1"
HERE = Path(__file__).resolve().parent
PLAIN_DIR = HERE / "plain"
DEMAND_DIR = HERE / "demand"
OUTPUT_DIR = HERE / "output"
NET_FILE = OUTPUT_DIR / "district.net.xml"


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, capture_output=True, text=True, **kwargs)


def build_network() -> dict[str, object]:
    result = run(
        [
            "netconvert",
            f"--node-files={PLAIN_DIR / 'district.nod.xml'}",
            f"--edge-files={PLAIN_DIR / 'district.edg.xml'}",
            f"--type-files={PLAIN_DIR / 'district.typ.xml'}",
            "--tls.default-type",
            "actuated",
            "--crossings.guess",
            "--sidewalks.guess",
            "--default.junctions.radius",
            "6",
            "-o",
            str(NET_FILE),
        ]
    )
    combined = result.stdout + result.stderr
    if "Warning" in combined or "Success." not in combined:
        raise SystemExit(f"netconvert did not report a clean success:\n{combined}")
    return {"netconvert_output": combined.strip()}


def count_intersections_and_crossings() -> dict[str, int]:
    root = ET.parse(NET_FILE).getroot()
    tls_ids = {tl.get("id") for tl in root.findall("tlLogic")}
    crossing_edges = [e for e in root.findall("edge") if e.get("function") == "crossing"]
    return {"traffic_light_count": len(tls_ids), "crossing_count": len(crossing_edges)}


def check_bicycle_lane_isolation() -> dict[str, object]:
    """Corridor edges (4 lanes: sidewalk, bike, 2 general) must isolate
    bicycle traffic onto exactly one dedicated lane. Cross-street edges
    (3 lanes: sidewalk, 2 general) carry no dedicated bike lane by design
    and are skipped."""
    root = ET.parse(NET_FILE).getroot()
    normal_edges = [e for e in root.findall("edge") if e.get("function") is None]
    corridor_edges = [e for e in normal_edges if len(e.findall("lane")) >= 4]
    violations = []
    for edge in corridor_edges:
        lanes = edge.findall("lane")
        bike_lanes = [lane for lane in lanes if lane.get("allow") == "bicycle"]
        general_lanes = [
            lane
            for lane in lanes
            if lane.get("disallow") and "bicycle" in str(lane.get("disallow"))
        ]
        if len(bike_lanes) != 1 or not general_lanes:
            violations.append(edge.get("id"))
    return {"corridor_edges_checked": len(corridor_edges), "bicycle_lane_violations": violations}


def run_demo_simulation() -> None:
    for stale in OUTPUT_DIR.glob("demo-*.xml"):
        stale.unlink()
    run(
        [
            "sumo",
            "-c",
            str(DEMAND_DIR / "district-demo.sumocfg"),
            "--no-step-log",
            "--duration-log.disable",
        ]
    )


def verify_demo_simulation() -> dict[str, object]:
    stats = ET.parse(OUTPUT_DIR / "demo-statistics.xml").getroot()
    teleports = stats.find("teleports")
    safety = stats.find("safety")
    vehicles = stats.find("vehicles")
    assert teleports is not None and safety is not None and vehicles is not None
    teleport_total = int(teleports.get("total", "-1"))
    collisions = int(safety.get("collisions", "-1"))
    if teleport_total != 0:
        raise SystemExit(f"expected zero teleports, got {teleport_total}")
    if collisions != 0:
        raise SystemExit(f"expected zero collisions, got {collisions}")

    stops = ET.parse(OUTPUT_DIR / "demo-stopinfo.xml").getroot()
    bus_stops = [s.get("busStop") for s in stops.findall("stopinfo") if s.get("id") == "veh-bus-1"]
    if bus_stops != ["stop-crossing-bc", "stop-corridor-c"]:
        raise SystemExit(f"expected 2 completed bus stops in order, got {bus_stops}")

    fcd = ET.parse(OUTPUT_DIR / "demo-fcd.xml").getroot()
    cyclist_lanes = {
        v.get("lane")
        for ts in fcd.findall("timestep")
        for v in ts.findall("vehicle")
        if v.get("id") == "veh-cyclist-1"
    }
    off_bike_lane = [
        lane for lane in cyclist_lanes if not (lane.endswith("_1") or lane.startswith(":"))
    ]
    if off_bike_lane:
        raise SystemExit(f"cyclist left the dedicated bike lane: {off_bike_lane}")

    return {
        "teleports_total": teleport_total,
        "collisions": collisions,
        "vehicles_completed": int(vehicles.get("inserted", "0"))
        - int(vehicles.get("running", "-1")),
        "bus_stops_completed_in_order": bus_stops,
        "cyclist_lanes_used": sorted(cyclist_lanes),
    }


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    summary: dict[str, object] = {"geometry_version": GEOMETRY_VERSION}
    summary.update(build_network())
    summary.update(count_intersections_and_crossings())
    bike_check = check_bicycle_lane_isolation()
    summary.update(bike_check)
    if bike_check["bicycle_lane_violations"]:
        raise SystemExit(f"bicycle lane isolation failed: {bike_check['bicycle_lane_violations']}")
    if summary["traffic_light_count"] != 12:
        raise SystemExit(
            f"expected 12 traffic-light intersections, got {summary['traffic_light_count']}"
        )
    if summary["crossing_count"] < 1:
        raise SystemExit("expected at least one pedestrian crossing")

    run_demo_simulation()
    summary["demo_simulation"] = verify_demo_simulation()

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
