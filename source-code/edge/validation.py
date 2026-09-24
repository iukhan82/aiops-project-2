"""P04.01: edge intake validation.

Validates every inbound observation-envelope/v1 event before anything else
(features, inference, outbox) sees it. Behavior is explicit and
fail-safe:

- Structurally or semantically invalid input is REJECTED with stable reason
  codes and never reaches feature extraction.
- Valid-but-degraded input (stale, clock not synced, sequence gap, low
  confidence, invalid/suspect measurement) is ACCEPTED WITH FLAGS; flagged
  measurements are masked out of `Verdict.measurements` rather than trusted,
  and `Verdict.fresh` is False for stale events so nothing computes a
  "current" decision from them.
- Duplicate and out-of-order events are rejected by per-device sequence and
  a bounded event_id memory (bounded state: edge memory budget is 512 MiB).

Never raises on hostile input; every failure mode is a Verdict.
"""

from __future__ import annotations

import json
import uuid
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from edge.contracts import load_validator
from edge.timeutil import parse_ts

# event_type prefix -> device_type(s) allowed to emit it. `platform.` events
# come from any device (device health is reported about/by any device).
EVENT_TYPE_DEVICE_TYPES: dict[str, frozenset[str]] = {
    "traffic.loop_detector.": frozenset({"inductive_loop"}),
    "vru.cycle_counter.": frozenset({"cycle_counter"}),
    "vru.crossing_detector.": frozenset({"crossing_detector"}),
    "signal.controller.": frozenset({"signal_controller"}),
    "weather.station.": frozenset({"weather_station"}),
    "road.condition_sensor.": frozenset({"road_condition_sensor"}),
    "emergency.unit_position.": frozenset({"emergency_cad_avl_adapter"}),
}
ANY_DEVICE_EVENT_PREFIXES = ("platform.",)


# Stable reason/flag vocabularies: also the allow-lists for bounded metric labels.
REJECT_REASONS: tuple[str, ...] = (
    "not_object",
    "schema_invalid",
    "bad_event_id",
    "bad_timestamp",
    "future_timestamp",
    "unknown_device",
    "device_inactive",
    "agency_mismatch",
    "identity_mismatch",
    "event_type_device_mismatch",
    "geometry_version_mismatch",
    "truth_label_not_allowed",
    "duplicate_event_id",
    "sequence_conflict",
    "out_of_order_sequence",
)
FLAG_STEMS: tuple[str, ...] = (
    "sequence_gap",
    "time_regression",
    "stale",
    "ingest_lag_high",
    "ingest_before_observation",
    "clock_not_synced",
    "measurement_invalid",
    "measurement_suspect",
    "low_confidence",
)


class Status(str, Enum):
    ACCEPTED = "accepted"
    FLAGGED = "accepted_with_flags"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Verdict:
    status: Status
    reasons: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    detail: str = ""
    event: dict | None = None
    fresh: bool = False
    measurements: dict[str, float | str | bool] | None = None

    @property
    def usable(self) -> bool:
        return self.status is not Status.REJECTED


@dataclass(frozen=True)
class ValidationConfig:
    expected_geometry_version: str
    max_future_skew_s: float = 5.0
    max_staleness_s: float = 120.0
    max_ingest_lag_s: float = 30.0
    min_measurement_confidence: float = 0.5
    seen_event_id_capacity: int = 4096
    allowed_truth_labels: frozenset[str] = frozenset(
        {"simulated", "measured", "operator_entered", "verified"}
    )


class DeviceRegistry:
    """device/v1 records keyed by device_id; invalid records fail fast."""

    def __init__(self, devices: Iterable[dict]) -> None:
        validator = load_validator("device")
        self._devices: dict[str, dict] = {}
        for device in devices:
            validator.validate(device)
            self._devices[device["device_id"]] = device

    @classmethod
    def from_jsonl(cls, path: Path) -> DeviceRegistry:
        lines = path.read_text(encoding="utf-8").splitlines()
        return cls(json.loads(line) for line in lines if line)

    def get(self, device_id: str) -> dict | None:
        return self._devices.get(device_id)

    def all(self) -> list[dict]:
        return [self._devices[k] for k in sorted(self._devices)]

    def __len__(self) -> int:
        return len(self._devices)


@dataclass
class _DeviceState:
    last_seq: int = -1
    last_obs: datetime | None = None
    last_event_id: str | None = None


