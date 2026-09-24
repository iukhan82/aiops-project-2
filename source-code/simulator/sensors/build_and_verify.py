"""P03.03: build and verify the traffic/VRU/signal/weather/road sensor catalog.

Runs inside the pinned ghcr.io/eclipse-sumo/sumo image (see
source-code/simulator/network/run_container.sh for the exact digest); requires
source-code/simulator/network/output/district.net.xml to already exist
(P03.01's build_and_verify.py). Generates its own deterministic demand (reusing
source-code/simulator/demand/generate_demand.py) rather than depending on
P03.02's output artifacts, so this stage is self-contained and reproducible on
its own.

Proves:
- The device catalog and the full observation-event stream are byte-identical
  across two independent runs of the same seed/run_id.
- All five required sensor categories (traffic, VRU, signal, weather, road)
  produced at least one event.

Schema conformance against contracts/device/v1 and
contracts/observation-envelope/v1 is checked separately by
source-code/tests/test_sensor_catalog.py (host pytest venv), because the
pinned SUMO image used here has no jsonschema package; that test reads the
devices.jsonl/observations.jsonl this script writes under output/run-a/.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIMULATOR_DIR = HERE.parent
NETWORK_DIR = SIMULATOR_DIR / "network"
DEMAND_DIR = SIMULATOR_DIR / "demand"
NET_FILE = NETWORK_DIR / "output" / "district.net.xml"
NOD_FILE = NETWORK_DIR / "plain" / "district.nod.xml"
VTYPES_FILE = NETWORK_DIR / "demand" / "vtypes.add.xml"
STOPS_FILE = DEMAND_DIR / "stops.add.xml"
OUTPUT_DIR = HERE / "output"

sys.path.insert(0, str(DEMAND_DIR))
from generate_demand import generate as generate_demand  # noqa: E402

sys.path.insert(0, str(HERE))
from build_sensor_catalog import build_catalog  # noqa: E402
from generate_observations import (  # noqa: E402
    SequenceCounter,
    crossing_events,
    parse_loop_intervals,
    signal_events,
    traffic_events,
    weather_and_road_events,
)

SEED = 20260918
RUN_ID = "p03-03-sensors"
ANCHOR_UTC = "2026-09-18T09:00:00Z"
SIM_END = 600
CORRELATION_ID = f"run-2026-09-18-seed-{SEED}"


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, capture_output=True, text=True, **kwargs)


def write_additional_files(
    devices: dict[str, object], run_dir: Path
) -> tuple[Path, Path, dict[str, Path]]:
    loops_path = run_dir / "loops.add.xml"
    lines = ["<additional>"]
    for device in devices["devices"]:
        if device["device_type"] == "inductive_loop":
            lines.append(
                f'    <inductionLoop id="{device["device_id"]}" '
                f'lane="{device["location"]["lane_id"]}" pos="10" freq="60" file="loop-output.xml"/>'
            )
        elif device["device_type"] == "cycle_counter":
            lines.append(
                f'    <inductionLoop id="{device["device_id"]}" '
                f'lane="{device["location"]["lane_id"]}" pos="5" freq="60" file="loop-output.xml"/>'
            )
    lines.append("</additional>")
    loops_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    tls_dir = run_dir / "tls"
    tls_dir.mkdir(exist_ok=True)
    tls_files: dict[str, Path] = {}
    tls_lines = ["<additional>"]
    signal_devices = [d for d in devices["devices"] if d["device_type"] == "signal_controller"]
    for device in sorted(signal_devices, key=lambda d: d["device_id"]):
        junction_id = device["location"]["intersection_id"]
        dest = tls_dir / f"tls-{junction_id}.xml"
        tls_files[junction_id] = dest
        tls_lines.append(
            f'    <timedEvent type="SaveTLSStates" source="{junction_id}" dest="{dest}"/>'
        )
    tls_lines.append("</additional>")
    tls_add_path = run_dir / "tls.add.xml"
    tls_add_path.write_text("\n".join(tls_lines) + "\n", encoding="utf-8", newline="\n")

    return loops_path, tls_add_path, tls_files


def write_sumocfg(route_file: Path, loops_add: Path, tls_add: Path, sumocfg_path: Path) -> None:
    sumocfg_path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <input>
        <net-file value="{NET_FILE}"/>
        <route-files value="{route_file}"/>
        <additional-files value="{VTYPES_FILE},{STOPS_FILE},{loops_add},{tls_add}"/>
    </input>
    <time><begin value="0"/><end value="{SIM_END}"/></time>
    <random_number><seed value="{SEED}"/></random_number>
</configuration>
""",
        encoding="utf-8",
    )


