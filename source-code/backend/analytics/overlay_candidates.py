"""P06.05: collision, wrong-way, flooding and low-visibility candidates from
labelled telemetry overlays (docs/evidence/SIMULATION_LIMITATIONS.md: SUMO
cannot safely simulate these, so P03.05 overlays their sensor signatures).

These are event-driven state machines over explicit sensor flags, so what they
demonstrate is the *handling* the platform needs around a flag, not skill at
finding an anomaly in raw physics: quality gating (an `invalid` reading never
raises a candidate), a confidence floor, deduplication (a repeated flag extends
the open candidate rather than opening another), corroboration by an
independent source (a congestion episode near a collision flag; a friction
reading with a flood flag), contradiction (a flood flag with dry-road friction
is halved) and a stuck-sensor guard (a flag held far longer than any real
incident, with no corroboration, is marked suspect and demoted).

Signal faults are deliberately not safety candidates: they are device faults
and belong to data-quality/AIOps handling (P06.07, Phase 10).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from backend.analytics.congestion import Episode, episode_id
from backend.analytics.kpis import parse_time
from backend.analytics.topology import edge_of_lane

SOURCE = "rule:overlay/1"
MIN_CONFIDENCE = 0.3
QUALITY_FACTOR = {"valid": 1.0, "suspect": 0.85}
CORROBORATION_SLACK_S = 120.0
CONGESTION_SOURCE_CONFIDENCE = 0.75
STUCK_AFTER_S = 1800.0
VISIBILITY_ON_M, VISIBILITY_OFF_M = 200.0, 500.0
FLOOD_FRICTION = 0.3
DRY_FRICTION = 0.5


def _m(event: dict) -> dict:
    return {x["name"]: x for x in event["measurements"]}


def _usable(measurement: dict | None) -> bool:
    return (
        measurement is not None
        and measurement["quality"] in QUALITY_FACTOR
        and measurement["confidence"] >= MIN_CONFIDENCE
    )


def _conf(measurement: dict) -> float:
    return measurement["confidence"] * QUALITY_FACTOR[measurement["quality"]]


def _noisy_or(*values: float) -> float:
    out = 1.0
    for v in values:
        out *= 1.0 - v
    return 1.0 - out


class _Open:
    def __init__(
        self,
        kind: str,
        element_type: str,
        element: str,
        onset: datetime,
        confidence: float,
        evidence: str,
        attrs: dict,
    ) -> None:
        self.kind, self.element_type, self.element = kind, element_type, element
        self.onset, self.last_seen, self.clear = onset, onset, None
        self.confidence, self.evidence, self.attrs = confidence, [evidence], attrs
        self.detected_at = onset


def detect_overlay_candidates(
    events: Iterable[dict], episodes: list[Episode] | None = None, geometry: str = "2026-09-18.1"
) -> list[dict]:
    episodes = episodes or []
    open_: dict[tuple[str, str], _Open] = {}
    done: list[_Open] = []

    def corroborated(segment: str, at: datetime) -> Episode | None:
        slack = timedelta(seconds=CORROBORATION_SLACK_S)
        for ep in episodes:
            if (
                ep.segment == segment
                and ep.onset - slack <= at <= (ep.clear or ep.last_seen or ep.detected_at) + slack
            ):
                return ep
        return None

    def open_or_extend(key, kind, etype, element, at, conf, evidence, attrs):
        cur = open_.get(key)
        if cur is None:
            open_[key] = _Open(kind, etype, element, at, conf, evidence, attrs)
        else:
            cur.last_seen = at
            cur.evidence.append(evidence)
            cur.confidence = max(cur.confidence, conf)

    def close(key, at):
        cur = open_.pop(key, None)
        if cur is not None:
            cur.clear = at
            done.append(cur)

    for e in sorted(events, key=lambda ev: (ev["observation_time"], ev["event_id"])):
        at = parse_time(e["observation_time"])
        m = _m(e)
        loc = e.get("location", {})
        lane = loc.get("lane_id")
        etype = e["event_type"]
        if etype == "traffic.loop_detector.count" and (lane or e["device_id"].startswith("loop-")):
            # P03.05's overlay events carry no lane_id; the loop's device id names its segment
            seg = (
                edge_of_lane(lane)
                if lane
                else e["device_id"][len("loop-") :].replace("-int-", "_int-")
            )
            flag = m.get("stopped_vehicle_flag")
            if flag is not None:
                if flag["value"] is True and _usable(flag):
                    ep = corroborated(seg, at)
                    conf = (
                        _noisy_or(_conf(flag), CONGESTION_SOURCE_CONFIDENCE) if ep else _conf(flag)
                    )
                    open_or_extend(
                        (e["device_id"], "collision"),
                        "collision",
                        "segment",
                        seg,
                        at,
                        conf,
                        e["event_id"],
                        {
                            "corroborated": ep is not None,
                            "flag_confidence": flag["confidence"],
                            "device_id": e["device_id"],
                        },
                    )
                elif flag["value"] is False:
                    close((e["device_id"], "collision"), at)
            conflict = m.get("direction_conflict")
            if conflict is not None:
                if conflict["value"] is True and _usable(conflict):
                    speed = m.get("mean_speed")
                    support = (
                        speed is not None
                        and speed["quality"] in QUALITY_FACTOR
                        and speed["value"] < 0
                    )
                    conf = _noisy_or(_conf(conflict), _conf(speed)) if support else _conf(conflict)
                    open_or_extend(
                        (e["device_id"], "wrong_way"),
                        "wrong_way",
                        "segment",
                        seg,
                        at,
                        conf,
                        e["event_id"],
                        {
                            "corroborated": bool(support),
                            "negative_speed_support": bool(support),
                            "device_id": e["device_id"],
                        },
                    )
                elif conflict["value"] is False:
                    close((e["device_id"], "wrong_way"), at)
        elif etype == "road.condition_sensor.reading":
            corridor = loc.get("corridor_id")
            state, friction = m.get("surface_state"), m.get("friction_estimate")
            if state is not None and corridor:
                if state["value"] == "flooded" and _usable(state):
                    conf = _conf(state)
                    f_val = (
                        friction["value"]
                        if friction is not None and friction["quality"] in QUALITY_FACTOR
                        else None
                    )
                    if f_val is not None and f_val < FLOOD_FRICTION:
                        conf = _noisy_or(conf, _conf(friction))
                    elif f_val is not None and f_val >= DRY_FRICTION:
                        conf *= 0.5  # a flood flag over dry-road friction contradicts itself
                    open_or_extend(
                        (e["device_id"], "flooding"),
                        "flooding",
                        "corridor",
                        corridor,
                        at,
                        conf,
                        e["event_id"],
                        {
                            "corroborated": f_val is not None and f_val < FLOOD_FRICTION,
                            "friction": f_val,
                            "device_id": e["device_id"],
                        },
                    )
                elif state["value"] != "flooded":
                    close((e["device_id"], "flooding"), at)
        elif etype == "weather.station.reading":
            corridor, vis = loc.get("corridor_id"), m.get("visibility_distance")
            if vis is not None and corridor and vis["quality"] in QUALITY_FACTOR:
                if vis["value"] < VISIBILITY_ON_M and _usable(vis):
                    open_or_extend(
                        (e["device_id"], "low_visibility"),
                        "low_visibility",
                        "corridor",
                        corridor,
                        at,
                        _conf(vis),
                        e["event_id"],
                        {
                            "corroborated": False,
                            "visibility_m": vis["value"],
                            "device_id": e["device_id"],
                        },
                    )
                elif vis["value"] >= VISIBILITY_OFF_M:
                    close((e["device_id"], "low_visibility"), at)

    out = []
    for c in done + list(open_.values()):
        duration = ((c.clear or c.last_seen) - c.onset).total_seconds()
        stuck = duration > STUCK_AFTER_S and not c.attrs.get("corroborated")
        confidence = c.confidence * (0.5 if stuck else 1.0)
        severity = (
            "high"
            if c.kind in ("collision", "wrong_way")
            else (
                "high"
                if (c.attrs.get("friction") or 1.0) < 0.2
                or (c.attrs.get("visibility_m") or 1e9) < 100
                else "medium"
            )
        )
        if c.kind == "collision" and c.attrs.get("corroborated") and confidence >= 0.9:
            severity = "critical"
        out.append(
            {
                "candidate_id": episode_id(c.kind, c.element, c.onset),
                "kind": c.kind,
                "network_element_type": c.element_type,
                "network_element_id": c.element,
                "geometry_version": geometry,
                "onset_time": c.onset,
                "clear_time": c.clear,
                "detected_at": c.onset,
                "severity": severity,
                "confidence": round(min(confidence, 0.99), 4),
                "evidence_event_ids": list(dict.fromkeys(c.evidence)),
                "source": SOURCE,
                "attributes": {**c.attrs, "suspect_stuck": stuck, "duration_s": duration},
            }
        )
    return sorted(out, key=lambda x: (x["onset_time"], x["kind"]))
