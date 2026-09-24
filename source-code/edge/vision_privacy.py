"""P04.06: privacy-preserving track/vision metadata path.

Raw per-object tracks (vehicle/cyclist/pedestrian positions over time, as an
edge camera or LiDAR would produce) never leave this module and are never
retained beyond one aggregation window. What is exported is
`contracts/observation-envelope/v1`-shaped aggregate metadata: counts, mean
speed, class - never a track_id, a raw (x, y) point, or a face/identity
field (docs/SECURITY_COMPLIANCE_BASELINE.md #10: "aggregation,
pseudonymization, privacy zones").

Three structural guarantees, each enforced in code and pinned by tests, not
just documented:

1. **Privacy zones**: any track point inside a configured zone is dropped
   before it reaches aggregation. That area is invisible to this pipeline,
   not merely "anonymized" - there is no code path that lets a suppressed
   point influence any output, in that window or any other.
2. **No persistent identity**: a track's local id is rehashed every window
   from a boot-random, never-exported salt (`hmac_sha256(salt, window:id)`).
   The same physical object gets an unrelated id in the next window, and the
   raw id/salt never appear in any emitted event.
3. **k-anonymity floor**: an aggregate is emitted only when at least
   `min_cohort` distinct (rehashed) objects contributed to it in that
   window. A window with fewer is suppressed entirely, so a single
   pedestrian is never individually reported.

No face/plate recognition, no identity field, no raw frame or trajectory
storage exists in this module at all - there is nothing to disable, because
it was never implemented (AGENTS.md: "Do not implement facial
recognition... covert identity tracking").
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from edge.timeutil import format_ts

AGGREGATE_EVENT_TYPE = "vision.edge_camera.aggregate"
SUPPRESSED_EVENT_TYPE = "vision.edge_camera.window_suppressed"
DEFAULT_MIN_COHORT = 3
DEFAULT_WINDOW_S = 10.0
EVENT_NAMESPACE = uuid.UUID("6f6f6f6f-0406-4a4a-8a8a-202609190006")


@dataclass(frozen=True)
class PrivacyZone:
    """A circular region where tracking is fully suppressed, e.g. a
    building entrance or accessible-crossing waiting area
    (docs/SENSOR_AND_DATA_CATALOG.md #2: "never store disability identity")."""

    zone_id: str
    center_x: float
    center_y: float
    radius_m: float

    def contains(self, x: float, y: float) -> bool:
        return math.hypot(x - self.center_x, y - self.center_y) <= self.radius_m


@dataclass(frozen=True)
class TrackPoint:
    """One raw detection. Ephemeral by construction: nothing outside this
    module holds a reference to a TrackPoint or its `track_id`."""

    track_id: str
    device_id: str
    object_class: str  # vehicle | cyclist | pedestrian
    timestamp: datetime
    x: float
    y: float
    speed_m_s: float


@dataclass(frozen=True)
class SuppressedPoint:
    reason: str  # "privacy_zone"
    zone_id: str


@dataclass
class _Cell:
    window_start: datetime
    ephemeral_ids: set[str] = field(default_factory=set)
    speeds: list[float] = field(default_factory=list)


class PrivacyGate:
    """Applies zone suppression and per-window identity rehashing. A
    standalone, independently testable step so "does a zone actually
    suppress" and "does identity actually rotate" can be proven without the
    aggregator in between."""

    def __init__(
        self, zones: list[PrivacyZone], window_s: float, salt: bytes | None = None
    ) -> None:
        self.zones = zones
        self.window_s = window_s
        self._salt = salt if salt is not None else os.urandom(32)  # never exported

    def zone_for(self, x: float, y: float) -> PrivacyZone | None:
        for zone in self.zones:
            if zone.contains(x, y):
                return zone
        return None

    def window_index(self, timestamp: datetime) -> int:
        return math.floor(timestamp.timestamp() / self.window_s)

    def ephemeral_id(self, track_id: str, window_index: int) -> str:
        message = f"{window_index}:{track_id}".encode()
        return hmac.new(self._salt, message, hashlib.sha256).hexdigest()[:16]

    def admit(self, point: TrackPoint) -> str | SuppressedPoint:
        """Returns the ephemeral id, or a SuppressedPoint if a zone applies."""
        zone = self.zone_for(point.x, point.y)
        if zone is not None:
            return SuppressedPoint("privacy_zone", zone.zone_id)
        return self.ephemeral_id(point.track_id, self.window_index(point.timestamp))


@dataclass(frozen=True)
class WindowAggregate:
    device_id: str
    object_class: str
    window_start: datetime
    window_end: datetime
    count: int
    mean_speed_m_s: float
    suppressed_privacy_zone_points: int


@dataclass(frozen=True)
class SuppressedWindow:
    device_id: str
    object_class: str
    window_start: datetime
    window_end: datetime
    reason: str  # "below_k_anonymity_floor"
    below_threshold_count: int


class VisionPrivacyAggregator:
    def __init__(
        self,
        gate: PrivacyGate,
        min_cohort: int = DEFAULT_MIN_COHORT,
    ) -> None:
        self.gate = gate
        self.min_cohort = min_cohort
        self._cells: dict[tuple[str, str, int], _Cell] = {}
        self.zone_suppressed_count = 0
        self.cohort_suppressed_windows = 0

    def ingest(self, point: TrackPoint) -> str | SuppressedPoint:
        """Feed one raw detection. Never stores the point itself."""
        admitted = self.gate.admit(point)
        if isinstance(admitted, SuppressedPoint):
            self.zone_suppressed_count += 1
            return admitted
        window_index = self.gate.window_index(point.timestamp)
        key = (point.device_id, point.object_class, window_index)
        window_start = datetime.fromtimestamp(
            window_index * self.gate.window_s, tz=point.timestamp.tzinfo
        )
        cell = self._cells.setdefault(key, _Cell(window_start))
        cell.ephemeral_ids.add(admitted)
        cell.speeds.append(point.speed_m_s)
        return admitted

    def close_window(
        self, device_id: str, object_class: str, window_index: int
    ) -> WindowAggregate | SuppressedWindow | None:
        """Finalize and evict one window's cell (bounded memory: a cell
        lives only until it is closed)."""
        key = (device_id, object_class, window_index)
        cell = self._cells.pop(key, None)
        if cell is None:
            return None
        window_end = cell.window_start + timedelta(seconds=self.gate.window_s)
        count = len(cell.ephemeral_ids)
        if count < self.min_cohort:
            self.cohort_suppressed_windows += 1
            return SuppressedWindow(
                device_id, object_class, cell.window_start, window_end,
                "below_k_anonymity_floor", count,
            )  # fmt: skip
        return WindowAggregate(
            device_id, object_class, cell.window_start, window_end,
            count, sum(cell.speeds) / len(cell.speeds), 0,
        )  # fmt: skip

    def open_window_keys(self) -> list[tuple[str, str, int]]:
        return sorted(self._cells)


def close_due_windows(
    aggregator: VisionPrivacyAggregator, now: datetime
) -> list[WindowAggregate | SuppressedWindow]:
    """Close every window whose end has passed `now`."""
    results = []
    for device_id, object_class, window_index in aggregator.open_window_keys():
        window_end = (window_index + 1) * aggregator.gate.window_s
        if window_end <= now.timestamp():
            result = aggregator.close_window(device_id, object_class, window_index)
            if result is not None:
                results.append(result)
    return results


def to_observation_event(
    result: WindowAggregate,
    device: dict,
    run_id: str,
    sequence_number: int,
    geometry_version: str,
    pipeline_version: str,
) -> dict:
    """A privacy-passed aggregate as an observation-envelope/v1 event. No
    track_id, no raw position, no per-object field exists to omit."""
    location = device["location"]
    return {
        "schema_version": "1.0.0",
        "event_id": str(
            uuid.uuid5(EVENT_NAMESPACE, f"{run_id}:{device['device_id']}:{sequence_number}")
        ),
        "event_type": AGGREGATE_EVENT_TYPE,
        "device_id": device["device_id"],
        "agency_scope": device["agency_scope"],
        "observation_time": format_ts(result.window_end),
        "ingest_time": format_ts(result.window_end + timedelta(milliseconds=250)),
        "sequence_number": sequence_number,
        "clock_quality": "synced",
        "geometry_version": geometry_version,
        "location": {
            "coordinate_reference": "EPSG:4326",
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            **({"lane_id": location["lane_id"]} if "lane_id" in location else {}),
        },
        "measurements": [
            {
                "name": f"count_{result.object_class}",
                "value": result.count,
                "unit": "count",
                "quality": "valid",
                "confidence": 0.9,
            },
            {
                "name": "mean_speed",
                "value": round(result.mean_speed_m_s, 3),
                "unit": "m_s-1",
                "quality": "valid",
                "confidence": 0.85,
            },
        ],
        "truth_label": "simulated",
        "privacy_classification": "aggregated",
        "retention_class": "short",
        "correlation_id": f"vision-{run_id}",
        "provenance": {"producer": "edge-vision-privacy", "pipeline_version": pipeline_version},
    }
