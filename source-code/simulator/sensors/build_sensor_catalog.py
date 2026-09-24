"""P03.03: deterministic device catalog for traffic, VRU, signal, weather and
road sensors placed on the P03.01 district network.

Pure function of the built network file (source-code/simulator/network/output/
district.net.xml) and the hand-authored node file (network/plain/district.nod.xml):
no randomness, no SUMO invocation. Given the same network files, produces a
byte-identical device/v1 catalog every time.

Placement scope (documented limitation, see README.md):
- Traffic loop detectors and cycle counters sit on the 18 corridor edges only
  (general lane and dedicated bicycle lane respectively); the 8 cross-street
  edges carry no sensor in this initial catalog.
- Crossing detectors cover all 16 netconvert-generated pedestrian crossings.
- One signal controller per intersection (12).
- One weather station and one road-condition sensor per corridor (3 + 3), each
  colocated at that corridor's second intersection.
"""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

GEOMETRY_VERSION = "2026-09-18.1"
AGENCY_SCOPE = "city-traffic-ops"
REGISTERED_AT = "2026-09-18T00:00:00Z"

# Anchor: SUMO local-plane origin (0, 0), i.e. junction int-a1, projected onto
# the same synthetic-city coordinate used by source-code/contracts/road-geometry
# and observation-envelope examples. Flat-earth equirectangular projection is
# an acceptable approximation for a ~900m x 800m synthetic district; it is not
# a geodetic claim about any real place.
ORIGIN_LAT = 31.5204
ORIGIN_LON = 74.3587
_EARTH_RADIUS_M = 6378137.0


def project(x_m: float, y_m: float) -> tuple[float, float]:
    lat = ORIGIN_LAT + (y_m / _EARTH_RADIUS_M) * (180.0 / math.pi)
    lon = ORIGIN_LON + (x_m / (_EARTH_RADIUS_M * math.cos(math.radians(ORIGIN_LAT)))) * (
        180.0 / math.pi
    )
    return round(lat, 6), round(lon, 6)


def slug(raw_id: str) -> str:
    return raw_id.replace(":", "").replace("_", "-").strip("-")


def corridor_of(node_or_edge_id: str) -> str | None:
    """int-a3 / int-a1_int-a2 -> corridor-a; cross-corridor ids -> None."""
    parts = [p for p in node_or_edge_id.replace(":", "").split("_") if p.startswith("int-")]
    letters = {p[4] for p in parts if len(p) > 4}
    if len(letters) == 1:
        return f"corridor-{letters.pop()}"
    return None


def _shape_points(shape: str) -> list[tuple[float, float]]:
    points = []
    for pair in shape.split():
        x_str, y_str = pair.split(",")
        points.append((float(x_str), float(y_str)))
    return points


def load_junction_coordinates(nod_file: Path) -> dict[str, tuple[float, float]]:
    root = ET.parse(nod_file).getroot()
    return {
        node.get("id"): (float(node.get("x")), float(node.get("y")))
        for node in root.findall("node")
    }


def load_network(net_file: Path):
    return ET.parse(net_file).getroot()


def _device(
    device_id: str,
    device_type: str,
    capabilities: list[str],
    x_m: float,
    y_m: float,
    intersection_id: str | None = None,
    corridor_id: str | None = None,
    lane_id: str | None = None,
) -> dict[str, object]:
    latitude, longitude = project(x_m, y_m)
    location: dict[str, object] = {
        "geometry_version": GEOMETRY_VERSION,
        "coordinate_reference": "EPSG:4326",
        "latitude": latitude,
        "longitude": longitude,
    }
    if intersection_id:
        location["intersection_id"] = intersection_id
    if corridor_id:
        location["corridor_id"] = corridor_id
    if lane_id:
        location["lane_id"] = lane_id
    return {
        "schema_version": "1.0.0",
        "device_id": device_id,
        "device_type": device_type,
        "deployment_type": "simulated",
        "agency_scope": AGENCY_SCOPE,
        "location": location,
        "capabilities": capabilities,
        "status": "active",
        "registered_at": REGISTERED_AT,
        "privacy_classification": "none",
        "retention_class": "standard",
    }


