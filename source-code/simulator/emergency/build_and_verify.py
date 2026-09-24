"""P03.04: build and verify simulated emergency units and CAD/AVL events.

Runs inside the pinned ghcr.io/eclipse-sumo/sumo image (see
source-code/simulator/network/run_container.sh for the exact digest);
requires source-code/simulator/network/output/district.net.xml to already
exist (P03.01). Self-contained: does not depend on P03.02/P03.03 output
artifacts.

Each emergency unit's route is a real SUMO trip (from-edge/to-edge, routed by
SUMO's own router at insertion, not fabricated) so travel time, route
distance and arrival are measured simulation outcomes, not invented numbers.

Proves:
- Calls, assignments, devices and AVL position events are byte-identical
  across two independent runs of the same seed/run_id.
- Every call/assignment/device record validates against its contract
  (checked separately on the host venv; see
  source-code/tests/test_emergency_contracts.py, same reasoning as P03.03's
  split for jsonschema availability).
- No record anywhere in the output contains a "patient"-named field or value
  (AGENTS.md: never add patient data without separate authorization).
- Zero SUMO teleports/collisions for the emergency-unit trips themselves.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIMULATOR_DIR = HERE.parent
NETWORK_DIR = SIMULATOR_DIR / "network"
NET_FILE = NETWORK_DIR / "output" / "district.net.xml"
OUTPUT_DIR = HERE / "output"

sys.path.insert(0, str(SIMULATOR_DIR / "sensors"))
from build_sensor_catalog import project, slug  # noqa: E402

sys.path.insert(0, str(HERE))
from generate_scenario import (  # noqa: E402
    AGENCIES,
    dispatch_calls,
    generate_calls,
    iso,
)

SEED = 20260918
RUN_ID = "p03-04-emergency"
ANCHOR_UTC = "2026-09-18T09:00:00Z"
SIM_END = 900
GEOMETRY_VERSION = "2026-09-18.1"
CORRELATION_ID = f"run-2026-09-18-seed-{SEED}"
AVL_SAMPLE_EVERY_S = 5


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, capture_output=True, text=True, **kwargs)


def load_edge_index(
    net_file: Path,
) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, tuple[float, float]]]:
    """Return (edges_from_junction, edges_to_junction, edge_start_xy) for real edges only."""
    root = ET.parse(net_file).getroot()
    from_junction: dict[str, list[str]] = {}
    to_junction: dict[str, list[str]] = {}
    start_xy: dict[str, tuple[float, float]] = {}
    for edge in root.findall("edge"):
        if edge.get("function") is not None:
            continue
        edge_id = edge.get("id")
        from_junction.setdefault(edge.get("from"), []).append(edge_id)
        to_junction.setdefault(edge.get("to"), []).append(edge_id)
        lane = edge.find("lane")
        if lane is not None and lane.get("shape"):
            x_str, y_str = lane.get("shape").split()[0].split(",")
            start_xy[edge_id] = (float(x_str), float(y_str))
    for mapping in (from_junction, to_junction):
        for edge_list in mapping.values():
            edge_list.sort()
    return from_junction, to_junction, start_xy


def build_trips_file(
    dispatches: list, depart_edge: dict[str, str], arrival_edge: dict[str, str], path: Path
) -> dict[str, str]:
    """Writes the trips file; returns {assignment_id: sumo_vehicle_id}."""
    lines = [
        "<routes>",
        '    <vType id="emergency" vClass="emergency" length="6.0" maxSpeed="20.0" '
        'guiShape="emergency"/>',
    ]
    vehicle_ids: dict[str, str] = {}
    # SUMO silently drops any trip that arrives out of departure-time order
    # instead of erroring, so the trips file must be depart-sorted even
    # though `dispatches` itself is reported-time-sorted (dispatch_calls'
    # queuing can push a later call's depart time earlier than an unrelated
    # unit's later depart, since each agency is dispatched independently).
    for dispatch in sorted(dispatches, key=lambda d: (d.acknowledged_at_s, d.assignment_id)):
        veh_id = f"trip-{dispatch.assignment_id}"
        vehicle_ids[dispatch.assignment_id] = veh_id
        from_edge = depart_edge[dispatch.unit_id]
        to_edge = arrival_edge[dispatch.call.junction_id]
        lines.append(
            f'    <trip id="{veh_id}" type="emergency" '
            f'depart="{dispatch.acknowledged_at_s:.1f}" from="{from_edge}" to="{to_edge}"/>'
        )
    lines.append("</routes>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return vehicle_ids


def write_sumocfg(
    trips_file: Path, sumocfg_path: Path, tripinfo_out: Path, fcd_out: Path, stats_out: Path
) -> None:
    sumocfg_path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <input>
        <net-file value="{NET_FILE}"/>
        <route-files value="{trips_file}"/>
    </input>
    <time><begin value="0"/><end value="{SIM_END}"/></time>
    <random_number><seed value="{SEED}"/></random_number>
    <output>
        <tripinfo-output value="{tripinfo_out}"/>
        <fcd-output value="{fcd_out}"/>
        <statistic-output value="{stats_out}"/>
    </output>
</configuration>
""",
        encoding="utf-8",
    )


