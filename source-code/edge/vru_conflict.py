"""P06.06: pedestrian/cyclist-vs-vehicle conflict indicators from anonymous
tracks, with the same privacy guarantees as P04.06.

Input is what an edge tracker would produce for one intersection site: a stream
of `TrackSample` (frame time, a *local* track id, object class, x, y). Nothing
here needs, stores or emits an identity, a face, a plate or a raw trajectory.

Indicator. At every frame, for each (VRU, vehicle) pair within range, velocities
are estimated from the last few frames, both objects are projected forward, and
if the projected paths cross the pair's *predicted post-encroachment time*
(`pet_pred` = |time the VRU reaches the crossing point - time the vehicle does|)
is compared with thresholds. It is deliberately the same quantity the truth
side measures after the fact (realized PET), so prediction error, not a change
of definition, is what the evaluation measures. Two vehicle motion models:
`constant_velocity` and `constant_turn_rate` (arc continuing the recent yaw
rate - needed because most crossing conflicts involve turning vehicles); both
optionally with the vehicle's current acceleration (a driver already braking
for the crossing is not a conflict).

Most geometric "the paths cross" candidates never become a near miss (people
turn a corner, a driver yields). A small logistic `PairScorer` over causal
features of the candidate (predicted PET, time to the crossing point, speeds,
braking, crossing angle, distance) turns the geometry into a probability that
the encounter really ends with PET <= 3 s; the detector fires at the first frame
where it clears a threshold chosen on validation data.

Privacy structure (each enforced in code and pinned by tests):
1. privacy zones: a sample inside a zone is dropped before any computation, so
   nothing inside can create or influence a conflict (`PrivacyGate.admit`);
2. no persistent identity: pair/track ids leave the tracker only as per-window
   HMAC-rehashed ids used to count distinct VRUs; the raw id and the salt never
   appear in any emitted record;
3. k-anonymity floor: a (site, mode, window) is exported only when at least
   `min_cohort` distinct VRUs were seen in it, and what is exported is counts
   and a minimum PET - never a track, a position, a time finer than the window
   or an individual speed. Below the floor the whole window is suppressed.

Consequence, documented rather than hidden: the floor also suppresses real
conflicts at low-volume sites and times - exactly where a lone pedestrian is
most exposed. `docs`/evaluation report the share of true conflicts lost to it.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from edge.timeutil import format_ts
from edge.vision_privacy import PrivacyGate, SuppressedPoint

AGGREGATE_EVENT_TYPE = "vru.conflict.aggregate"
SUPPRESSED_EVENT_TYPE = "vru.conflict.window_suppressed"
EVENT_NAMESPACE = uuid.UUID("6f6f6f6f-0606-4a4a-8a8a-202609200006")
VRU_CLASSES = ("pedestrian", "cyclist")
MODES = ("constant_velocity", "constant_turn_rate")
DEFAULT_WINDOW_S = 300.0
DEFAULT_MIN_COHORT = 3
MAX_TURN_RATE = 0.6  # rad/s; faster than any real passenger-car turn at junction speeds
MAX_BRAKE, MAX_ACCEL = -6.0, 1.5  # m/s^2 clamps for the estimated vehicle acceleration
FEATURE_NAMES = (
    "pet_pred",
    "tau_vehicle",
    "tau_vru",
    "vehicle_speed",
    "vru_speed",
    "vehicle_accel",
    "sin_angle",
    "distance",
    "abs_yaw_rate",
    "is_cyclist",
    "vru_first",
)


@dataclass(frozen=True)
class TrackSample:
    t: float  # seconds on the tracker's clock (frame time)
    track_id: str  # local, ephemeral
    object_class: str  # vehicle | cyclist | pedestrian
    x: float
    y: float


@dataclass(frozen=True)
class ConflictParams:
    pet_pred_s: float = 3.0  # alert when predicted PET is at most this
    severe_pet_s: float = 1.5
    horizon_s: float = 6.0  # both users must reach the crossing point within this
    min_vehicle_speed: float = 2.0
    min_vru_speed: float = 0.3
    smooth_frames: int = 3
    range_m: float = 35.0
    min_sin_angle: float = 0.25  # nearly parallel paths do not "cross"
    vehicle_model: str = "constant_turn_rate"
    use_acceleration: bool = False
    min_score: float = 0.0  # scorer probability needed to fire (ignored without a scorer)


@dataclass
class ConflictEvent:
    """A conflict between one VRU and one vehicle. `vru_track`/`vehicle_track`
    are the tracker's local ids: they exist only inside the tracker and the
    test harness, and are dropped by the aggregator."""

    site: str
    vru_track: str
    vehicle_track: str
    mode: str  # pedestrian | cyclist
    first_alert_t: float
    min_pet_s: float
    time_to_conflict_s: float  # at the first alert
    vehicle_speed_m_s: float
    alerts: int = 1
    score: float = 1.0

    @property
    def severity(self) -> str:
        return "severe" if self.min_pet_s <= 1.5 else "serious"


def _velocity(history: deque[tuple[float, float, float]]) -> tuple[float, float] | None:
    """Least-squares velocity over the retained frames."""
    if len(history) < 2:
        return None
    t0 = history[0][0]
    n = len(history)
    mt = sum(h[0] - t0 for h in history) / n
    mx = sum(h[1] for h in history) / n
    my = sum(h[2] for h in history) / n
    var = sum((h[0] - t0 - mt) ** 2 for h in history)
    if var < 1e-9:
        return None
    return (
        sum((h[0] - t0 - mt) * (h[1] - mx) for h in history) / var,
        sum((h[0] - t0 - mt) * (h[2] - my) for h in history) / var,
    )


def _yaw_rate(history: deque[tuple[float, float, float]]) -> float:
    """Heading change between the first and last segment of the window."""
    if len(history) < 3:
        return 0.0
    a, b, c = history[0], history[len(history) // 2], history[-1]
    if math.hypot(b[1] - a[1], b[2] - a[2]) < 0.5 or math.hypot(c[1] - b[1], c[2] - b[2]) < 0.5:
        return 0.0
    h1 = math.atan2(b[2] - a[2], b[1] - a[1])
    h2 = math.atan2(c[2] - b[2], c[1] - b[1])
    dh = (h2 - h1 + math.pi) % (2 * math.pi) - math.pi
    dt = (c[0] - a[0]) / 2.0
    return max(-MAX_TURN_RATE, min(MAX_TURN_RATE, dh / dt)) if dt > 0 else 0.0


def _accel(history: deque[tuple[float, float, float]]) -> float:
    """Longitudinal acceleration from the speed of the first vs the last half of the window."""
    if len(history) < 3:
        return 0.0
    a, b, c = history[0], history[len(history) // 2], history[-1]
    d1, d2 = b[0] - a[0], c[0] - b[0]
    if d1 <= 0 or d2 <= 0:
        return 0.0
    v1 = math.hypot(b[1] - a[1], b[2] - a[2]) / d1
    v2 = math.hypot(c[1] - b[1], c[2] - b[2]) / d2
    return max(MAX_BRAKE, min(MAX_ACCEL, (v2 - v1) / ((d1 + d2) / 2.0)))


def _vehicle_path(
    x: float,
    y: float,
    vx: float,
    vy: float,
    omega: float,
    horizon: float,
    accel: float = 0.0,
    dt: float = 0.25,
) -> list[tuple[float, float, float]]:
    """(tau, x, y) along a constant-yaw-rate arc, speed changing at `accel` until it stops."""
    speed = math.hypot(vx, vy)
    heading = math.atan2(vy, vx)
    pts = [(0.0, x, y)]
    tau = 0.0
    while tau < horizon:
        tau += dt
        heading += omega * dt
        new_speed = max(0.0, speed + accel * dt)
        mean = (speed + new_speed) / 2.0
        x += mean * math.cos(heading) * dt
        y += mean * math.sin(heading) * dt
        speed = new_speed
        pts.append((tau, x, y))
    return pts


def predicted_pet(
    vru: tuple[float, float, float, float],
    veh: tuple[float, float, float, float],
    omega: float,
    params: ConflictParams,
    accel: float = 0.0,
) -> tuple[float, float, float] | None:
    """(pet_pred, tau_vehicle, tau_vru) of the earliest predicted path crossing, or None."""
    px, py, pvx, pvy = vru
    x, y, vx, vy = veh
    path = _vehicle_path(x, y, vx, vy, omega, params.horizon_s, accel)
    # VRU straight line, parameterised by its own time tau_p in [-1, horizon]
    ax, ay = px - pvx * 1.0, py - pvy * 1.0
    dx, dy = pvx * (params.horizon_s + 1.0), pvy * (params.horizon_s + 1.0)
    best = None
    for (t1, x1, y1), (t2, x2, y2) in zip(path, path[1:], strict=False):
        ex, ey = x2 - x1, y2 - y1
        den = dx * ey - dy * ex
        if abs(den) < 1e-9:
            continue
        s = ((x1 - ax) * ey - (y1 - ay) * ex) / den  # along the VRU line
        u = ((x1 - ax) * dy - (y1 - ay) * dx) / den  # along the vehicle segment
        if 0.0 <= s <= 1.0 and 0.0 <= u <= 1.0:
            tau_v = t1 + u * (t2 - t1)
            tau_p = -1.0 + s * (params.horizon_s + 1.0)
            pet = abs(tau_v - tau_p)
            if best is None or tau_v < best[1]:
                best = (pet, tau_v, tau_p)
    if best is None:
        return None
    return best


class PairScorer:
    """Logistic model over `FEATURE_NAMES`, stored as plain JSON so an edge
    device needs nothing but arithmetic. Trained offline
    (backend/analytics/evaluate_vru.py); the deployed artifact carries its
    training data digest and thresholds."""

    def __init__(self, weights: dict) -> None:
        self.names: list[str] = weights["features"]
        self.mean: list[float] = weights["mean"]
        self.scale: list[float] = weights["scale"]
        self.coef: list[float] = weights["coef"]
        self.intercept: float = weights["intercept"]

    def predict(self, features: dict[str, float]) -> float:
        z = self.intercept + sum(
            c * (features[n] - m) / sc
            for n, m, sc, c in zip(self.names, self.mean, self.scale, self.coef, strict=True)
        )
        return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


class SiteConflictDetector:
    """Streaming detector for one site. Feed frames in time order."""

    STALE_S = 5.0

    def __init__(
        self,
        site: str,
        params: ConflictParams | None = None,
        scorer: PairScorer | None = None,
        trace: list | None = None,
    ) -> None:
        self.site = site
        self.params = params or ConflictParams()
        self.scorer = scorer
        self.trace = (
            trace  # optional (frame, site, vru, vehicle, features) rows, for offline training only
        )
        self._hist: dict[str, deque[tuple[float, float, float]]] = {}
        self._cls: dict[str, str] = {}
        self._last_seen: dict[str, float] = {}
        self._events: dict[tuple[str, str], ConflictEvent] = {}
        self.exposed: dict[str, set[str]] = defaultdict(set)  # mode -> local VRU ids seen

    def update(self, frame_t: float, samples: Iterable[TrackSample]) -> list[ConflictEvent]:
        """One tracker frame. Returns the events *opened* by this frame."""
        p = self.params
        for s in samples:
            hist = self._hist.setdefault(s.track_id, deque(maxlen=p.smooth_frames))
            if hist and frame_t - hist[-1][0] > 2.5:  # tracker lost it: start over
                hist.clear()
            hist.append((s.t, s.x, s.y))
            self._cls[s.track_id] = s.object_class
            self._last_seen[s.track_id] = frame_t
            if s.object_class in VRU_CLASSES:
                self.exposed[s.object_class].add(s.track_id)
        for stale in [i for i, t in self._last_seen.items() if frame_t - t > self.STALE_S]:
            del self._hist[stale], self._cls[stale], self._last_seen[stale]
        live = [i for i, t in self._last_seen.items() if frame_t - t <= 0.5]
        vrus, vehicles = [], []
        for i in live:
            v = _velocity(self._hist[i])
            if v is None:
                continue
            h = self._hist[i][-1]
            state = (h[1], h[2], v[0], v[1])
            speed = math.hypot(*v)
            if self._cls[i] in VRU_CLASSES:
                if speed >= p.min_vru_speed:
                    vrus.append((i, state, speed))
            elif speed >= p.min_vehicle_speed:
                omega = _yaw_rate(self._hist[i]) if p.vehicle_model == "constant_turn_rate" else 0.0
                accel = _accel(self._hist[i])
                vehicles.append((i, state, omega, accel, speed))
        opened: list[ConflictEvent] = []
        for vru_id, vru, vru_speed in vrus:
            for veh_id, veh, omega, accel, veh_speed in vehicles:
                distance = math.hypot(vru[0] - veh[0], vru[1] - veh[1])
                if distance > p.range_m:
                    continue
                sin_angle = abs(vru[2] * veh[3] - vru[3] * veh[2]) / (vru_speed * veh_speed)
                if sin_angle < p.min_sin_angle:
                    continue
                result = predicted_pet(vru, veh, omega, p, accel if p.use_acceleration else 0.0)
                if result is None or result[0] > p.pet_pred_s:
                    continue
                pet, tau_v, tau_p = result
                features = {
                    "pet_pred": pet,
                    "tau_vehicle": tau_v,
                    "tau_vru": tau_p,
                    "vehicle_speed": veh_speed,
                    "vru_speed": vru_speed,
                    "vehicle_accel": accel,
                    "sin_angle": sin_angle,
                    "distance": distance,
                    "abs_yaw_rate": abs(omega),
                    "is_cyclist": 1.0 if self._cls[vru_id] == "cyclist" else 0.0,
                    "vru_first": 1.0 if tau_p < tau_v else 0.0,
                }
                if self.trace is not None:
                    self.trace.append((frame_t, self.site, vru_id, veh_id, features))
                score = self.scorer.predict(features) if self.scorer is not None else 1.0
                if self.scorer is not None and score < p.min_score:
                    continue
                key = (vru_id, veh_id)
                existing = self._events.get(key)
                if existing is None:
                    event = ConflictEvent(
                        self.site,
                        vru_id,
                        veh_id,
                        self._cls[vru_id],
                        frame_t,
                        pet,
                        tau_v,
                        veh_speed,
                        1,
                        score,
                    )
                    self._events[key] = event
                    opened.append(event)
                else:
                    existing.alerts += 1
                    existing.min_pet_s = min(existing.min_pet_s, pet)
                    existing.score = max(existing.score, score)
        return opened

    @property
    def events(self) -> list[ConflictEvent]:
        return sorted(
            self._events.values(), key=lambda e: (e.first_alert_t, e.vru_track, e.vehicle_track)
        )


def run_site(
    site: str,
    frames: dict[float, list[TrackSample]],
    params: ConflictParams | None = None,
    scorer: PairScorer | None = None,
    trace: list | None = None,
) -> SiteConflictDetector:
    detector = SiteConflictDetector(site, params, scorer, trace)
    for frame_t in sorted(frames):
        detector.update(frame_t, frames[frame_t])
    return detector


@dataclass
class _Cell:
    window_start: datetime
    vru_ids: set[str] = field(default_factory=set)
    counts: dict[str, int] = field(default_factory=lambda: {"serious": 0, "severe": 0})
    min_pet_s: float | None = None


@dataclass(frozen=True)
class ConflictAggregate:
    site: str
    mode: str
    window_start: datetime
    window_end: datetime
    exposure: int
    serious: int
    severe: int
    min_pet_s: float | None


@dataclass(frozen=True)
class SuppressedConflictWindow:
    site: str
    mode: str
    window_start: datetime
    window_end: datetime
    reason: str
    below_threshold_count: int


class ConflictAggregator:
    """Turns per-site conflict events and VRU exposure into privacy-passed
    window aggregates. Keeps only sets of *rehashed* ids and counters."""

    def __init__(
        self, gate: PrivacyGate, anchor: datetime, min_cohort: int = DEFAULT_MIN_COHORT
    ) -> None:
        self.gate = gate
        self.anchor = anchor
        self.min_cohort = min_cohort
        self._cells: dict[tuple[str, str, int], _Cell] = {}
        self.zone_suppressed_samples = 0

    def _window(self, t: float) -> int:
        return math.floor(t / self.gate.window_s)

    def _cell(self, site: str, mode: str, window: int) -> _Cell:
        start = self.anchor + timedelta(seconds=window * self.gate.window_s)
        return self._cells.setdefault((site, mode, window), _Cell(start))

    def admit_sample(self, sample: TrackSample) -> bool:
        """Privacy-zone gate; call before the tracker sample reaches the detector."""
        from edge.vision_privacy import TrackPoint

        point = TrackPoint(
            sample.track_id,
            "",
            sample.object_class,
            self.anchor + timedelta(seconds=sample.t),
            sample.x,
            sample.y,
            0.0,
        )
        if isinstance(self.gate.admit(point), SuppressedPoint):
            self.zone_suppressed_samples += 1
            return False
        return True

    def note_exposure(self, site: str, mode: str, track_id: str, t: float) -> None:
        window = self._window(t)
        self._cell(site, mode, window).vru_ids.add(self.gate.ephemeral_id(track_id, window))

    def note_conflict(self, event: ConflictEvent) -> None:
        window = self._window(event.first_alert_t)
        cell = self._cell(event.site, event.mode, window)
        cell.vru_ids.add(self.gate.ephemeral_id(event.vru_track, window))
        cell.counts[event.severity] += 1
        cell.min_pet_s = (
            event.min_pet_s if cell.min_pet_s is None else min(cell.min_pet_s, event.min_pet_s)
        )

    def close_all(self) -> list[ConflictAggregate | SuppressedConflictWindow]:
        out: list[ConflictAggregate | SuppressedConflictWindow] = []
        for (site, mode, window), cell in sorted(self._cells.items()):
            end = cell.window_start + timedelta(seconds=self.gate.window_s)
            if len(cell.vru_ids) < self.min_cohort:
                out.append(
                    SuppressedConflictWindow(
                        site,
                        mode,
                        cell.window_start,
                        end,
                        "below_k_anonymity_floor",
                        len(cell.vru_ids),
                    )
                )
            else:
                out.append(
                    ConflictAggregate(
                        site,
                        mode,
                        cell.window_start,
                        end,
                        len(cell.vru_ids),
                        cell.counts["serious"],
                        cell.counts["severe"],
                        None if cell.min_pet_s is None else round(cell.min_pet_s, 2),
                    )
                )
        self._cells.clear()
        return out


def to_observation_event(
    result: ConflictAggregate,
    device: dict,
    run_id: str,
    sequence_number: int,
    geometry_version: str,
    pipeline_version: str,
) -> dict:
    """A privacy-passed aggregate as an observation-envelope/v1 event: counts and
    one minimum PET. No track id, no position, no per-object field exists to omit."""
    location = device["location"]
    m = result.mode
    measurements = [
        {
            "name": f"exposure_{m}",
            "value": result.exposure,
            "unit": "count",
            "quality": "valid",
            "confidence": 0.9,
        },
        {
            "name": f"conflicts_serious_{m}",
            "value": result.serious,
            "unit": "count",
            "quality": "valid",
            "confidence": 0.6,
        },
        {
            "name": f"conflicts_severe_{m}",
            "value": result.severe,
            "unit": "count",
            "quality": "valid",
            "confidence": 0.6,
        },
    ]
    if result.min_pet_s is not None:
        measurements.append(
            {
                "name": f"min_predicted_pet_{m}",
                "value": result.min_pet_s,
                "unit": "s",
                "quality": "valid",
                "confidence": 0.6,
            }
        )
    return {
        "schema_version": "1.0.0",
        "event_id": str(
            uuid.uuid5(EVENT_NAMESPACE, f"{run_id}:{device['device_id']}:{m}:{sequence_number}")
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
            "intersection_id": location.get("intersection_id", result.site),
        },
        "measurements": measurements,
        "truth_label": "inferred",
        "privacy_classification": "aggregated",
        "retention_class": "short",
        "correlation_id": f"vru-conflict-{run_id}",
        "provenance": {"producer": "edge-vru-conflict", "pipeline_version": pipeline_version},
    }


def suppression_event(
    result: SuppressedConflictWindow,
    device: dict,
    run_id: str,
    sequence_number: int,
    geometry_version: str,
    pipeline_version: str,
) -> dict:
    location = device["location"]
    return {
        "schema_version": "1.0.0",
        "event_id": str(
            uuid.uuid5(
                EVENT_NAMESPACE,
                f"{run_id}:{device['device_id']}:{result.mode}:suppressed:{sequence_number}",
            )
        ),
        "event_type": SUPPRESSED_EVENT_TYPE,
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
            "intersection_id": location.get("intersection_id", result.site),
        },
        "measurements": [
            {
                "name": f"suppressed_window_{result.mode}",
                "value": True,
                "unit": "boolean",
                "quality": "valid",
                "confidence": 1.0,
            },
            {
                "name": f"cohort_size_{result.mode}",
                "value": result.below_threshold_count,
                "unit": "count",
                "quality": "valid",
                "confidence": 1.0,
            },
        ],
        "truth_label": "inferred",
        "privacy_classification": "aggregated",
        "retention_class": "short",
        "correlation_id": f"vru-conflict-{run_id}",
        "provenance": {"producer": "edge-vru-conflict", "pipeline_version": pipeline_version},
    }
