"""P03.06: labeled synthetic device/platform fault overlays - silence,
stuck, clock, network, model, service, storage - per
docs/SENSOR_AND_DATA_CATALOG.md section 7 and section 10's required
data-quality incidents.

Same mechanism as P03.05's overlay scenarios and for the same reason: none
of these seven conditions are things SUMO simulates (they are
platform/AIOps-layer faults, not road physics), so each picks one real
device - four from the P03.03 sensor catalog, plus one new `aiops_agent`
device representing the platform's own self-observability agent for the
three faults the sensor catalog has no device for (model, service,
storage) - and emits exactly two observation-envelope events: an onset
(anomalous, `quality` degraded or `clock_quality: "drifting"`) and an end
(recovered).
"""

from __future__ import annotations

import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sensors"))
from build_sensor_catalog import build_catalog  # noqa: E402

GEOMETRY_VERSION = "2026-09-18.1"
AGENCY_SCOPE = "city-traffic-ops"
PRODUCER = "traffic-sim-fault-generator"
PIPELINE_VERSION = "p03.06.1"
NAMESPACE = uuid.UUID("6f6f6f6f-0306-4a4a-8a8a-202609180007")

AIOPS_AGENT_DEVICE_ID = "aiops-agent-platform-self-observability"


def _iso(anchor: datetime, offset_s: float) -> str:
    dt = anchor + timedelta(seconds=offset_s)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def aiops_agent_device() -> dict:
    return {
        "schema_version": "1.0.0",
        "device_id": AIOPS_AGENT_DEVICE_ID,
        "device_type": "aiops_agent",
        "deployment_type": "simulated",
        "agency_scope": AGENCY_SCOPE,
        "location": {
            "geometry_version": GEOMETRY_VERSION,
            "coordinate_reference": "EPSG:4326",
            "latitude": 31.5204,
            "longitude": 74.3587,
        },
        "capabilities": [
            "model_inference_health",
            "service_health",
            "storage_health",
        ],
        "status": "active",
        "registered_at": "2026-09-18T00:00:00Z",
        "privacy_classification": "none",
        "retention_class": "standard",
    }


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
    clock_quality: str = "synced",
    ingest_lag_s: float = 0.25,
) -> dict:
    observation_time = _iso(anchor, observation_time_s)
    location = device["location"]
    return {
        "schema_version": "1.0.0",
        "event_id": str(uuid.uuid5(NAMESPACE, f"{run_id}:{device['device_id']}:{seq}")),
        "event_type": event_type,
        "device_id": device["device_id"],
        "agency_scope": device["agency_scope"],
        "observation_time": observation_time,
        "ingest_time": _iso(anchor, observation_time_s + ingest_lag_s),
        "sequence_number": seq,
        "clock_quality": clock_quality,
        "geometry_version": GEOMETRY_VERSION,
        "location": {
            "coordinate_reference": "EPSG:4326",
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            **(
                {"intersection_id": location["intersection_id"]}
                if "intersection_id" in location
                else {}
            ),
            **({"corridor_id": location["corridor_id"]} if "corridor_id" in location else {}),
        },
        "measurements": measurements,
        "truth_label": "simulated",
        "privacy_classification": "none",
        "retention_class": "standard",
        "correlation_id": f"fault-{scenario_id}",
        "provenance": {"producer": PRODUCER, "pipeline_version": PIPELINE_VERSION},
    }


def _ground_truth(
    scenario_id: str,
    fault_type: str,
    onset_s: float,
    end_s: float,
    anchor: datetime,
    affected: list[str],
    description: str,
) -> dict:
    return {
        "scenario_id": scenario_id,
        "scenario_type": fault_type,
        "onset": _iso(anchor, onset_s),
        "end": _iso(anchor, end_s),
        "affected_entities": affected,
        "description": description,
        "truth_label": "simulated",
        "synthetic_overlay": True,
    }


#            (fault_type, base_onset_s, duration_s, device_type)
_FAULT_SPECS = [
    ("silence", 60.0, 120.0, "cycle_counter"),
    ("stuck", 150.0, 120.0, "inductive_loop"),
    ("clock", 220.0, 80.0, "weather_station"),
    ("network", 260.0, 80.0, "crossing_detector"),
    ("model", 300.0, 80.0, None),
    ("service", 340.0, 80.0, None),
    ("storage", 380.0, 80.0, None),
]


def _pick_device(devices: list[dict], device_type: str, offset: int) -> dict:
    matching = sorted(
        (d for d in devices if d["device_type"] == device_type), key=lambda d: d["device_id"]
    )
    return matching[offset % len(matching)]


