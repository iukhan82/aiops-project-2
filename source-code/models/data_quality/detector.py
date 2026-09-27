"""Streaming data-quality detector: five transparent rules over observation content, and the tracker that turns their signals into incidents.

Nothing here is learned. Each rule has one or two numbers (`Params`), chosen on the selection seeds by `tune_evaluate.py`, and each says
in plain words why it fired:

    duplicate     an event id (or sequence number) already accepted arrives again
    out_of_order  a sequence number lower than one already accepted arrives (a gap that a late event fills is this, not 'missing')
    missing       the device has been silent for `silence_after_s`, or a sequence gap was not filled within `gap_grace_s`
    stuck         the last `stuck_run` events carry exactly the same values (an all-zero tuple is an empty road, not a frozen sensor)
    drift         a CUSUM on how far a device's channel has moved from its own commissioning level RELATIVE TO ITS PEERS of the same type
                  (peers cancel what everybody shares - the morning peak, the weather); needs at least two peers reporting

Limits that belong to the rules and not to any dataset: a sensor frozen at exactly zero looks like an empty road and is not flagged; the
drift rule needs an hour of fault-free commissioning data per device and two peers; a fault that never changes the events the platform
receives (a sensor wrong from the day it was installed) is invisible to all five.
"""

from __future__ import annotations

import statistics
from collections import defaultdict, deque
from dataclasses import asdict, dataclass

from models.data_quality.streams import CADENCE_S, CALIBRATION_STEPS, Event

BIN_SETTLE_S = 90.0  # a minute's readings are compared once they are this old, so peers that arrive a little late are still counted
MIN_PEERS = 2
SIGMA_FLOOR = 0.005
RIVAL_FACTOR = 1.5
ZBAR_ALPHA = 0.15


@dataclass(frozen=True)
class Params:
    stuck_run: int = 8
    silence_after_s: float = 240.0
    gap_grace_s: float = 150.0
    cusum_k: float = 0.75
    cusum_h: float = 10.0
    merge_gap_s: float = 900.0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Signal:
    device_id: str
    fault_class: str
    at: float
    detail: str