def build_catalog(net_file: Path, nod_file: Path) -> dict[str, object]:
    root = load_network(net_file)
    junctions = load_junction_coordinates(nod_file)

    loop_devices: list[dict[str, object]] = []
    cycle_devices: list[dict[str, object]] = []
    crossing_devices: list[dict[str, object]] = []
    signal_devices: list[dict[str, object]] = []
    weather_devices: list[dict[str, object]] = []
    road_devices: list[dict[str, object]] = []

    edge_types: dict[str, str] = {}
    for edge in root.findall("edge"):
        edge_id = edge.get("id")
        edge_type = edge.get("type")
        if edge_type:
            edge_types[edge_id] = edge_type

    for edge in sorted(root.findall("edge"), key=lambda e: e.get("id") or ""):
        edge_id = edge.get("id")
        if edge.get("type") != "corridor":
            continue
        corridor_id = corridor_of(edge_id)
        general_lane = None
        bicycle_lane = None
        for lane in edge.findall("lane"):
            allow = lane.get("allow")
            disallow = lane.get("disallow") or ""
            if allow == "bicycle":
                bicycle_lane = lane
            elif "pedestrian" in disallow and "bicycle" in disallow:
                general_lane = general_lane or lane

        if general_lane is not None:
            x0, y0 = _shape_points(general_lane.get("shape"))[0]
            loop_devices.append(
                _device(
                    f"loop-{slug(edge_id)}",
                    "inductive_loop",
                    ["vehicle_count", "occupancy", "mean_speed"],
                    x0,
                    y0,
                    corridor_id=corridor_id,
                    lane_id=general_lane.get("id"),
                )
            )
        if bicycle_lane is not None:
            x0, y0 = _shape_points(bicycle_lane.get("shape"))[0]
            cycle_devices.append(
                _device(
                    f"cyc-{slug(edge_id)}",
                    "cycle_counter",
                    ["cyclist_count", "mean_speed"],
                    x0,
                    y0,
                    corridor_id=corridor_id,
                    lane_id=bicycle_lane.get("id"),
                )
            )

    for edge in sorted(root.findall("edge"), key=lambda e: e.get("id") or ""):
        edge_id = edge.get("id")
        if edge.get("function") != "crossing":
            continue
        junction_id = edge_id[1:].split("_c")[0]
        crossing_edges = (edge.get("crossingEdges") or "").split()
        corridor_id = None
        if crossing_edges:
            crossed_type = edge_types.get(crossing_edges[0])
            if crossed_type == "corridor":
                corridor_id = corridor_of(crossing_edges[0])
        lane = edge.find("lane")
        points = _shape_points(lane.get("shape"))
        mid_x = sum(p[0] for p in points) / len(points)
        mid_y = sum(p[1] for p in points) / len(points)
        crossing_devices.append(
            _device(
                f"crossing-{slug(edge_id)}",
                "crossing_detector",
                ["crossing_demand", "crossing_clearance"],
                mid_x,
                mid_y,
                intersection_id=junction_id,
                corridor_id=corridor_id,
            )
        )

    tls_ids = sorted({tl.get("id") for tl in root.findall("tlLogic")})
    for junction_id in tls_ids:
        x, y = junctions[junction_id]
        signal_devices.append(
            _device(
                f"signal-{slug(junction_id)}",
                "signal_controller",
                ["active_phase", "signal_state"],
                x,
                y,
                intersection_id=junction_id,
                corridor_id=corridor_of(junction_id),
            )
        )

    for letter in ("a", "b", "c"):
        station_junction = f"int-{letter}2"
        x, y = junctions[station_junction]
        corridor_id = f"corridor-{letter}"
        weather_devices.append(
            _device(
                f"weather-station-corridor-{letter}",
                "weather_station",
                [
                    "air_temperature",
                    "relative_humidity",
                    "precipitation_intensity",
                    "wind_speed",
                ],
                x,
                y,
                intersection_id=station_junction,
                corridor_id=corridor_id,
            )
        )
        road_devices.append(
            _device(
                f"road-condition-corridor-{letter}",
                "road_condition_sensor",
                ["road_surface_temperature", "surface_state", "friction_estimate"],
                x,
                y,
                intersection_id=station_junction,
                corridor_id=corridor_id,
            )
        )

    all_devices = (
        loop_devices
        + cycle_devices
        + crossing_devices
        + signal_devices
        + weather_devices
        + road_devices
    )
    all_devices.sort(key=lambda d: d["device_id"])

    return {
        "devices": all_devices,
        "counts": {
            "inductive_loop": len(loop_devices),
            "cycle_counter": len(cycle_devices),
            "crossing_detector": len(crossing_devices),
            "signal_controller": len(signal_devices),
            "weather_station": len(weather_devices),
            "road_condition_sensor": len(road_devices),
        },
    }


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    network_dir = here.parent / "network"
    result = build_catalog(
        network_dir / "output" / "district.net.xml", network_dir / "plain" / "district.nod.xml"
    )
    print(json.dumps(result["counts"], indent=2, sort_keys=True))