def parse_tripinfo(path: Path) -> dict[str, dict[str, float]]:
    root = ET.parse(path).getroot()
    out: dict[str, dict[str, float]] = {}
    for trip in root.findall("tripinfo"):
        out[trip.get("id")] = {
            "depart": float(trip.get("depart")),
            "arrival": float(trip.get("arrival")),
            "duration": float(trip.get("duration")),
            "route_length_m": float(trip.get("routeLength")),
        }
    return out


def parse_fcd_for_vehicles(path: Path, vehicle_ids: set[str]) -> dict[str, list[dict[str, float]]]:
    root = ET.parse(path).getroot()
    by_vehicle: dict[str, list[dict[str, float]]] = {v: [] for v in vehicle_ids}
    for timestep in root.findall("timestep"):
        time_s = float(timestep.get("time"))
        for vehicle in timestep.findall("vehicle"):
            veh_id = vehicle.get("id")
            if veh_id in vehicle_ids:
                by_vehicle[veh_id].append(
                    {
                        "time_s": time_s,
                        "x": float(vehicle.get("x")),
                        "y": float(vehicle.get("y")),
                        "speed_m_s": float(vehicle.get("speed")),
                        "heading_deg": float(vehicle.get("angle")),
                    }
                )
    return by_vehicle


def _no_patient_fields(obj: object, path: str = "$") -> list[str]:
    violations: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if "patient" in key.lower():
                violations.append(f"{path}.{key}")
            violations.extend(_no_patient_fields(value, f"{path}.{key}"))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            violations.extend(_no_patient_fields(item, f"{path}[{i}]"))
    elif isinstance(obj, str):
        if "patient" in obj.lower():
            violations.append(f"{path} (value)")
    return violations