def build_one_run(run_label: str) -> dict[str, object]:
    run_dir = OUTPUT_DIR / run_label
    run_dir.mkdir(parents=True, exist_ok=True)

    devices = build_catalog(NET_FILE, NOD_FILE)

    demand_dir = run_dir / "demand"
    manifest = generate_demand(SEED, RUN_ID, ANCHOR_UTC, SIM_END, demand_dir)
    route_file = demand_dir / f"{RUN_ID}.rou.xml"

    loops_add, tls_add, tls_files = write_additional_files(devices, run_dir)
    sumocfg = run_dir / "sensors.sumocfg"
    write_sumocfg(route_file, loops_add, tls_add, sumocfg)

    run(["sumo", "-c", str(sumocfg), "--no-step-log", "--duration-log.disable"], cwd=run_dir)

    intervals = parse_loop_intervals(run_dir / "loop-output.xml")
    anchor = datetime.fromisoformat(ANCHOR_UTC.replace("Z", "+00:00")).astimezone(timezone.utc)
    seq = SequenceCounter()

    loop_devices = [d for d in devices["devices"] if d["device_type"] == "inductive_loop"]
    cycle_devices = [d for d in devices["devices"] if d["device_type"] == "cycle_counter"]
    crossing_devices = [d for d in devices["devices"] if d["device_type"] == "crossing_detector"]
    signal_devices = [d for d in devices["devices"] if d["device_type"] == "signal_controller"]
    weather_devices = [d for d in devices["devices"] if d["device_type"] == "weather_station"]
    road_devices = [d for d in devices["devices"] if d["device_type"] == "road_condition_sensor"]

    events: list[dict[str, object]] = []
    events += traffic_events(
        loop_devices,
        intervals,
        anchor,
        RUN_ID,
        seq,
        CORRELATION_ID,
        "traffic.loop_detector.count",
        "vehicle_count",
    )
    events += traffic_events(
        cycle_devices,
        intervals,
        anchor,
        RUN_ID,
        seq,
        CORRELATION_ID,
        "vru.cycle_counter.count",
        "cyclist_count",
    )
    events += signal_events(signal_devices, tls_files, anchor, RUN_ID, seq, CORRELATION_ID)
    events += crossing_events(crossing_devices, SIM_END, anchor, SEED, RUN_ID, seq, CORRELATION_ID)
    events += weather_and_road_events(
        weather_devices, road_devices, SIM_END, anchor, SEED, RUN_ID, seq, CORRELATION_ID
    )

    events.sort(key=lambda e: (e["observation_time"], e["device_id"], e["sequence_number"]))

    events_path = run_dir / "observations.jsonl"
    with events_path.open("w", encoding="utf-8", newline="\n") as stream:
        for event in events:
            stream.write(json.dumps(event, sort_keys=True) + "\n")

    devices_sorted = sorted(devices["devices"], key=lambda d: d["device_id"])
    devices_path = run_dir / "devices.jsonl"
    with devices_path.open("w", encoding="utf-8", newline="\n") as stream:
        for device in devices_sorted:
            stream.write(json.dumps(device, sort_keys=True) + "\n")

    return {
        "run_dir": run_dir,
        "devices_path": devices_path,
        "events_path": events_path,
        "device_counts": devices["counts"],
        "event_count": len(events),
        "manifest_entity_counts": manifest["entity_counts"],
    }


def check_category_coverage(events_path: Path) -> dict[str, object]:
    event_count = 0
    category_types: set[str] = set()
    for line in events_path.read_text(encoding="utf-8").splitlines():
        instance = json.loads(line)
        category_types.add(instance["event_type"].split(".")[0])
        event_count += 1

    required_categories = {"traffic", "vru", "signal", "weather", "road"}
    missing = required_categories - category_types
    if missing:
        raise SystemExit(
            f"missing required sensor categories in generated events: {sorted(missing)}"
        )

    return {"events_checked": event_count, "categories_present": sorted(category_types)}


def main() -> None:
    if not NET_FILE.is_file():
        raise SystemExit(f"missing {NET_FILE}; run P03.01's network build_and_verify.py first")
    OUTPUT_DIR.mkdir(exist_ok=True)

    run_a = build_one_run("run-a")
    run_b = build_one_run("run-b")

    devices_a = run_a["devices_path"].read_text(encoding="utf-8")
    devices_b = run_b["devices_path"].read_text(encoding="utf-8")
    if devices_a != devices_b:
        raise SystemExit("device catalog is not byte-identical across two runs of the same inputs")

    events_a = run_a["events_path"].read_text(encoding="utf-8")
    events_b = run_b["events_path"].read_text(encoding="utf-8")
    if events_a != events_b:
        raise SystemExit(
            "observation events are not byte-identical across two runs of the same seed/run_id"
        )

    coverage = check_category_coverage(run_a["events_path"])

    summary = {
        "seed": SEED,
        "run_id": RUN_ID,
        "anchor_utc": ANCHOR_UTC,
        "sim_end_seconds": SIM_END,
        "device_counts": run_a["device_counts"],
        "total_devices": sum(run_a["device_counts"].values()),
        "total_events": run_a["event_count"],
        "deterministic_catalog": True,
        "deterministic_events": True,
        "coverage": coverage,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