def _fault_events(
    fault_type: str,
    device: dict,
    run_id: str,
    scenario_id: str,
    onset_s: float,
    end_s: float,
    anchor: datetime,
    seq_onset: int,
    seq_end: int,
) -> list[dict]:
    if fault_type == "silence":
        onset = _event(
            run_id=run_id,
            scenario_id=scenario_id,
            seq=seq_onset,
            event_type="platform.device_health.silence_detected",
            device=device,
            observation_time_s=onset_s,
            anchor=anchor,
            measurements=[
                {
                    "name": "seconds_since_last_seen",
                    "value": 60.0,
                    "unit": "s",
                    "quality": "invalid",
                    "confidence": 0.9,
                },
            ],
        )
        end = _event(
            run_id=run_id,
            scenario_id=scenario_id,
            seq=seq_end,
            event_type="platform.device_health.resumed",
            device=device,
            observation_time_s=end_s,
            anchor=anchor,
            measurements=[
                {
                    "name": "seconds_since_last_seen",
                    "value": 0.0,
                    "unit": "s",
                    "quality": "valid",
                    "confidence": 0.97,
                },
            ],
        )
    elif fault_type == "stuck":
        onset = _event(
            run_id=run_id,
            scenario_id=scenario_id,
            seq=seq_onset,
            event_type="platform.device_health.stuck_value",
            device=device,
            observation_time_s=onset_s,
            anchor=anchor,
            measurements=[
                {
                    "name": "stuck_value_flag",
                    "value": True,
                    "unit": "boolean",
                    "quality": "suspect",
                    "confidence": 0.85,
                },
                {
                    "name": "repeated_value",
                    "value": 4,
                    "unit": "count",
                    "quality": "suspect",
                    "confidence": 0.85,
                },
            ],
        )
        end = _event(
            run_id=run_id,
            scenario_id=scenario_id,
            seq=seq_end,
            event_type="platform.device_health.resumed",
            device=device,
            observation_time_s=end_s,
            anchor=anchor,
            measurements=[
                {
                    "name": "stuck_value_flag",
                    "value": False,
                    "unit": "boolean",
                    "quality": "valid",
                    "confidence": 0.96,
                },
            ],
        )
    elif fault_type == "clock":
        onset = _event(
            run_id=run_id,
            scenario_id=scenario_id,
            seq=seq_onset,
            event_type="platform.device_health.clock_drift",
            device=device,
            observation_time_s=onset_s,
            anchor=anchor,
            measurements=[
                {
                    "name": "clock_offset",
                    "value": 47.5,
                    "unit": "s",
                    "quality": "suspect",
                    "confidence": 0.8,
                },
            ],
            clock_quality="drifting",
        )
        end = _event(
            run_id=run_id,
            scenario_id=scenario_id,
            seq=seq_end,
            event_type="platform.device_health.resumed",
            device=device,
            observation_time_s=end_s,
            anchor=anchor,
            measurements=[
                {
                    "name": "clock_offset",
                    "value": 0.1,
                    "unit": "s",
                    "quality": "valid",
                    "confidence": 0.97,
                },
            ],
            clock_quality="synced",
        )
    elif fault_type == "network":
        onset = _event(
            run_id=run_id,
            scenario_id=scenario_id,
            seq=seq_onset,
            event_type="platform.device_health.network_degraded",
            device=device,
            observation_time_s=onset_s,
            anchor=anchor,
            measurements=[
                {
                    "name": "reconnect_count",
                    "value": 3,
                    "unit": "count",
                    "quality": "suspect",
                    "confidence": 0.8,
                },
                {
                    "name": "packet_loss",
                    "value": 0.22,
                    "unit": "ratio",
                    "quality": "suspect",
                    "confidence": 0.75,
                },
            ],
            ingest_lag_s=45.0,
        )
        end = _event(
            run_id=run_id,
            scenario_id=scenario_id,
            seq=seq_end,
            event_type="platform.device_health.resumed",
            device=device,
            observation_time_s=end_s,
            anchor=anchor,
            measurements=[
                {
                    "name": "reconnect_count",
                    "value": 0,
                    "unit": "count",
                    "quality": "valid",
                    "confidence": 0.96,
                },
                {
                    "name": "packet_loss",
                    "value": 0.0,
                    "unit": "ratio",
                    "quality": "valid",
                    "confidence": 0.97,
                },
            ],
        )
    elif fault_type == "model":
        onset = _event(
            run_id=run_id,
            scenario_id=scenario_id,
            seq=seq_onset,
            event_type="platform.model_health.degraded",
            device=device,
            observation_time_s=onset_s,
            anchor=anchor,
            measurements=[
                {
                    "name": "confidence_mean",
                    "value": 0.31,
                    "unit": "ratio",
                    "quality": "suspect",
                    "confidence": 0.8,
                },
                {
                    "name": "input_drift_score",
                    "value": 0.72,
                    "unit": "ratio",
                    "quality": "suspect",
                    "confidence": 0.75,
                },
            ],
        )
        end = _event(
            run_id=run_id,
            scenario_id=scenario_id,
            seq=seq_end,
            event_type="platform.model_health.resumed",
            device=device,
            observation_time_s=end_s,
            anchor=anchor,
            measurements=[
                {
                    "name": "confidence_mean",
                    "value": 0.91,
                    "unit": "ratio",
                    "quality": "valid",
                    "confidence": 0.95,
                },
                {
                    "name": "input_drift_score",
                    "value": 0.08,
                    "unit": "ratio",
                    "quality": "valid",
                    "confidence": 0.95,
                },
            ],
        )
    elif fault_type == "service":
        onset = _event(
            run_id=run_id,
            scenario_id=scenario_id,
            seq=seq_onset,
            event_type="platform.service_health.degraded",
            device=device,
            observation_time_s=onset_s,
            anchor=anchor,
            measurements=[
                {
                    "name": "request_error_rate",
                    "value": 0.38,
                    "unit": "ratio",
                    "quality": "suspect",
                    "confidence": 0.8,
                },
                {
                    "name": "saturation",
                    "value": 0.94,
                    "unit": "ratio",
                    "quality": "suspect",
                    "confidence": 0.8,
                },
            ],
        )
        end = _event(
            run_id=run_id,
            scenario_id=scenario_id,
            seq=seq_end,
            event_type="platform.service_health.resumed",
            device=device,
            observation_time_s=end_s,
            anchor=anchor,
            measurements=[
                {
                    "name": "request_error_rate",
                    "value": 0.01,
                    "unit": "ratio",
                    "quality": "valid",
                    "confidence": 0.96,
                },
                {
                    "name": "saturation",
                    "value": 0.35,
                    "unit": "ratio",
                    "quality": "valid",
                    "confidence": 0.96,
                },
            ],
        )
    elif fault_type == "storage":
        onset = _event(
            run_id=run_id,
            scenario_id=scenario_id,
            seq=seq_onset,
            event_type="platform.storage_health.degraded",
            device=device,
            observation_time_s=onset_s,
            anchor=anchor,
            measurements=[
                {
                    "name": "consumer_lag",
                    "value": 480.0,
                    "unit": "s",
                    "quality": "suspect",
                    "confidence": 0.8,
                },
                {
                    "name": "replay_backlog",
                    "value": 12000,
                    "unit": "count",
                    "quality": "suspect",
                    "confidence": 0.8,
                },
            ],
        )
        end = _event(
            run_id=run_id,
            scenario_id=scenario_id,
            seq=seq_end,
            event_type="platform.storage_health.resumed",
            device=device,
            observation_time_s=end_s,
            anchor=anchor,
            measurements=[
                {
                    "name": "consumer_lag",
                    "value": 2.0,
                    "unit": "s",
                    "quality": "valid",
                    "confidence": 0.96,
                },
                {
                    "name": "replay_backlog",
                    "value": 0,
                    "unit": "count",
                    "quality": "valid",
                    "confidence": 0.97,
                },
            ],
        )
    else:  # pragma: no cover - defensive
        raise ValueError(fault_type)
    return [onset, end]