def build_one_run(run_label: str) -> dict[str, object]:
    run_dir = OUTPUT_DIR / run_label
    run_dir.mkdir(parents=True, exist_ok=True)

    from_junction, to_junction, edge_start_xy = load_edge_index(NET_FILE)

    calls = generate_calls(SEED, RUN_ID, SIM_END)
    dispatches = dispatch_calls(calls, RUN_ID)

    depart_edge = {
        unit_id: from_junction[info["station_junction"]][0]  # type: ignore[index]
        for info in AGENCIES.values()
        for unit_id in info["units"]  # type: ignore[union-attr]
    }
    arrival_edge = {
        junction_id: to_junction[junction_id][0] for junction_id in {c.junction_id for c in calls}
    }

    trips_file = run_dir / "trips.xml"
    vehicle_ids = build_trips_file(dispatches, depart_edge, arrival_edge, trips_file)

    sumocfg = run_dir / "emergency.sumocfg"
    tripinfo_out = run_dir / "tripinfo.xml"
    fcd_out = run_dir / "fcd.xml"
    stats_out = run_dir / "statistics.xml"
    write_sumocfg(trips_file, sumocfg, tripinfo_out, fcd_out, stats_out)
    run(["sumo", "-c", str(sumocfg), "--no-step-log", "--duration-log.disable"], cwd=run_dir)

    tripinfo = parse_tripinfo(tripinfo_out)
    missing = set(vehicle_ids.values()) - set(tripinfo)
    if missing:
        raise SystemExit(
            f"SUMO dropped or never completed these emergency trips: {sorted(missing)}"
        )
    fcd_by_vehicle = parse_fcd_for_vehicles(fcd_out, set(vehicle_ids.values()))

    anchor = datetime.fromisoformat(ANCHOR_UTC.replace("Z", "+00:00")).astimezone(timezone.utc)

    calls_records = []
    for call in calls:
        location_xy = edge_start_xy.get(to_junction[call.junction_id][0])
        latitude, longitude = project(*location_xy) if location_xy else (None, None)
        calls_records.append(
            {
                "schema_version": "1.0.0",
                "call_id": call.call_id,
                "call_type": call.call_type,
                "call_subtype": call.call_subtype,
                "priority": call.priority,
                "location": {
                    "coordinate_reference": "EPSG:4326",
                    "latitude": latitude,
                    "longitude": longitude,
                    "intersection_id": call.junction_id,
                },
                "geometry_version": GEOMETRY_VERSION,
                "reported_at": iso(anchor, call.reported_at_s),
                "source_reliability": call.source_reliability,
                "status": "cleared",
                "truth_label": "simulated",
            }
        )

    assignment_records = []
    device_records = []
    avl_events = []
    registered_units: set[str] = set()
    device_sequence: dict[str, int] = {}

    for dispatch in dispatches:
        veh_id = vehicle_ids[dispatch.assignment_id]
        trip = tripinfo[veh_id]
        eta_seconds = trip["duration"]
        eta_uncertainty = round(eta_seconds * 0.12, 1)
        arrived_at_s = trip["arrival"]
        info = AGENCIES[dispatch.call.call_type]
        cleared_at_s = arrived_at_s + float(info["scene_duration_s"])  # type: ignore[arg-type]

        assignment_records.append(
            {
                "schema_version": "1.0.0",
                "assignment_id": dispatch.assignment_id,
                "call_id": dispatch.call.call_id,
                "unit_id": dispatch.unit_id,
                "agency": dispatch.agency_scope,
                "capability": dispatch.capability,
                "status": "clear",
                "assigned_at": iso(anchor, dispatch.assigned_at_s),
                "acknowledged_at": iso(anchor, dispatch.acknowledged_at_s),
                "arrived_at": iso(anchor, arrived_at_s),
                "cleared_at": iso(anchor, cleared_at_s),
                "route_alternatives": [
                    {
                        "route_id": f"route-{dispatch.assignment_id}-1",
                        "geometry_version": GEOMETRY_VERSION,
                        "distance_m": round(trip["route_length_m"], 2),
                        "eta_seconds": round(eta_seconds, 1),
                        "eta_uncertainty_seconds": eta_uncertainty,
                        "constraints_applied": [],
                        "selected": True,
                    }
                ],
                "truth_label": "simulated",
            }
        )

        device_id = f"avl-{slug(dispatch.unit_id)}"
        if device_id not in registered_units:
            registered_units.add(device_id)
            station_xy = edge_start_xy[depart_edge[dispatch.unit_id]]
            lat, lon = project(*station_xy)
            device_records.append(
                {
                    "schema_version": "1.0.0",
                    "device_id": device_id,
                    "device_type": "emergency_cad_avl_adapter",
                    "deployment_type": "simulated",
                    "agency_scope": dispatch.agency_scope,
                    "location": {
                        "geometry_version": GEOMETRY_VERSION,
                        "coordinate_reference": "EPSG:4326",
                        "latitude": lat,
                        "longitude": lon,
                    },
                    "capabilities": ["position", "speed", "heading", "eta"],
                    "status": "active",
                    "registered_at": "2026-09-18T00:00:00Z",
                    "privacy_classification": "none",
                    "retention_class": "standard",
                }
            )

        for index, sample in enumerate(fcd_by_vehicle.get(veh_id, [])):
            if index % AVL_SAMPLE_EVERY_S != 0:
                continue
            lat, lon = project(sample["x"], sample["y"])
            sequence_number = device_sequence.get(device_id, 0)
            device_sequence[device_id] = sequence_number + 1
            avl_events.append(
                {
                    "schema_version": "1.0.0",
                    "event_id": None,
                    "event_type": "emergency.unit_position.avl",
                    "device_id": device_id,
                    "agency_scope": dispatch.agency_scope,
                    "observation_time": iso(anchor, sample["time_s"]),
                    "sequence_number": sequence_number,
                    "location": {
                        "coordinate_reference": "EPSG:4326",
                        "latitude": lat,
                        "longitude": lon,
                    },
                    "measurements": [
                        {
                            "name": "speed",
                            "value": round(sample["speed_m_s"], 3),
                            "unit": "m_s-1",
                            "quality": "valid",
                            "confidence": 0.97,
                        },
                        {
                            "name": "heading",
                            "value": round(sample["heading_deg"], 1),
                            "unit": "degrees",
                            "quality": "valid",
                            "confidence": 0.95,
                        },
                    ],
                    "truth_label": "simulated",
                    "privacy_classification": "none",
                    "retention_class": "standard",
                    "correlation_id": f"{CORRELATION_ID}:{dispatch.assignment_id}",
                }
            )

    for index, event in enumerate(avl_events):
        event["event_id"] = str(
            uuid.uuid5(
                uuid.UUID("6f6f6f6f-0304-4a4a-8a8a-202609180005"),
                f"{RUN_ID}:{event['device_id']}:{index}",
            )
        )
        event["ingest_time"] = iso(
            anchor,
            (
                datetime.fromisoformat(event["observation_time"].replace("Z", "+00:00")) - anchor
            ).total_seconds()
            + 0.25,
        )
        event["clock_quality"] = "synced"
        event["geometry_version"] = GEOMETRY_VERSION
        event["provenance"] = {
            "producer": "traffic-sim-emergency-generator",
            "pipeline_version": "p03.04.1",
        }

    calls_records.sort(key=lambda c: c["call_id"])
    assignment_records.sort(key=lambda a: a["assignment_id"])
    device_records.sort(key=lambda d: d["device_id"])
    avl_events.sort(key=lambda e: (e["observation_time"], e["device_id"], e["sequence_number"]))

    all_records = calls_records + assignment_records
    violations = (
        _no_patient_fields(all_records)
        + _no_patient_fields(avl_events)
        + _no_patient_fields(device_records)
    )
    if violations:
        raise SystemExit(f"patient-related field/value found (not permitted): {violations}")

    def write_jsonl(records: list[dict], path: Path) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            for record in records:
                stream.write(json.dumps(record, sort_keys=True) + "\n")

    calls_path = run_dir / "calls.jsonl"
    assignments_path = run_dir / "assignments.jsonl"
    devices_path = run_dir / "devices.jsonl"
    avl_path = run_dir / "avl_events.jsonl"
    write_jsonl(calls_records, calls_path)
    write_jsonl(assignment_records, assignments_path)
    write_jsonl(device_records, devices_path)
    write_jsonl(avl_events, avl_path)

    stats = ET.parse(stats_out).getroot()
    teleport_count = int(stats.find("teleports").get("total", "-1"))
    collision_count = int(stats.find("safety").get("collisions", "-1"))

    return {
        "calls_path": calls_path,
        "assignments_path": assignments_path,
        "devices_path": devices_path,
        "avl_path": avl_path,
        "call_count": len(calls_records),
        "assignment_count": len(assignment_records),
        "device_count": len(device_records),
        "avl_event_count": len(avl_events),
        "teleport_count": teleport_count,
        "collision_count": collision_count,
    }