class Detector:
    def __init__(self, params: Params, devices: dict[str, str]):
        self.p = params
        self.devices = devices
        self.peers = {
            d: [o for o, t in devices.items() if t == devices[d] and o != d] for d in devices
        }
        self.level: dict[tuple[str, str], float] = {}
        self.sigma: dict[tuple[str, str], float] = {}
        self.last_seq = dict.fromkeys(devices, -1)
        self.last_arrival = dict.fromkeys(devices, 0.0)
        self.accepted: dict[str, set[int]] = defaultdict(set)
        self.pending: dict[str, dict[int, float]] = defaultdict(dict)
        self.recent: dict[str, deque] = {d: deque(maxlen=params.stuck_run) for d in devices}
        self.bins: dict[int, dict[str, dict[str, float]]] = defaultdict(dict)
        self.next_bin = CALIBRATION_STEPS
        self.cusum: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
        self.zbar: dict[tuple[str, str], float] = defaultdict(
            float
        )  # smoothed standardised deviation: how far the device is NOW

    # ------------------------------------------------------------------------------------------------------ commissioning
    def calibrate(self, events: list[Event]) -> None:
        """Learn each device's channel levels and how far it normally sits from its peers, from fault-free commissioning events."""
        by_device: dict[str, dict[int, Event]] = defaultdict(dict)
        for e in events:
            if e.sequence_number < CALIBRATION_STEPS:
                by_device[e.device_id][e.sequence_number] = e
                self.last_seq[e.device_id] = max(self.last_seq[e.device_id], e.sequence_number)
                self.last_arrival[e.device_id] = max(self.last_arrival[e.device_id], e.arrival)
                self.accepted[e.device_id].add(e.sequence_number)
        for device, evs in by_device.items():
            for channel in next(iter(evs.values())).values:
                mean = statistics.fmean(e.values[channel] for e in evs.values())
                if mean > 1e-9:
                    self.level[(device, channel)] = mean
        for device, evs in by_device.items():
            for channel in evs[0].values:
                if (device, channel) not in self.level:
                    continue
                residuals = []
                for step, e in evs.items():
                    common = self._peer_median(
                        device,
                        channel,
                        step,
                        {
                            d: by_device[d][step].values
                            for d in self.peers[device]
                            if step in by_device[d]
                        },
                    )
                    if common is not None:
                        residuals.append(
                            e.values[channel] / self.level[(device, channel)] / common - 1.0
                        )
                if len(residuals) > 10:
                    self.sigma[(device, channel)] = max(statistics.pstdev(residuals), SIGMA_FLOOR)

    def _peer_median(
        self, device: str, channel: str, _step: int, readings: dict[str, dict[str, float]]
    ) -> float | None:
        ratios = [
            v[channel] / self.level[(p, channel)]
            for p, v in readings.items()
            if (p, channel) in self.level and channel in v
        ]
        median = statistics.median(ratios) if len(ratios) >= MIN_PEERS else None
        return median if median and median > 1e-6 else None

    # ------------------------------------------------------------------------------------------------------ streaming
    def observe(self, ev: Event) -> list[Signal]:
        d = ev.device_id
        out: list[Signal] = []
        self.last_arrival[d] = max(self.last_arrival[d], ev.arrival)
        if ev.sequence_number in self.accepted[d]:
            return [
                Signal(d, "duplicate", ev.arrival, f"sequence {ev.sequence_number} delivered again")
            ]
        self.accepted[d].add(ev.sequence_number)
        if ev.sequence_number > self.last_seq[d]:
            for s in range(self.last_seq[d] + 1, ev.sequence_number):
                self.pending[d][s] = ev.arrival + self.p.gap_grace_s
            self.last_seq[d] = ev.sequence_number
        else:
            self.pending[d].pop(ev.sequence_number, None)
            out.append(
                Signal(
                    d,
                    "out_of_order",
                    ev.arrival,
                    f"sequence {ev.sequence_number} after {self.last_seq[d]}",
                )
            )
        # a frozen sensor: identical values, event after event
        values = tuple(sorted(ev.values.items()))
        self.recent[d].append(values)
        window = self.recent[d]
        if (
            len(window) == self.p.stuck_run
            and len(set(window)) == 1
            and any(v != 0 for _, v in values)
        ):
            out.append(
                Signal(
                    d,
                    "stuck",
                    ev.arrival,
                    f"the last {self.p.stuck_run} events carry identical values",
                )
            )
        # a minute's readings wait for the tick, so a device is compared with the peers of the same minute
        if ev.sequence_number >= CALIBRATION_STEPS:
            self.bins[int(ev.observation_time // CADENCE_S)].setdefault(d, ev.values)
        return out

    def tick(self, now: float) -> list[Signal]:
        out: list[Signal] = []
        for d in self.devices:
            silent = now - self.last_arrival[d]
            if silent > self.p.silence_after_s:
                out.append(Signal(d, "missing", now, f"nothing received for {silent:.0f} s"))
            expired = [s for s, deadline in self.pending[d].items() if deadline <= now]
            for s in expired:
                del self.pending[d][s]
            if expired:
                out.append(
                    Signal(
                        d,
                        "missing",
                        now,
                        f"{len(expired)} events never arrived (sequence {min(expired)}-{max(expired)})",
                    )
                )
        while (self.next_bin + 1) * CADENCE_S + BIN_SETTLE_S <= now:
            out.extend(self._compare_minute(self.next_bin, now))
            self.bins.pop(self.next_bin, None)
            self.next_bin += 1
        return out

    def _compare_minute(self, b: int, now: float) -> list[Signal]:
        readings = self.bins.get(b, {})
        candidates = []
        for d, values in readings.items():
            for channel, x in values.items():
                key = (d, channel)
                if key not in self.sigma:
                    continue
                common = self._peer_median(
                    d, channel, b, {p: readings[p] for p in self.peers[d] if p in readings}
                )
                if common is None:
                    continue
                z = (x / self.level[key] / common - 1.0) / self.sigma[key]
                self.zbar[key] += ZBAR_ALPHA * (z - self.zbar[key])
                state = self.cusum[key]
                cap = 2 * self.p.cusum_h
                state[0] = min(cap, max(0.0, state[0] + z - self.p.cusum_k))
                state[1] = min(cap, max(0.0, state[1] - z - self.p.cusum_k))
                if max(state) >= self.p.cusum_h:
                    candidates.append((d, channel))
        out = []
        for d, channel in candidates:
            mine = abs(self.zbar[(d, channel)])
            # among only three devices, one drifting device pulls the other two the opposite way; blame the one that moved furthest
            rival = max((abs(self.zbar.get((p, channel), 0.0)) for p in self.peers[d]), default=0.0)
            if rival > RIVAL_FACTOR * mine:
                continue
            up = self.cusum[(d, channel)][0] > self.cusum[(d, channel)][1]
            out.append(
                Signal(
                    d,
                    "drift",
                    now,
                    f"{channel} has moved {'up' if up else 'down'} against its peers",
                )
            )
        return out


# A frozen or silent sensor drifts away from its peers by definition; that is the same fault, not a second one.
EXPLAINED_BY = {"drift": ("stuck", "missing")}


class IncidentTracker:
    """Signals of one (device, class) that follow each other closer than `merge_gap_s` are one incident.

    A drift signal on a device that has a live stuck or missing incident is explained by it and does not open a ticket of its own.
    """

    def __init__(self, merge_gap_s: float):
        self.merge_gap_s = merge_gap_s
        self.open: dict[tuple[str, str], dict] = {}
        self.incidents: list[dict] = []
        self.suppressed = 0

    def _explained(self, s: Signal) -> bool:
        for cause in EXPLAINED_BY.get(s.fault_class, ()):
            other = self.open.get((s.device_id, cause))
            if other and s.at - other["last_signal_at"] <= self.merge_gap_s:
                return True
        return False

    def add(self, signals: list[Signal]) -> None:
        for s in sorted(signals, key=lambda s: s.at):
            if self._explained(s):
                self.suppressed += 1
                continue
            key = (s.device_id, s.fault_class)
            current = self.open.get(key)
            if current and s.at - current["last_signal_at"] <= self.merge_gap_s:
                current["last_signal_at"] = s.at
                current["signals"] += 1
                continue
            if current:
                current["status"] = "resolved"
            current = {
                "device_id": s.device_id, "fault_class": s.fault_class, "opened_at": s.at, "last_signal_at": s.at,
                "signals": 1, "status": "open", "evidence": s.detail, "verified_cause": None,
            }  # fmt: skip
            self.open[key] = current
            self.incidents.append(current)

    def finish(self, now: float) -> list[dict]:
        for incident in self.incidents:
            if now - incident["last_signal_at"] > self.merge_gap_s:
                incident["status"] = "resolved"
        return self.incidents


def run(
    params: Params, events: list[Event], devices: dict[str, str], tick_s: float = 30.0
) -> list[dict]:
    """Calibrate on the fault-free first hour, then stream every delivered event in arrival order with a tick every `tick_s`."""
    detector = Detector(params, devices)
    detector.calibrate([e for e in events if e.sequence_number < CALIBRATION_STEPS])
    tracker = IncidentTracker(params.merge_gap_s)
    live = [e for e in events if e.sequence_number >= CALIBRATION_STEPS]
    calibration_end = max(e.arrival for e in events if e.sequence_number < CALIBRATION_STEPS)
    next_tick = calibration_end + tick_s
    horizon = events[
        -1
    ].arrival  # ticks stop with the data: a stream that has ended is not a silent fleet
    i = 0
    while next_tick <= horizon:
        while i < len(live) and live[i].arrival <= next_tick:
            tracker.add(detector.observe(live[i]))
            i += 1
        tracker.add(detector.tick(next_tick))
        next_tick += tick_s
    return tracker.finish(horizon)