def build_faults(
    net_file: Path, nod_file: Path, run_id: str, anchor: datetime, seed: int = 20260918
) -> dict[str, object]:
    """`seed` varies device selection and fault onset timing (duration is
    fixed per fault type) so different seeds produce genuinely different
    fault datasets for P03.08's cross-seed splits, not just different
    event_ids for the same content."""
    devices = build_catalog(net_file, nod_file)["devices"]
    agent = aiops_agent_device()
    rng = random.Random(f"{seed}:{run_id}:faults")

    events: list[dict] = []
    ground_truth: list[dict] = []
    device_sequence: dict[str, int] = {}

    def next_seq(device_id: str) -> int:
        value = device_sequence.get(device_id, 0)
        device_sequence[device_id] = value + 1
        return value

    for base_offset, (fault_type, base_onset_s, duration_s, device_type) in enumerate(_FAULT_SPECS):
        offset = base_offset + rng.randrange(0, 1000)
        onset_s = max(0.0, base_onset_s + rng.uniform(-20.0, 20.0))
        end_s = onset_s + duration_s
        device = agent if device_type is None else _pick_device(devices, device_type, offset)
        scenario_id = f"p03-06-{fault_type}"
        seq_onset = next_seq(device["device_id"])
        seq_end = next_seq(device["device_id"])
        events.extend(
            _fault_events(
                fault_type, device, run_id, scenario_id, onset_s, end_s, anchor, seq_onset, seq_end
            )
        )
        ground_truth.append(
            _ground_truth(
                scenario_id=scenario_id,
                fault_type=fault_type,
                onset_s=onset_s,
                end_s=end_s,
                anchor=anchor,
                affected=[device["device_id"]],
                description=f"Synthetic {fault_type} fault overlaid on {device['device_id']}.",
            )
        )

    events.sort(key=lambda e: (e["observation_time"], e["device_id"], e["sequence_number"]))
    ground_truth.sort(key=lambda g: g["scenario_id"])
    return {"events": events, "ground_truth": ground_truth, "aiops_agent_device": agent}
