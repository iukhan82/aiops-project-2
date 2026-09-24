"""P03.03: deterministic (seed, run_id) observation-envelope event generator.

Combines three sources, all deterministic given the same seed/run_id/anchor:

1. Native SUMO induction-loop output (loop-output.xml) for traffic and cyclist
   counters -- real simulated counts/occupancy/speed, not invented numbers.
2. Native SUMO TLS-state output (tls-int-*.xml, one per intersection) for
   signal controller SPaT events, emitted on phase transition.
3. Pure-Python seeded generators for pedestrian crossing demand and for
   weather/road-condition readings, documented in README.md as a scoped
   simplification: they are not derived from individual FCD pedestrian
   trajectories or a physical weather model.

Every event conforms to source-code/contracts/observation-envelope/v1/schema.json.
"""

from __future__ import annotations

import math
import random
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = "1.0.0"
AGENCY_SCOPE = "city-traffic-ops"
GEOMETRY_VERSION = "2026-09-18.1"
PRODUCER = "traffic-sim-sensor-generator"
PIPELINE_VERSION = "p03.03.1"
INGEST_LATENCY = timedelta(milliseconds=250)

# Fixed namespace for deterministic event_id = uuid5(namespace, "run_id:device_id:sequence").
EVENT_NAMESPACE = uuid.UUID("6f6f6f6f-0303-4a4a-8a8a-202609180003")


class SequenceCounter:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def next(self, device_id: str) -> int:
        value = self._counts.get(device_id, 0)
        self._counts[device_id] = value + 1
        return value


def _event_id(run_id: str, device_id: str, sequence_number: int) -> str:
    return str(uuid.uuid5(EVENT_NAMESPACE, f"{run_id}:{device_id}:{sequence_number}"))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _envelope(
    *,
    run_id: str,
    seq: SequenceCounter,
    event_type: str,
    device_id: str,
    observation_time: datetime,
    location: dict[str, object],
    measurements: list[dict[str, object]],
    correlation_id: str,
) -> dict[str, object]:
    sequence_number = seq.next(device_id)
    ingest_time = observation_time + INGEST_LATENCY
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": _event_id(run_id, device_id, sequence_number),
        "event_type": event_type,
        "device_id": device_id,
        "agency_scope": AGENCY_SCOPE,
        "observation_time": _iso(observation_time),
        "ingest_time": _iso(ingest_time),
        "sequence_number": sequence_number,
        "clock_quality": "synced",
        "geometry_version": GEOMETRY_VERSION,
        "location": location,
        "measurements": measurements,
        "truth_label": "simulated",
        "privacy_classification": "none",
        "retention_class": "standard",
        "correlation_id": correlation_id,
        "provenance": {"producer": PRODUCER, "pipeline_version": PIPELINE_VERSION},
    }


def _device_location(device: dict[str, object]) -> dict[str, object]:
    loc = device["location"]
    out: dict[str, object] = {
        "coordinate_reference": loc["coordinate_reference"],
        "latitude": loc["latitude"],
        "longitude": loc["longitude"],
    }
    for key in ("intersection_id", "corridor_id", "lane_id"):
        if key in loc:
            out[key] = loc[key]
    return out


def parse_loop_intervals(path: Path) -> dict[str, list[dict[str, float]]]:
    root = ET.parse(path).getroot()
    by_device: dict[str, list[dict[str, float]]] = {}
    for interval in root.findall("interval"):
        device_id = interval.get("id")
        by_device.setdefault(device_id, []).append(
            {
                "begin": float(interval.get("begin")),
                "end": float(interval.get("end")),
                "n_veh": int(interval.get("nVehContrib")),
                "occupancy_pct": float(interval.get("occupancy")),
                "speed_m_s": float(interval.get("speed")),
            }
        )
    for rows in by_device.values():
        rows.sort(key=lambda r: r["begin"])
    return by_device


