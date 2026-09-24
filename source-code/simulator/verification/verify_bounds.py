"""P03.09: automated cross-stage invariant checks over P03.01-P03.08's
already-generated output. Pure Python, no SUMO/Docker needed.

This is a *new* cross-cutting check, not a re-run of each stage's own
build_and_verify.py (which already proves its own determinism/coverage
claims): it checks properties that only make sense once you look across all
stages at once - e.g. that every stage's lat/lon, when projected back to the
network's local plane, actually lands inside the network's real bounding
box (each stage only ever checked its own forward projection, never an
end-to-end round trip), and that every event's device_id actually exists in
that stage's own device registry (referential integrity across files that
are otherwise never cross-checked).

Gracefully skips any stage whose output isn't present (reports it as
`"skipped"` rather than failing), so this can run against a partial set of
regenerated stages; `build_and_verify.py` requires the full set before
treating the overall pass as meaningful.
"""

from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SIMULATOR_DIR = Path(__file__).resolve().parent.parent
NET_FILE = SIMULATOR_DIR / "network" / "output" / "district.net.xml"

sys.path.insert(0, str(SIMULATOR_DIR / "sensors"))
from build_sensor_catalog import ORIGIN_LAT, ORIGIN_LON, _EARTH_RADIUS_M  # noqa: E402

ANCHOR_UTC = "2026-09-18T09:00:00Z"
MAX_REASONABLE_WINDOW = timedelta(hours=6)
MAX_REASONABLE_SPEED_M_S = 40.0
BOUNDARY_MARGIN_M = 50.0


def load_network_bbox(net_file: Path) -> tuple[float, float, float, float]:
    text = net_file.read_text(encoding="utf-8")
    match = re.search(r'convBoundary="([^"]+)"', text)
    if not match:
        raise SystemExit(f"no convBoundary found in {net_file}")
    x0, y0, x1, y1 = (float(v) for v in match.group(1).split(","))
    return x0, y0, x1, y1


def inverse_project(latitude: float, longitude: float) -> tuple[float, float]:
    y = (latitude - ORIGIN_LAT) * _EARTH_RADIUS_M * (math.pi / 180.0)
    x = (
        (longitude - ORIGIN_LON)
        * (_EARTH_RADIUS_M * math.cos(math.radians(ORIGIN_LAT)))
        * (math.pi / 180.0)
    )
    return x, y


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def check_location_bounds(
    bbox: tuple[float, float, float, float], events: list[dict], label: str
) -> list[str]:
    x0, y0, x1, y1 = bbox
    problems = []
    for event in events:
        location = event.get("location", {})
        if "latitude" not in location:
            continue
        x, y = inverse_project(location["latitude"], location["longitude"])
        if not (x0 - BOUNDARY_MARGIN_M <= x <= x1 + BOUNDARY_MARGIN_M):
            problems.append(f"{label} {event.get('event_id')}: x={x:.1f} outside [{x0},{x1}]")
        if not (y0 - BOUNDARY_MARGIN_M <= y <= y1 + BOUNDARY_MARGIN_M):
            problems.append(f"{label} {event.get('event_id')}: y={y:.1f} outside [{y0},{y1}]")
    return problems


def check_timestamp_bounds(events: list[dict], label: str) -> list[str]:
    anchor = datetime.fromisoformat(ANCHOR_UTC.replace("Z", "+00:00")).astimezone(timezone.utc)
    latest_allowed = anchor + MAX_REASONABLE_WINDOW
    problems = []
    for event in events:
        raw = event.get("observation_time")
        if not raw:
            continue
        observed = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        if not (anchor <= observed <= latest_allowed):
            problems.append(
                f"{label} {event.get('event_id')}: observation_time {raw} outside expected window"
            )
    return problems


def check_confidence_bounds(events: list[dict], label: str) -> list[str]:
    problems = []
    for event in events:
        for measurement in event.get("measurements", []):
            confidence = measurement.get("confidence")
            if confidence is not None and not (0.0 <= confidence <= 1.0):
                problems.append(
                    f"{label} {event.get('event_id')}: confidence {confidence} out of [0,1]"
                )
    return problems


def check_speed_bounds(events: list[dict], label: str) -> list[str]:
    problems = []
    for event in events:
        for measurement in event.get("measurements", []):
            if measurement.get("unit") == "m_s-1":
                value = measurement.get("value")
                if isinstance(value, (int, float)) and abs(value) > MAX_REASONABLE_SPEED_M_S:
                    problems.append(
                        f"{label} {event.get('event_id')}: speed {value} m/s exceeds {MAX_REASONABLE_SPEED_M_S}"
                    )
    return problems


