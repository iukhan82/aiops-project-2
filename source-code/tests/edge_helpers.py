"""Shared builders for edge tests: valid device registry + envelope events."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from edge.timeutil import format_ts
from edge.validation import DeviceRegistry, EdgeValidator, ValidationConfig

GEOMETRY = "2026-09-18.1"
T0 = datetime(2026, 9, 18, 9, 0, 0, tzinfo=timezone.utc)
NS = uuid.UUID("11111111-2222-4333-8444-555555555555")


def device(
    device_id: str,
    device_type: str,
    status: str = "active",
    agency: str = "city-traffic-ops",
    lane_id: str | None = None,
):
    record = {
        "schema_version": "1.0.0",
        "device_id": device_id,
        "device_type": device_type,
        "deployment_type": "simulated",
        "agency_scope": agency,
        "location": {
            "geometry_version": GEOMETRY,
            "coordinate_reference": "EPSG:4326",
            "latitude": 31.5204,
            "longitude": 74.3587,
        },
        "capabilities": ["vehicle_count"],
        "status": status,
        "registered_at": "2026-09-18T00:00:00Z",
        "privacy_classification": "none",
        "retention_class": "standard",
    }
    if lane_id:
        record["location"]["lane_id"] = lane_id
    return record


def make_chain_registry() -> DeviceRegistry:
    """Corridor a eastbound (e1->e2->e3) plus one westbound loop, as in the real network."""
    return DeviceRegistry(
        [
            device("loop-e1", "inductive_loop", lane_id="int-a1_int-a2_2"),
            device("loop-e2", "inductive_loop", lane_id="int-a2_int-a3_2"),
            device("loop-e3", "inductive_loop", lane_id="int-a3_int-a4_2"),
            device("loop-w2", "inductive_loop", lane_id="int-a3_int-a2_2"),
            device("cyc-e1", "cycle_counter", lane_id="int-a1_int-a2_1"),
        ]
    )


def make_registry() -> DeviceRegistry:
    return DeviceRegistry(
        [
            device("loop-a", "inductive_loop"),
            device("loop-b", "inductive_loop"),
            device("cyc-a", "cycle_counter"),
            device("loop-dead", "inductive_loop", status="inactive"),
            device("loop-fire", "inductive_loop", agency="fire-dispatch"),
        ]
    )


def make_validator(**overrides) -> EdgeValidator:
    return EdgeValidator(
        make_registry(), ValidationConfig(expected_geometry_version=GEOMETRY, **overrides)
    )


def event(
    device_id: str = "loop-a",
    seq: int = 0,
    obs: datetime = T0,
    count: float = 3,
    occ: float = 0.1,
    speed: float | None = 10.0,
    *,
    event_type: str = "traffic.loop_detector.count",
    lag_s: float = 0.25,
    clock: str = "synced",
    quality: str = "valid",
    confidence: float = 0.95,
    agency: str = "city-traffic-ids-unused",
) -> dict:
    measurements = [
        {
            "name": "vehicle_count",
            "value": count,
            "unit": "count",
            "quality": quality,
            "confidence": confidence,
        },
        {
            "name": "occupancy",
            "value": occ,
            "unit": "ratio",
            "quality": quality,
            "confidence": confidence,
        },
    ]
    if speed is not None:
        measurements.append(
            {
                "name": "mean_speed",
                "value": speed,
                "unit": "m_s-1",
                "quality": quality,
                "confidence": confidence,
            }
        )
    return {
        "schema_version": "1.0.0",
        "event_id": str(uuid.uuid5(NS, f"{device_id}:{seq}:{obs.isoformat()}")),
        "event_type": event_type,
        "device_id": device_id,
        "agency_scope": "fire-dispatch" if device_id == "loop-fire" else "city-traffic-ops",
        "observation_time": format_ts(obs),
        "ingest_time": format_ts(obs + timedelta(seconds=lag_s)),
        "sequence_number": seq,
        "clock_quality": clock,
        "geometry_version": GEOMETRY,
        "location": {
            "coordinate_reference": "EPSG:4326",
            "latitude": 31.5204,
            "longitude": 74.3587,
        },
        "measurements": measurements,
        "truth_label": "simulated",
        "privacy_classification": "none",
        "retention_class": "standard",
    }