def traffic_events(
    devices: list[dict[str, object]],
    intervals_by_device: dict[str, list[dict[str, float]]],
    anchor: datetime,
    run_id: str,
    seq: SequenceCounter,
    correlation_id: str,
    event_type: str,
    count_name: str,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for device in devices:
        device_id = device["device_id"]
        for row in intervals_by_device.get(device_id, []):
            observation_time = anchor + timedelta(seconds=row["end"])
            measurements = [
                {
                    "name": count_name,
                    "value": row["n_veh"],
                    "unit": "count",
                    "quality": "valid",
                    "confidence": 0.98,
                },
                {
                    "name": "occupancy",
                    "value": round(row["occupancy_pct"] / 100.0, 4),
                    "unit": "ratio",
                    "quality": "valid",
                    "confidence": 0.95,
                },
            ]
            if row["n_veh"] > 0 and row["speed_m_s"] >= 0:
                measurements.append(
                    {
                        "name": "mean_speed",
                        "value": round(row["speed_m_s"], 3),
                        "unit": "m_s-1",
                        "quality": "valid",
                        "confidence": 0.9,
                    }
                )
            events.append(
                _envelope(
                    run_id=run_id,
                    seq=seq,
                    event_type=event_type,
                    device_id=device_id,
                    observation_time=observation_time,
                    location=_device_location(device),
                    measurements=measurements,
                    correlation_id=correlation_id,
                )
            )
    return events


def parse_tls_transitions(path: Path) -> list[tuple[float, int, str]]:
    root = ET.parse(path).getroot()
    rows = [
        (float(r.get("time")), int(r.get("phase")), r.get("state"))
        for r in root.findall("tlsState")
    ]
    rows.sort(key=lambda r: r[0])
    transitions: list[tuple[float, int, str]] = []
    last_state = None
    for time_s, phase, state in rows:
        if state != last_state:
            transitions.append((time_s, phase, state))
            last_state = state
    return transitions


def signal_events(
    devices: list[dict[str, object]],
    tls_files: dict[str, Path],
    anchor: datetime,
    run_id: str,
    seq: SequenceCounter,
    correlation_id: str,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for device in devices:
        junction_id = device["location"]["intersection_id"]
        path = tls_files[junction_id]
        for time_s, phase, state in parse_tls_transitions(path):
            observation_time = anchor + timedelta(seconds=time_s)
            measurements = [
                {
                    "name": "active_phase",
                    "value": phase,
                    "unit": "index",
                    "quality": "valid",
                    "confidence": 1.0,
                },
                {
                    "name": "signal_state",
                    "value": state,
                    "unit": "category",
                    "quality": "valid",
                    "confidence": 1.0,
                },
            ]
            events.append(
                _envelope(
                    run_id=run_id,
                    seq=seq,
                    event_type="signal.controller.spat",
                    device_id=device["device_id"],
                    observation_time=observation_time,
                    location=_device_location(device),
                    measurements=measurements,
                    correlation_id=correlation_id,
                )
            )
    return events


def crossing_events(
    devices: list[dict[str, object]],
    sim_end: int,
    anchor: datetime,
    seed: int,
    run_id: str,
    seq: SequenceCounter,
    correlation_id: str,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for device in devices:
        device_id = device["device_id"]
        rng = random.Random(f"{seed}:{device_id}:crossing")
        mean_gap_s = 90.0
        pending: list[tuple[datetime, str, list[dict[str, object]]]] = []
        t = rng.expovariate(1.0 / mean_gap_s)
        while t < sim_end:
            demand_time = anchor + timedelta(seconds=t)
            clearance_s = round(rng.uniform(6.0, 14.0), 1)
            pending.append(
                (
                    demand_time,
                    "vru.crossing_detector.demand",
                    [
                        {
                            "name": "crossing_demand",
                            "value": True,
                            "unit": "boolean",
                            "quality": "valid",
                            "confidence": 0.9,
                        }
                    ],
                )
            )
            pending.append(
                (
                    demand_time + timedelta(seconds=clearance_s),
                    "vru.crossing_detector.clearance",
                    [
                        {
                            "name": "crossing_clearance",
                            "value": clearance_s,
                            "unit": "s",
                            "quality": "valid",
                            "confidence": 0.9,
                        }
                    ],
                )
            )
            t += rng.expovariate(1.0 / mean_gap_s)
        # sequence numbers must be monotonic in observation-time order per
        # device (observation-envelope/v1); a clearance can fall after the
        # next arrival's demand, so sort before numbering.
        pending.sort(key=lambda item: (item[0], item[1]))
        for observation_time, event_type, measurements in pending:
            events.append(
                _envelope(
                    run_id=run_id,
                    seq=seq,
                    event_type=event_type,
                    device_id=device_id,
                    observation_time=observation_time,
                    location=_device_location(device),
                    measurements=measurements,
                    correlation_id=correlation_id,
                )
            )
    return events


def weather_and_road_events(
    weather_devices: list[dict[str, object]],
    road_devices: list[dict[str, object]],
    sim_end: int,
    anchor: datetime,
    seed: int,
    run_id: str,
    seq: SequenceCounter,
    correlation_id: str,
    step_s: int = 60,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    road_by_corridor = {d["location"]["corridor_id"]: d for d in road_devices}

    for weather_device in weather_devices:
        corridor_id = weather_device["location"]["corridor_id"]
        road_device = road_by_corridor[corridor_id]
        rng = random.Random(f"{seed}:{weather_device['device_id']}:weather")

        for t in range(0, sim_end + 1, step_s):
            observation_time = anchor + timedelta(seconds=t)
            air_temp_c = round(
                28.0 + 3.0 * math.sin(2 * math.pi * t / 1800.0) + rng.uniform(-0.4, 0.4), 2
            )
            humidity_pct = round(
                min(
                    100.0,
                    max(
                        0.0,
                        55.0
                        + 10.0 * math.sin(2 * math.pi * t / 1800.0 + 1.0)
                        + rng.uniform(-2.0, 2.0),
                    ),
                ),
                1,
            )
            is_raining = rng.random() < 0.1
            precipitation_mm_h = round(rng.uniform(0.5, 4.0), 2) if is_raining else 0.0
            wind_speed = round(2.0 + rng.uniform(0.0, 2.0), 2)

            events.append(
                _envelope(
                    run_id=run_id,
                    seq=seq,
                    event_type="weather.station.reading",
                    device_id=weather_device["device_id"],
                    observation_time=observation_time,
                    location=_device_location(weather_device),
                    measurements=[
                        {
                            "name": "air_temperature",
                            "value": air_temp_c,
                            "unit": "celsius",
                            "quality": "valid",
                            "confidence": 0.96,
                        },
                        {
                            "name": "relative_humidity",
                            "value": humidity_pct,
                            "unit": "percent",
                            "quality": "valid",
                            "confidence": 0.96,
                        },
                        {
                            "name": "precipitation_intensity",
                            "value": precipitation_mm_h,
                            "unit": "mm_h-1",
                            "quality": "valid",
                            "confidence": 0.9,
                        },
                        {
                            "name": "wind_speed",
                            "value": wind_speed,
                            "unit": "m_s-1",
                            "quality": "valid",
                            "confidence": 0.9,
                        },
                    ],
                    correlation_id=correlation_id,
                )
            )

            road_temp_c = round(air_temp_c - (2.0 if is_raining else 0.8), 2)
            surface_state = "wet" if is_raining else "dry"
            friction = round((0.35 if is_raining else 0.75) + rng.uniform(-0.03, 0.03), 3)
            events.append(
                _envelope(
                    run_id=run_id,
                    seq=seq,
                    event_type="road.condition_sensor.reading",
                    device_id=road_device["device_id"],
                    observation_time=observation_time,
                    location=_device_location(road_device),
                    measurements=[
                        {
                            "name": "road_surface_temperature",
                            "value": road_temp_c,
                            "unit": "celsius",
                            "quality": "valid",
                            "confidence": 0.93,
                        },
                        {
                            "name": "surface_state",
                            "value": surface_state,
                            "unit": "category",
                            "quality": "valid",
                            "confidence": 0.9,
                        },
                        {
                            "name": "friction_estimate",
                            "value": friction,
                            "unit": "ratio",
                            "quality": "valid",
                            "confidence": 0.85,
                        },
                    ],
                    correlation_id=correlation_id,
                )
            )
    return events