def check_device_referential_integrity(
    events: list[dict], known_device_ids: set[str], label: str
) -> list[str]:
    problems = []
    for event in events:
        device_id = event.get("device_id")
        if device_id and device_id not in known_device_ids:
            problems.append(
                f"{label} {event.get('event_id')}: device_id {device_id} not in device registry"
            )
    return problems


def run_all_checks() -> dict[str, object]:
    if not NET_FILE.is_file():
        raise SystemExit(f"missing {NET_FILE}; run P03.01's network build_and_verify.py first")
    bbox = load_network_bbox(NET_FILE)

    report: dict[str, object] = {"bbox": bbox, "stages": {}}
    all_problems: list[str] = []

    sensors_dir = SIMULATOR_DIR / "sensors" / "output" / "run-a"
    if (sensors_dir / "observations.jsonl").is_file() and (sensors_dir / "devices.jsonl").is_file():
        events = _load_jsonl(sensors_dir / "observations.jsonl")
        device_ids = {d["device_id"] for d in _load_jsonl(sensors_dir / "devices.jsonl")}
        problems = (
            check_location_bounds(bbox, events, "sensors")
            + check_timestamp_bounds(events, "sensors")
            + check_confidence_bounds(events, "sensors")
            + check_speed_bounds(events, "sensors")
            + check_device_referential_integrity(events, device_ids, "sensors")
        )
        report["stages"]["sensors"] = {"checked_events": len(events), "problems": problems}
        all_problems.extend(problems)
    else:
        report["stages"]["sensors"] = {"skipped": "output missing"}

    emergency_dir = SIMULATOR_DIR / "emergency" / "output" / "run-a"
    if (emergency_dir / "avl_events.jsonl").is_file() and (
        emergency_dir / "devices.jsonl"
    ).is_file():
        events = _load_jsonl(emergency_dir / "avl_events.jsonl")
        device_ids = {d["device_id"] for d in _load_jsonl(emergency_dir / "devices.jsonl")}
        problems = (
            check_location_bounds(bbox, events, "emergency")
            + check_timestamp_bounds(events, "emergency")
            + check_confidence_bounds(events, "emergency")
            + check_speed_bounds(events, "emergency")
            + check_device_referential_integrity(events, device_ids, "emergency")
        )
        report["stages"]["emergency"] = {"checked_events": len(events), "problems": problems}
        all_problems.extend(problems)
    else:
        report["stages"]["emergency"] = {"skipped": "output missing"}

    # scenarios' overlay events and faults' events both reference devices from
    # the P03.03 sensor catalog (or, for faults, the one new aiops_agent
    # device), so both are checked against that union.
    sensor_device_ids: set[str] = set()
    if (sensors_dir / "devices.jsonl").is_file():
        sensor_device_ids = {d["device_id"] for d in _load_jsonl(sensors_dir / "devices.jsonl")}

    scenarios_dir = SIMULATOR_DIR / "scenarios" / "output" / "run-a"
    if (scenarios_dir / "overlay_events.jsonl").is_file():
        events = _load_jsonl(scenarios_dir / "overlay_events.jsonl")
        problems = (
            check_location_bounds(bbox, events, "scenarios")
            + check_timestamp_bounds(events, "scenarios")
            + check_confidence_bounds(events, "scenarios")
            + check_speed_bounds(events, "scenarios")
            + check_device_referential_integrity(events, sensor_device_ids, "scenarios")
        )
        report["stages"]["scenarios"] = {"checked_events": len(events), "problems": problems}
        all_problems.extend(problems)
    else:
        report["stages"]["scenarios"] = {"skipped": "output missing"}

    faults_dir = SIMULATOR_DIR / "faults" / "output" / "run-a"
    if (faults_dir / "fault_events.jsonl").is_file() and (
        faults_dir / "aiops_agent_device.json"
    ).is_file():
        events = _load_jsonl(faults_dir / "fault_events.jsonl")
        agent_device = json.loads(
            (faults_dir / "aiops_agent_device.json").read_text(encoding="utf-8")
        )
        known_ids = sensor_device_ids | {agent_device["device_id"]}
        problems = (
            check_location_bounds(bbox, events, "faults")
            + check_timestamp_bounds(events, "faults")
            + check_confidence_bounds(events, "faults")
            + check_speed_bounds(events, "faults")
            + check_device_referential_integrity(events, known_ids, "faults")
        )
        report["stages"]["faults"] = {"checked_events": len(events), "problems": problems}
        all_problems.extend(problems)
    else:
        report["stages"]["faults"] = {"skipped": "output missing"}

    report["total_problems"] = len(all_problems)
    report["problems"] = all_problems
    report["passed"] = len(all_problems) == 0
    return report
