"""P03.05: labeled synthetic-anomaly overlays for the five scenario types
SUMO cannot safely or meaningfully simulate as physics in this project
(collision, wrong-way, flood, low visibility, signal fault) - see README.md
for why each is an overlay rather than a physical simulation.

Each overlay picks one real device from the P03.03 catalog
(`source-code/simulator/sensors/build_sensor_catalog.py`) and emits exactly
two observation-envelope events against it: an onset reading (the anomalous
value, `quality: "suspect"`) and an end reading (`quality: "valid"`, back to
a normal-shaped value), plus a separate ground-truth record with
scenario_type/onset/end/affected_entities. The two events are illustrative
sensor evidence, not a claim that this is how every such anomaly would
present in every case.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sensors"))
from build_sensor_catalog import build_catalog  # noqa: E402

GEOMETRY_VERSION = "2026-09-18.1"
AGENCY_SCOPE = "city-traffic-ops"
PRODUCER = "traffic-sim-scenario-generator"
PIPELINE_VERSION = "p03.05.1"
NAMESPACE = uuid.UUID("6f6f6f6f-0305-4a4a-8a8a-202609180006")


def _iso(anchor: datetime, offset_s: float) -> str:
    dt = anchor + timedelta(seconds=offset_s)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _event(
    *,
    run_id: str,
    scenario_id: str,
    seq: int,
    event_type: str,
    device: dict,
    observation_time_s: float,
    anchor: datetime,
    measurements: list[dict],
) -> dict:
    observation_time = _iso(anchor, observation_time_s)
    return {
        "schema_version": "1.0.0",
        "event_id": str(uuid.uuid5(NAMESPACE, f"{run_id}:{device['device_id']}:{seq}")),
        "event_type": event_type,
        "device_id": device["device_id"],
        "agency_scope": device["agency_scope"],
        "observation_time": observation_time,
        "ingest_time": _iso(anchor, observation_time_s + 0.25),
        "sequence_number": seq,
        "clock_quality": "synced",
        "geometry_version": GEOMETRY_VERSION,
        "location": {
            "coordinate_reference": "EPSG:4326",
            "latitude": device["location"]["latitude"],
            "longitude": device["location"]["longitude"],
            **(
                {"intersection_id": device["location"]["intersection_id"]}
                if "intersection_id" in device["location"]
                else {}
            ),
            **(
                {"corridor_id": device["location"]["corridor_id"]}
                if "corridor_id" in device["location"]
                else {}
            ),
        },
        "measurements": measurements,
        "truth_label": "simulated",
        "privacy_classification": "none",
        "retention_class": "standard",
        "correlation_id": f"scenario-{scenario_id}",
        "provenance": {"producer": PRODUCER, "pipeline_version": PIPELINE_VERSION},
    }


def _ground_truth(
    scenario_id: str,
    scenario_type: str,
    onset_s: float,
    end_s: float,
    anchor: datetime,
    affected: list[str],
    description: str,
) -> dict:
    return {
        "scenario_id": scenario_id,
        "scenario_type": scenario_type,
        "onset": _iso(anchor, onset_s),
        "end": _iso(anchor, end_s),
        "affected_entities": affected,
        "description": description,
        "truth_label": "simulated",
        "synthetic_overlay": True,
    }


_OVERLAY_SPECS = [
    ("collision", 120.0, 180.0, "inductive_loop"),
    ("wrong_way", 200.0, 230.0, "inductive_loop"),
    ("flood", 300.0, 420.0, "road_condition_sensor"),
    ("visibility", 330.0, 450.0, "weather_station"),
    ("signal", 500.0, 560.0, "signal_controller"),
]


def _pick_device(devices: list[dict], device_type: str, offset: int) -> dict:
    matching = sorted(
        (d for d in devices if d["device_type"] == device_type), key=lambda d: d["device_id"]
    )
    return matching[offset % len(matching)]


def _anomaly_measurements(scenario_type: str) -> tuple[list[dict], list[dict]]:
    if scenario_type == "collision":
        onset = [
            {
                "name": "vehicle_count",
                "value": 0,
                "unit": "count",
                "quality": "suspect",
                "confidence": 0.4,
            },
            {
                "name": "stopped_vehicle_flag",
                "value": True,
                "unit": "boolean",
                "quality": "suspect",
                "confidence": 0.85,
            },
        ]
        end = [
            {
                "name": "vehicle_count",
                "value": 2,
                "unit": "count",
                "quality": "valid",
                "confidence": 0.97,
            },
            {
                "name": "stopped_vehicle_flag",
                "value": False,
                "unit": "boolean",
                "quality": "valid",
                "confidence": 0.95,
            },
        ]
    elif scenario_type == "wrong_way":
        onset = [
            {
                "name": "direction_conflict",
                "value": True,
                "unit": "boolean",
                "quality": "suspect",
                "confidence": 0.7,
            },
            {
                "name": "mean_speed",
                "value": -8.5,
                "unit": "m_s-1",
                "quality": "suspect",
                "confidence": 0.6,
            },
        ]
        end = [
            {
                "name": "direction_conflict",
                "value": False,
                "unit": "boolean",
                "quality": "valid",
                "confidence": 0.95,
            },
        ]
    elif scenario_type == "flood":
        onset = [
            {
                "name": "surface_state",
                "value": "flooded",
                "unit": "category",
                "quality": "suspect",
                "confidence": 0.8,
            },
            {
                "name": "friction_estimate",
                "value": 0.18,
                "unit": "ratio",
                "quality": "suspect",
                "confidence": 0.75,
            },
        ]
        end = [
            {
                "name": "surface_state",
                "value": "wet",
                "unit": "category",
                "quality": "valid",
                "confidence": 0.9,
            },
            {
                "name": "friction_estimate",
                "value": 0.6,
                "unit": "ratio",
                "quality": "valid",
                "confidence": 0.88,
            },
        ]
    elif scenario_type == "visibility":
        onset = [
            {
                "name": "visibility_distance",
                "value": 80.0,
                "unit": "m",
                "quality": "suspect",
                "confidence": 0.8,
            },
        ]
        end = [
            {
                "name": "visibility_distance",
                "value": 9000.0,
                "unit": "m",
                "quality": "valid",
                "confidence": 0.9,
            },
        ]
    elif scenario_type == "signal":
        onset = [
            {
                "name": "signal_state",
                "value": "fault",
                "unit": "category",
                "quality": "invalid",
                "confidence": 0.9,
            },
            {
                "name": "active_phase",
                "value": -1,
                "unit": "index",
                "quality": "invalid",
                "confidence": 0.9,
            },
        ]
        end = [
            {
                "name": "signal_state",
                "value": "GGGrrrr",
                "unit": "category",
                "quality": "valid",
                "confidence": 0.97,
            },
            {
                "name": "active_phase",
                "value": 0,
                "unit": "index",
                "quality": "valid",
                "confidence": 0.97,
            },
        ]
    else:  # pragma: no cover - defensive
        raise ValueError(scenario_type)
    return onset, end


def build_overlays(
    net_file: Path, nod_file: Path, run_id: str, anchor: datetime
) -> dict[str, list[dict]]:
    devices = build_catalog(net_file, nod_file)["devices"]
    events: list[dict] = []
    ground_truth: list[dict] = []

    event_type_by_scenario = {
        "collision": "traffic.loop_detector.count",
        "wrong_way": "traffic.loop_detector.count",
        "flood": "road.condition_sensor.reading",
        "visibility": "weather.station.reading",
        "signal": "signal.controller.spat",
    }

    for offset, (scenario_type, onset_s, end_s, device_type) in enumerate(_OVERLAY_SPECS):
        device = _pick_device(devices, device_type, offset)
        scenario_id = f"p03-05-{scenario_type}"
        onset_measurements, end_measurements = _anomaly_measurements(scenario_type)
        events.append(
            _event(
                run_id=run_id,
                scenario_id=scenario_id,
                seq=0,
                event_type=event_type_by_scenario[scenario_type],
                device=device,
                observation_time_s=onset_s,
                anchor=anchor,
                measurements=onset_measurements,
            )
        )
        events.append(
            _event(
                run_id=run_id,
                scenario_id=scenario_id,
                seq=1,
                event_type=event_type_by_scenario[scenario_type],
                device=device,
                observation_time_s=end_s,
                anchor=anchor,
                measurements=end_measurements,
            )
        )
        ground_truth.append(
            _ground_truth(
                scenario_id=scenario_id,
                scenario_type=scenario_type,
                onset_s=onset_s,
                end_s=end_s,
                anchor=anchor,
                affected=[device["device_id"]],
                description=f"Synthetic {scenario_type.replace('_', ' ')} anomaly overlaid on {device['device_id']}.",
            )
        )

    events.sort(key=lambda e: (e["observation_time"], e["device_id"], e["sequence_number"]))
    ground_truth.sort(key=lambda g: g["scenario_id"])
    return {"events": events, "ground_truth": ground_truth}
