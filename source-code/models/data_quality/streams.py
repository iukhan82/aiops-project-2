"""Seeded observation streams for the P03.03 numeric devices, and fault injection with exact ground truth.

Why a generator and not the P03.03 simulator output itself: that output is ten simulated minutes per device and very sparse (a loop counts
0 to 2 vehicles a minute), so there is nothing to freeze, drift or gap over a meaningful time. The device set (ids, types) and the levels of
the continuous channels (speeds, temperatures, humidity, wind, friction) are taken from it. What is ASSUMED, and stated in the model
card: the counts' rates, the diurnal shape, the noise levels and a slow shared weather-like component. Faults are injected into these clean
streams, so the truth is exact and separate (as in P03.06) - but the streams are synthetic and so is every result measured on them.

The five classes are those of acceptance target FA-02:
    missing       events lost or the device silent for a while (the sequence number keeps counting, so the gap shows on resume)
    stuck         every channel frozen at its last value
    drift         one channel walks away from its peers (calibration/ageing error)
    duplicate     the same event delivered again
    out_of_order  events delivered in the wrong order
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SOURCE_ROOT = Path(__file__).resolve().parents[2]
# The device list is COMMITTED next to this file: it is the P03.03 catalogue's numeric devices, copied because that output directory is generated and not
# part of a checkout (a clean clone had no such file and this package's tests failed in the clean-start check, P01.08).
DEVICES_FILE = Path(__file__).with_name("devices.json")
CADENCE_S = 60
STEPS = 480  # eight hours
CALIBRATION_STEPS = 60  # the first hour is fault-free by construction: the detector learns each device's level from it
FIRST_FAULT_STEP = 90
CLASSES = ("missing", "stuck", "drift", "duplicate", "out_of_order")
SEEDS = {
    "train": [101, 102, 103, 104, 105, 106],
    "validation": [201, 202, 203],
    "test": [301, 302, 303],
}
NUMERIC_TYPES = ("inductive_loop", "cycle_counter", "weather_station", "road_condition_sensor")


@dataclass(frozen=True)
class Channel:
    name: str
    level: tuple[float, float]  # per-device level drawn uniformly from this range
    amplitude: float  # relative size of the shared morning-peak component
    cv: float  # relative noise
    counts: bool = False


PROFILES: dict[str, list[Channel]] = {
    # counts and occupancy levels are ASSUMED; speeds are the P03.03 simulated levels (10.7-15.1 m/s loops, 5.2-5.8 m/s cycle counters)
    "inductive_loop": [
        Channel("vehicle_count", (6, 14), 0.6, 0.0, counts=True),
        Channel("mean_speed", (10.7, 15.1), -0.2, 0.08),
        Channel("occupancy", (0.03, 0.08), 0.6, 0.15),
    ],
    "cycle_counter": [
        Channel("cyclist_count", (1.5, 3.5), 0.5, 0.0, counts=True),
        Channel("mean_speed", (5.2, 5.8), 0.0, 0.05),
        Channel("occupancy", (0.004, 0.012), 0.5, 0.3),
    ],
    "weather_station": [
        Channel("air_temperature", (30.1, 30.2), 0.12, 0.01),
        Channel("relative_humidity", (61.9, 62.0), -0.10, 0.02),
        Channel("wind_speed", (2.9, 3.4), 0.3, 0.15),
    ],
    "road_condition_sensor": [
        Channel("road_surface_temperature", (29.2, 29.4), 0.2, 0.015),
        Channel("friction_estimate", (0.72, 0.76), 0.0, 0.03),
    ],
}


def load_devices(path: Path = DEVICES_FILE) -> dict[str, str]:
    devices = json.loads(path.read_text(encoding="utf-8"))["devices"]
    return dict(sorted((d, t) for d, t in devices.items() if t in NUMERIC_TYPES))


@dataclass
class Event:
    device_id: str
    sequence_number: int
    event_id: str
    observation_time: float  # seconds from the start of the run
    arrival: float  # when the platform received it
    values: dict[str, float]

    def copy(self, **changes) -> Event:
        data = {**self.__dict__, "values": dict(self.values), **changes}
        return Event(**data)


@dataclass
class Fault:
    device_id: str
    fault_class: str
    onset: float
    end: float
    detail: dict = field(default_factory=dict)


def _shared_component(rng: np.random.Generator) -> np.ndarray:
    """A slow component every device of a type shares (cloud, a road-works queue): it is why a device is judged against its peers."""
    noise = rng.normal(0, 0.012, STEPS)
    out = np.zeros(STEPS)
    for i in range(1, STEPS):
        out[i] = 0.995 * out[i - 1] + noise[i]
    return out


def clean_streams(seed: int, devices: dict[str, str] | None = None) -> dict[str, list[Event]]:
    devices = devices or load_devices()
    rng = np.random.default_rng(seed)
    hours = 6.0 + np.arange(STEPS) * CADENCE_S / 3600.0
    peak = np.exp(-0.5 * ((hours - 8.5) / 1.2) ** 2)  # a morning peak
    shared = {t: _shared_component(rng) for t in NUMERIC_TYPES}
    streams: dict[str, list[Event]] = {}
    for device_id, device_type in devices.items():
        levels = {c.name: rng.uniform(*c.level) for c in PROFILES[device_type]}
        cols: dict[str, np.ndarray] = {}
        for c in PROFILES[device_type]:
            mean = levels[c.name] * (1 + c.amplitude * peak + shared[device_type])
            cols[c.name] = (
                rng.poisson(np.maximum(mean, 0)).astype(float)
                if c.counts
                else mean * (1 + c.cv * rng.normal(0, 1, STEPS))
            )
        events = []
        for i in range(STEPS):
            t = float(i * CADENCE_S)
            events.append(
                Event(
                    device_id,
                    i,
                    str(uuid.uuid5(uuid.NAMESPACE_URL, f"dq/{seed}/{device_id}/{i}")),
                    t,
                    t + 0.25,
                    {name: round(float(col[i]), 4) for name, col in cols.items()},
                )  # fmt: skip
            )
        streams[device_id] = events
    return streams


def _free(busy: dict[str, list[tuple[int, int]]], device: str, start: int, stop: int) -> bool:
    return all(stop + 20 < b0 or start > b1 + 20 for b0, b1 in busy.get(device, []))


def inject(
    seed: int, streams: dict[str, list[Event]], devices: dict[str, str], per_class: int = 4
) -> tuple[list[Event], list[Fault]]:
    """Inject `per_class` faults of each class; return every delivered event in arrival order, and the truth."""
    rng = np.random.default_rng(seed + 7_000)
    ids = sorted(streams)
    busy: dict[str, list[tuple[int, int]]] = {}
    faults: list[Fault] = []
    events = {d: [e.copy() for e in evs] for d, evs in streams.items()}
    drop: set[tuple[str, int]] = set()
    extra: list[Event] = []
    for fault_class in CLASSES:
        placed = 0
        while placed < per_class:
            device = ids[int(rng.integers(len(ids)))]
            length = {
                "missing": int(rng.integers(2, 16)),
                "stuck": int(rng.integers(10, 40)),
                "drift": int(rng.integers(40, 100)),
                "duplicate": int(rng.integers(3, 9)),
                "out_of_order": int(rng.integers(3, 9)),
            }[fault_class]
            start = int(rng.integers(FIRST_FAULT_STEP, STEPS - length - 30))
            stop = start + length
            if not _free(busy, device, start, stop):
                continue
            busy.setdefault(device, []).append(
                (start, stop + (40 if fault_class == "drift" else 0))
            )
            evs = events[device]
            detail: dict = {"steps": length}
            if fault_class == "missing":
                drop.update((device, i) for i in range(start, stop))
            elif fault_class == "stuck":
                frozen = dict(evs[start].values)
                for i in range(start, stop):
                    evs[i].values = dict(frozen)
            elif fault_class == "drift":
                profile = PROFILES[devices[device]]
                channel = profile[int(rng.integers(len(profile)))]
                sign = 1 if rng.random() < 0.5 else -1
                magnitude = float(rng.uniform(0.15, 0.5))  # final relative offset
                detail |= {"channel": channel.name, "relative_offset": round(sign * magnitude, 3)}
                for i in range(start, stop):
                    ramp = (i - start + 1) / length
                    evs[i].values[channel.name] = round(
                        evs[i].values[channel.name] * (1 + sign * magnitude * ramp), 4
                    )
                for i in range(
                    stop, min(stop + 40, STEPS)
                ):  # the error persists after the ramp, a real miscalibration does not heal
                    evs[i].values[channel.name] = round(
                        evs[i].values[channel.name] * (1 + sign * magnitude), 4
                    )
                stop += 40
            elif fault_class == "duplicate":
                for i in range(start, stop):
                    extra.append(evs[i].copy(arrival=evs[i].arrival + 1.0))
            else:  # out_of_order: each affected event is delivered after the next one
                for i in range(start, stop, 2):
                    evs[i].arrival += 1.5 * CADENCE_S
            faults.append(
                Fault(
                    device,
                    fault_class,
                    evs[start].observation_time,
                    evs[min(stop, STEPS - 1)].observation_time,
                    detail,
                )
            )
            placed += 1
    delivered = [e for d in ids for e in events[d] if (d, e.sequence_number) not in drop] + extra
    delivered.sort(key=lambda e: (e.arrival, e.device_id, e.sequence_number))
    return delivered, sorted(faults, key=lambda f: (f.onset, f.device_id))


def scenario(seed: int, per_class: int = 4) -> tuple[list[Event], list[Fault], dict[str, str]]:
    devices = load_devices()
    events, faults = inject(seed, clean_streams(seed, devices), devices, per_class)
    return events, faults, devices
