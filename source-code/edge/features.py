"""P04.01: past-only feature extraction with explicit missing/stale handling
and per-vector lineage.

Contract with callers:

- `FeatureBuilder.build(device_id, as_of)` is a pure function of the accepted
  observations with `observation_time <= as_of`, for the device itself and
  for its corridor neighbors. Anything later is ignored, so features can
  never see the future (asserted by tests, including a future-event
  injection test).
- Only validated, fresh events are added (`update` takes a `Verdict`); masked
  (invalid/suspect/low-confidence) measurements never become feature inputs.
- Missing intervals are never invented: a missing slot is counted, the
  vector is marked `degraded`, and too few present intervals returns
  quality `insufficient` with no vector so the runtime abstains instead of
  guessing.
- Loop-detector `mean_speed` is structurally absent when no vehicle crossed
  the loop (count == 0). That is not sensor failure; speed features for
  such intervals are imputed (bounded carry-forward, else a free-flow
  default), and the imputed fraction is itself a feature and lineage field.
- Corridor context (features v2): the edge site covers a corridor, so it
  also sees the loops immediately upstream/downstream of each detector.
  A blocked segment starves its downstream loop of flow before its own queue
  ever reaches its own loop - a signal a single-detector rule cannot use. A
  neighbor that is absent from the topology (corridor end) or has no usable
  window contributes zeros with `*_available = 0`; a neighbor that IS in the
  topology but is unusable marks the vector `degraded`. Neighbor event ids
  are recorded in lineage separately from the device's own.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from datetime import datetime

from edge.timeutil import parse_ts
from edge.topology import Neighbors
from edge.validation import Verdict

FEATURE_VERSION = "loop-window/2"
OWN_FEATURE_NAMES: tuple[str, ...] = (
    "count_last",
    "count_mean",
    "count_max",
    "count_delta",
    "occ_last",
    "occ_mean",
    "occ_max",
    "occ_delta",
    "speed_last",
    "speed_mean",
    "speed_min",
    "speed_delta",
    "zero_flow_run",
    "high_occ_run",
    "missing_fraction",
    "speed_imputed_fraction",
)
CONTEXT_FEATURE_NAMES: tuple[str, ...] = (
    "down_available",
    "down_count_mean",
    "down_occ_last",
    "down_zero_flow_run",
    "count_gap_down",
    "up_available",
    "up_count_mean",
    "up_occ_last",
)
FEATURE_NAMES: tuple[str, ...] = OWN_FEATURE_NAMES + CONTEXT_FEATURE_NAMES
LOOP_EVENT_PREFIX = "traffic.loop_detector."


@dataclass(frozen=True)
class FeatureConfig:
    interval_s: float = 30.0
    window_intervals: int = 4
    min_intervals: int = 2
    free_flow_speed_m_s: float = 13.9
    high_occ_threshold: float = 0.4
    max_speed_carry_intervals: int = 4
    history_capacity: int = 32


@dataclass(frozen=True)
class FeatureResult:
    device_id: str
    as_of: datetime
    quality: str  # "ok" | "degraded" | "insufficient"
    names: tuple[str, ...]
    values: tuple[float, ...] | None
    feature_version: str
    source_event_ids: tuple[str, ...]
    window_start: datetime
    window_end: datetime
    missing_intervals: int
    imputed: tuple[str, ...] = ()
    context_event_ids: dict[str, tuple[str, ...]] = field(default_factory=dict)
    context_missing: tuple[str, ...] = ()


@dataclass(frozen=True, order=True)
class _Obs:
    obs_time: datetime
    event_id: str = field(compare=False)
    count: float | None = field(compare=False)
    occupancy: float | None = field(compare=False)
    speed: float | None = field(compare=False)


@dataclass
class _Window:
    slots: list[int]
    by_slot: dict[int, _Obs]
    start_slot: int
    end_slot: int
    missing: int


class FeatureBuilder:
    def __init__(
        self,
        config: FeatureConfig | None = None,
        topology: dict[str, Neighbors] | None = None,
    ) -> None:
        self._cfg = config or FeatureConfig()
        self._topology = topology or {}
        self._history: dict[str, list[_Obs]] = {}
        self.skipped_not_fresh = 0

    def update(self, verdict: Verdict) -> bool:
        """Add a validated loop event. Returns True if it entered a window."""
        if not verdict.usable or verdict.event is None or verdict.measurements is None:
            return False
        event = verdict.event
        if not event["event_type"].startswith(LOOP_EVENT_PREFIX):
            return False
        if not verdict.fresh:
            self.skipped_not_fresh += 1
            return False
        m = verdict.measurements
        obs = _Obs(
            obs_time=parse_ts(event["observation_time"]),
            event_id=event["event_id"],
            count=float(m["vehicle_count"]) if "vehicle_count" in m else None,
            occupancy=float(m["occupancy"]) if "occupancy" in m else None,
            speed=float(m["mean_speed"]) if "mean_speed" in m else None,
        )
        history = self._history.setdefault(event["device_id"], [])
        bisect.insort(history, obs)
        del history[: max(0, len(history) - self._cfg.history_capacity)]
        return True

    def _slot(self, when: datetime) -> int:
        return round(when.timestamp() / self._cfg.interval_s)

    def _window(self, device_id: str, as_of: datetime) -> _Window:
        cfg = self._cfg
        end_slot = math.floor(as_of.timestamp() / cfg.interval_s)
        start_slot = end_slot - cfg.window_intervals + 1
        by_slot: dict[int, _Obs] = {}
        for obs in self._history.get(device_id, []):
            if obs.obs_time > as_of:  # past-only: never look ahead
                continue
            slot = self._slot(obs.obs_time)
            if start_slot <= slot <= end_slot:
                by_slot[slot] = obs  # later duplicate slot wins (sorted by time)
        slots = [s for s in range(start_slot, end_slot + 1) if s in by_slot]
        return _Window(slots, by_slot, start_slot, end_slot, cfg.window_intervals - len(slots))

    @staticmethod
    def _trailing_run(window: _Window, values: list[float | None], predicate) -> int:
        run, expected = 0, window.end_slot
        for slot, value in zip(reversed(window.slots), reversed(values), strict=True):
            if slot != expected or value is None or not predicate(value):
                break
            run += 1
            expected -= 1
        return run

    def _neighbor(self, device_id: str | None, as_of: datetime) -> dict | None:
        """Summary of a neighbor's own window, or None when it is unusable."""
        if device_id is None:
            return None
        window = self._window(device_id, as_of)
        if len(window.slots) < self._cfg.min_intervals:
            return None
        counts = [window.by_slot[s].count for s in window.slots]
        occs = [window.by_slot[s].occupancy for s in window.slots]
        c_vals = [c for c in counts if c is not None]
        o_vals = [o for o in occs if o is not None]
        if not c_vals or not o_vals:
            return None
        return {
            "count_mean": sum(c_vals) / len(c_vals),
            "occ_last": o_vals[-1],
            "zero_flow_run": float(self._trailing_run(window, counts, lambda v: v == 0)),
            "event_ids": tuple(window.by_slot[s].event_id for s in window.slots),
        }

    def build(self, device_id: str, as_of: datetime) -> FeatureResult:
        cfg = self._cfg
        window = self._window(device_id, as_of)
        window_start = datetime.fromtimestamp(
            (window.start_slot - 1) * cfg.interval_s, tz=as_of.tzinfo
        )
        source_ids = tuple(window.by_slot[s].event_id for s in window.slots)

        def insufficient() -> FeatureResult:
            return FeatureResult(
                device_id=device_id,
                as_of=as_of,
                quality="insufficient",
                names=FEATURE_NAMES,
                values=None,
                feature_version=FEATURE_VERSION,
                source_event_ids=source_ids,
                window_start=window_start,
                window_end=as_of,
                missing_intervals=window.missing,
            )

        if len(window.slots) < cfg.min_intervals:
            return insufficient()

        by_slot, slots = window.by_slot, window.slots
        counts = [by_slot[s].count for s in slots]
        occs = [by_slot[s].occupancy for s in slots]
        c_vals = [c for c in counts if c is not None]
        o_vals = [o for o in occs if o is not None]
        if not c_vals or not o_vals:
            return insufficient()

        speed_pairs = [
            (s, by_slot[s].speed)
            for s in slots
            if by_slot[s].speed is not None and (by_slot[s].count or 0) > 0
        ]
        valid_speeds = [v for _, v in speed_pairs]
        imputed: list[str] = []
        if valid_speeds:
            last_slot, last_speed = speed_pairs[-1]
            if window.end_slot - last_slot <= cfg.max_speed_carry_intervals:
                speed_last = last_speed
            else:
                speed_last = cfg.free_flow_speed_m_s
                imputed.append("speed_last")
            speed_mean = sum(valid_speeds) / len(valid_speeds)
            speed_min = min(valid_speeds)
            speed_delta = (
                last_speed - sum(valid_speeds[:-1]) / len(valid_speeds[:-1])
                if len(valid_speeds) > 1
                else 0.0
            )
        else:
            speed_last = speed_mean = speed_min = cfg.free_flow_speed_m_s
            speed_delta = 0.0
            imputed.extend(["speed_last", "speed_mean", "speed_min"])
        speed_imputed_fraction = 1.0 - len(valid_speeds) / len(slots)

        c_mean = sum(c_vals) / len(c_vals)
        o_mean = sum(o_vals) / len(o_vals)
        c_prev, o_prev = c_vals[:-1], o_vals[:-1]

        neighbors = self._topology.get(device_id, Neighbors(None, None))
        down = self._neighbor(neighbors.downstream, as_of)
        up = self._neighbor(neighbors.upstream, as_of)
        context_missing = tuple(
            role
            for role, expected, got in (
                ("downstream", neighbors.downstream, down),
                ("upstream", neighbors.upstream, up),
            )
            if expected is not None and got is None
        )
        context_ids = {
            role: got["event_ids"]
            for role, got in (("downstream", down), ("upstream", up))
            if got is not None
        }

        own = (
            c_vals[-1],
            c_mean,
            max(c_vals),
            c_vals[-1] - (sum(c_prev) / len(c_prev)) if c_prev else 0.0,
            o_vals[-1],
            o_mean,
            max(o_vals),
            o_vals[-1] - (sum(o_prev) / len(o_prev)) if o_prev else 0.0,
            speed_last,
            speed_mean,
            speed_min,
            speed_delta,
            float(self._trailing_run(window, counts, lambda v: v == 0)),
            float(self._trailing_run(window, occs, lambda v: v >= cfg.high_occ_threshold)),
            window.missing / cfg.window_intervals,
            speed_imputed_fraction,
        )
        context = (
            1.0 if down else 0.0,
            down["count_mean"] if down else 0.0,
            down["occ_last"] if down else 0.0,
            down["zero_flow_run"] if down else 0.0,
            (c_mean - down["count_mean"]) if down else 0.0,
            1.0 if up else 0.0,
            up["count_mean"] if up else 0.0,
            up["occ_last"] if up else 0.0,
        )
        degraded = bool(window.missing or context_missing)
        return FeatureResult(
            device_id=device_id,
            as_of=as_of,
            quality="degraded" if degraded else "ok",
            names=FEATURE_NAMES,
            values=tuple(float(v) for v in own + context),
            feature_version=FEATURE_VERSION,
            source_event_ids=source_ids,
            window_start=window_start,
            window_end=as_of,
            missing_intervals=window.missing,
            imputed=tuple(imputed),
            context_event_ids=context_ids,
            context_missing=context_missing,
        )