def main() -> None:
    if not NET_FILE.is_file():
        raise SystemExit(f"missing {NET_FILE}; run P03.01's network build_and_verify.py first")
    OUTPUT_DIR.mkdir(exist_ok=True)

    run_a = build_one_run("run-a")
    run_b = build_one_run("run-b")

    for key in ("calls_path", "assignments_path", "devices_path", "avl_path"):
        text_a = run_a[key].read_text(encoding="utf-8")
        text_b = run_b[key].read_text(encoding="utf-8")
        if text_a != text_b:
            raise SystemExit(f"{key} is not byte-identical across two runs of the same seed/run_id")

    if run_a["teleport_count"] != 0:
        raise SystemExit(
            f"expected zero teleports for emergency trips, got {run_a['teleport_count']}"
        )
    if run_a["collision_count"] != 0:
        raise SystemExit(
            f"expected zero collisions for emergency trips, got {run_a['collision_count']}"
        )

    summary = {
        "seed": SEED,
        "run_id": RUN_ID,
        "anchor_utc": ANCHOR_UTC,
        "sim_end_seconds": SIM_END,
        "calls": run_a["call_count"],
        "assignments": run_a["assignment_count"],
        "devices": run_a["device_count"],
        "avl_events": run_a["avl_event_count"],
        "teleport_count": run_a["teleport_count"],
        "collision_count": run_a["collision_count"],
        "deterministic": True,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
