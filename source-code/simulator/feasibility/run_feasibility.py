"""P01.06: deterministic headless SUMO and telemetry-extraction proof."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SEED = 20260918
SIM_END_SECONDS = 600
HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"
NET_FILE = OUTPUT_DIR / "network.net.xml"
ROUTE_FILE = OUTPUT_DIR / "demand.rou.xml"
TRIPS_FILE = OUTPUT_DIR / "demand.trips.xml"
ADD_FILE = OUTPUT_DIR / "detectors.add.xml"
SUMOCFG_FILE = OUTPUT_DIR / "run.sumocfg"


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, cwd=OUTPUT_DIR)


def find_random_trips() -> Path:
    candidates: list[Path] = []
    if sumo_home := os.environ.get("SUMO_HOME"):
        candidates.append(Path(sumo_home) / "tools" / "randomTrips.py")
    candidates.extend(
        [
            Path(sys.prefix) / "share" / "sumo" / "tools" / "randomTrips.py",
            Path("/usr/share/sumo/tools/randomTrips.py"),
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("SUMO randomTrips.py was not found")


def build_network() -> None:
    run(
        [
            "netgenerate",
            "--grid",
            "--grid.number=3",
            "--grid.length=250",
            "--default.lanenumber=1",
            "--tls.guess",
            "--tls.default-type=static",
            "--seed",
            str(SEED),
            "-o",
            NET_FILE.name,
        ]
    )


def build_demand() -> None:
    run(
        [
            sys.executable,
            str(find_random_trips()),
            "-n",
            NET_FILE.name,
            "-o",
            TRIPS_FILE.name,
            "-r",
            ROUTE_FILE.name,
            "--seed",
            str(SEED),
            "--begin",
            "0",
            "--end",
            str(SIM_END_SECONDS),
            "--period",
            "3",
            "--validate",
        ]
    )


def build_detectors() -> None:
    root = ET.parse(NET_FILE).getroot()
    lanes = [
        lane.get("id")
        for edge in root.findall("edge")
        if edge.get("function") is None
        for lane in edge.findall("lane")
    ]
    additional = ET.Element("additional")
    for index, lane_id in enumerate(sorted(lanes)[:3]):
        ET.SubElement(
            additional,
            "inductionLoop",
            {
                "id": f"loop_{index}",
                "lane": str(lane_id),
                "pos": "10",
                "freq": "60",
                "file": "loop-output.xml",
            },
        )
    ET.ElementTree(additional).write(ADD_FILE, encoding="UTF-8", xml_declaration=True)


def build_configuration() -> None:
    SUMOCFG_FILE.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <input>
        <net-file value="{NET_FILE.name}"/>
        <route-files value="{ROUTE_FILE.name}"/>
        <additional-files value="{ADD_FILE.name}"/>
    </input>
    <time><begin value="0"/><end value="{SIM_END_SECONDS}"/></time>
    <random_number><seed value="{SEED}"/></random_number>
    <output><fcd-output value="fcd-output.xml"/></output>
</configuration>
""",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def semantic_xml_sha256(path: Path) -> str:
    """Hash XML content while excluding SUMO's generated-at comment."""
    root = ET.parse(path).getroot()
    return hashlib.sha256(ET.tostring(root, encoding="utf-8")).hexdigest()


def run_sumo(label: str) -> dict[str, str]:
    fcd_output = OUTPUT_DIR / "fcd-output.xml"
    loop_output = OUTPUT_DIR / "loop-output.xml"
    for path in (fcd_output, loop_output):
        path.unlink(missing_ok=True)
    run(["sumo", "-c", SUMOCFG_FILE.name, "--no-step-log", "--duration-log.disable"])
    return {
        "label": label,
        "fcd_semantic_sha256": semantic_xml_sha256(fcd_output),
        "loop_semantic_sha256": semantic_xml_sha256(loop_output),
    }


def extract_telemetry() -> dict[str, object]:
    records: list[dict[str, object]] = []
    for timestep in ET.parse(OUTPUT_DIR / "fcd-output.xml").getroot().findall("timestep"):
        timestamp = float(str(timestep.get("time")))
        for vehicle in timestep.findall("vehicle"):
            records.append(
                {
                    "run_id": f"sumo-feasibility-{SEED}",
                    "classification": "simulated",
                    "timestamp_s": timestamp,
                    "vehicle_id": vehicle.get("id"),
                    "lane_id": vehicle.get("lane"),
                    "speed_m_s": float(str(vehicle.get("speed"))),
                    "x_m": float(str(vehicle.get("x"))),
                    "y_m": float(str(vehicle.get("y"))),
                }
            )
    telemetry_file = OUTPUT_DIR / "telemetry.jsonl"
    with telemetry_file.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
    return {
        "record_count": len(records),
        "telemetry_sha256": sha256(telemetry_file),
    }


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    build_network()
    build_demand()
    build_detectors()
    build_configuration()

    first = run_sumo("run_1")
    first_telemetry = extract_telemetry()
    second = run_sumo("run_2")
    second_telemetry = extract_telemetry()
    deterministic = (
        first["fcd_semantic_sha256"] == second["fcd_semantic_sha256"]
        and first["loop_semantic_sha256"] == second["loop_semantic_sha256"]
        and first_telemetry == second_telemetry
    )
    version = subprocess.run(
        ["sumo", "--version"], check=True, capture_output=True, text=True
    ).stdout.splitlines()[0]
    summary = {
        "sumo_version": version,
        "seed": SEED,
        "sim_end_seconds": SIM_END_SECONDS,
        "run_1": first,
        "run_2": second,
        "deterministic_replay": deterministic,
        "telemetry": second_telemetry,
    }
    print(json.dumps(summary, indent=2))
    if not deterministic:
        raise SystemExit("non-deterministic SUMO output across fixed-seed runs")


if __name__ == "__main__":
    main()