class EdgeValidator:
    def __init__(self, registry: DeviceRegistry, config: ValidationConfig) -> None:
        self._registry = registry
        self._config = config
        self._schema = load_validator("observation-envelope")
        self._state: dict[str, _DeviceState] = {}
        self._seen_ids: OrderedDict[str, None] = OrderedDict()

    # -- helpers ---------------------------------------------------------

    def _remember(self, event_id: str) -> None:
        self._seen_ids[event_id] = None
        self._seen_ids.move_to_end(event_id)
        while len(self._seen_ids) > self._config.seen_event_id_capacity:
            self._seen_ids.popitem(last=False)

    @staticmethod
    def _reject(code: str, detail: str = "") -> Verdict:
        return Verdict(status=Status.REJECTED, reasons=(code,), detail=detail[:200])

    def _device_type_ok(self, event_type: str, device_type: str) -> bool:
        if event_type.startswith(ANY_DEVICE_EVENT_PREFIXES):
            return True
        for prefix, allowed in EVENT_TYPE_DEVICE_TYPES.items():
            if event_type.startswith(prefix):
                return device_type in allowed
        return False

    # -- public ----------------------------------------------------------

    def validate(
        self,
        raw: object,
        now: datetime,
        transport_identity: str | None = None,
    ) -> Verdict:
        cfg = self._config
        if not isinstance(raw, dict):
            return self._reject("not_object")

        errors = sorted(
            self._schema.iter_errors(raw), key=lambda e: [str(part) for part in e.absolute_path]
        )
        if errors:
            first = errors[0]
            where = ".".join(str(p) for p in first.absolute_path) or "$"
            return self._reject("schema_invalid", f"{where}: {first.message}")

        event_id = raw["event_id"]
        try:
            uuid.UUID(event_id)
        except (ValueError, AttributeError, TypeError):
            return self._reject("bad_event_id")

        try:
            observed = parse_ts(raw["observation_time"])
            ingested = parse_ts(raw["ingest_time"])
        except (ValueError, TypeError):
            return self._reject("bad_timestamp")

        if observed > now + timedelta(seconds=cfg.max_future_skew_s):
            return self._reject("future_timestamp")

        device_id = raw["device_id"]
        device = self._registry.get(device_id)
        if device is None:
            return self._reject("unknown_device")
        if device["status"] != "active":
            return self._reject("device_inactive")
        if device["agency_scope"] != raw["agency_scope"]:
            return self._reject("agency_mismatch")
        if transport_identity is not None and transport_identity != device_id:
            return self._reject("identity_mismatch")
        if not self._device_type_ok(raw["event_type"], device["device_type"]):
            return self._reject("event_type_device_mismatch")
        if raw["geometry_version"] != cfg.expected_geometry_version:
            return self._reject("geometry_version_mismatch")
        if raw["truth_label"] not in cfg.allowed_truth_labels:
            return self._reject("truth_label_not_allowed")

        if event_id in self._seen_ids:
            return self._reject("duplicate_event_id")

        state = self._state.setdefault(device_id, _DeviceState())
        seq = raw["sequence_number"]
        flags: list[str] = []
        if state.last_seq >= 0:
            if seq == state.last_seq:
                return self._reject("sequence_conflict")
            if seq < state.last_seq:
                return self._reject("out_of_order_sequence")
            if seq > state.last_seq + 1:
                flags.append(f"sequence_gap:{seq - state.last_seq - 1}")
        if state.last_obs is not None and observed < state.last_obs:
            flags.append("time_regression")

        age = (now - observed).total_seconds()
        fresh = age <= cfg.max_staleness_s
        if not fresh:
            flags.append("stale")
        if (ingested - observed).total_seconds() > cfg.max_ingest_lag_s:
            flags.append("ingest_lag_high")
        if ingested < observed - timedelta(seconds=cfg.max_future_skew_s):
            flags.append("ingest_before_observation")
        if raw["clock_quality"] != "synced":
            flags.append("clock_not_synced")
            fresh = False

        usable: dict[str, float | str | bool] = {}
        for measurement in raw["measurements"]:
            name = measurement["name"]
            if measurement["quality"] == "invalid":
                flags.append(f"measurement_invalid:{name}")
                continue
            if measurement["quality"] == "suspect":
                flags.append(f"measurement_suspect:{name}")
                continue
            if measurement["confidence"] < cfg.min_measurement_confidence:
                flags.append(f"low_confidence:{name}")
                continue
            usable[name] = measurement["value"]

        state.last_seq = seq
        state.last_obs = observed if state.last_obs is None else max(state.last_obs, observed)
        state.last_event_id = event_id
        self._remember(event_id)

        status = Status.FLAGGED if flags else Status.ACCEPTED
        return Verdict(
            status=status,
            flags=tuple(flags),
            event=raw,
            fresh=fresh,
            measurements=usable,
        )
