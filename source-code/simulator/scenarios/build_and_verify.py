"""P03.05: build and verify traffic/safety scenarios with separate ground
truth: normal, peak, event, stall (physically simulated via real SUMO runs)
and collision, wrong_way, flood, visibility, signal (labeled synthetic
anomaly overlays - see generate_overlay_events.py for why).

Runs inside the pinned ghcr.io/eclipse-sumo/sumo image; requires
source-code/simulator/network/output/district.net.xml (P03.01) to already
exist. Self-contained: does not depend on P03.02/P03.03/P03.04 output
artifacts, only on their code (imported directly).
"""

from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIMULATOR_DIR = HERE.parent
NETWORK_DIR = SIMULATOR_DIR / "network"
NET_FILE = NETWORK_DIR / "output" / "district.net.xml"
NOD_FILE = NETWORK_DIR / "plain" / "district.nod.xml"
VTYPES_FILE = NETWORK_DIR / "demand" / "vtypes.add.xml"
STOPS_FILE = SIMULATOR_DIR / "demand" / "stops.add.xml"
OUTPUT_DIR = HERE / "output"

sys.path.insert(0, str(HERE))
from generate_physical_demand import (  # noqa: E402
    add_event_surge,
    choose_stall_vehicle,
    generate_scaled_entities,
    write_route_file_with_stall,
)
from generate_overlay_events import build_overlays  # noqa: E402

sys.path.insert(0, str(SIMULATOR_DIR / "demand"))
from generate_demand import write_route_file  # noqa: E402

SEED = 20260918
RUN_ID = "p03-05-scenarios"
ANCHOR_UTC = "2026-09-18T09:00:00Z"
SIM_END = 600


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, capture_output=True, text=True, **kwargs)


def _iso(anchor: datetime, offset_s: float) -> str:
    dt = anchor + timedelta(seconds=offset_s)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def write_sumocfg(
    route_file: Path,
    sumocfg_path: Path,
    stats_out: Path,
    stop_out: Path | None = None,
) -> None:
    stop_line = f'\n        <stop-output value="{stop_out}"/>' if stop_out else ""
    sumocfg_path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <input>
        <net-file value="{NET_FILE}"/>
        <route-files value="{route_file}"/>
        <additional-files value="{VTYPES_FILE},{STOPS_FILE}"/>
    </input>
    <time><begin value="0"/><end value="{SIM_END}"/></time>
    <random_number><seed value="{SEED}"/></random_number>
    <output>
        <statistic-output value="{stats_out}"/>{stop_line}
    </output>
</configuration>
""",
        encoding="utf-8",
    )


def run_physical_scenario(
    scenario_label: str, run_dir: Path, route_file: Path, need_stopinfo: bool
) -> dict:
    scenario_dir = run_dir / scenario_label
    scenario_dir.mkdir(parents=True, exist_ok=True)
    sumocfg = scenario_dir / f"{scenario_label}.sumocfg"
    stats_out = scenario_dir / "statistics.xml"
    stop_out = scenario_dir / "stopinfo.xml" if need_stopinfo else None
    write_sumocfg(route_file, sumocfg, stats_out, stop_out)
    run(["sumo", "-c", str(sumocfg), "--no-step-log", "--duration-log.disable"], cwd=scenario_dir)

    stats = ET.parse(stats_out).getroot()
    teleports = int(stats.find("teleports").get("total", "-1"))
    collisions = int(stats.find("safety").get("collisions", "-1"))
    vehicles = stats.find("vehicles")
    result = {
        "teleports": teleports,
        "collisions": collisions,
        "loaded": int(vehicles.get("loaded", "-1")),
        "inserted": int(vehicles.get("inserted", "-1")),
        "running": int(vehicles.get("running", "-1")),
    }
    if stop_out:
        result["stop_out_path"] = stop_out
    return result


def build_one_run(run_label: str) -> dict[str, object]:
    run_dir = OUTPUT_DIR / run_label
    run_dir.mkdir(parents=True, exist_ok=True)
    anchor = datetime.fromisoformat(ANCHOR_UTC.replace("Z", "+00:00")).astimezone(timezone.utc)

    ground_truth: list[dict] = []
    physical_summary: dict[str, dict] = {}

    normal_entities = generate_scaled_entities(SEED, f"{RUN_ID}-normal", SIM_END, 1.0)
    normal_route = run_dir / "normal.rou.xml"
    write_route_file(normal_entities, normal_route)
    physical_summary["normal"] = run_physical_scenario("normal", run_dir, normal_route, False)
    ground_truth.append(
        {
            "scenario_id": "p03-05-normal",
            "scenario_type": "normal",
            "onset": _iso(anchor, 0),
            "end": _iso(anchor, SIM_END),
            "affected_entities": [],
            "description": "Baseline demand, no injected anomaly.",
            "truth_label": "simulated",
            "synthetic_overlay": False,
        }
    )

    peak_entities = generate_scaled_entities(SEED, f"{RUN_ID}-peak", SIM_END, 2.0)
    peak_route = run_dir / "peak.rou.xml"
    write_route_file(peak_entities, peak_route)
    physical_summary["peak"] = run_physical_scenario("peak", run_dir, peak_route, False)
    ground_truth.append(
        {
            "scenario_id": "p03-05-peak",
            "scenario_type": "peak",
            "onset": _iso(anchor, 0),
            "end": _iso(anchor, SIM_END),
            "affected_entities": [],
            "description": "All road-user demand doubled relative to normal.",
            "truth_label": "simulated",
            "synthetic_overlay": False,
        }
    )

    event_base_entities = generate_scaled_entities(SEED, f"{RUN_ID}-event", SIM_END, 1.0)
    event_entities = add_event_surge(event_base_entities, SEED, f"{RUN_ID}-event")
    event_route = run_dir / "event.rou.xml"
    write_route_file(event_entities, event_route)
    physical_summary["event"] = run_physical_scenario("event", run_dir, event_route, False)
    surge_ids = [e.entity_id for e in event_entities if "event-ped" in e.entity_id]
    ground_truth.append(
        {
            "scenario_id": "p03-05-event",
            "scenario_type": "event",
            "onset": _iso(anchor, 200),
            "end": _iso(anchor, 260),
            "affected_entities": sorted(surge_ids) + ["int-b2_int-b3"],
            "description": "Localized pedestrian surge on int-b2_int-b3 (e.g. a nearby event letting out).",
            "truth_label": "simulated",
            "synthetic_overlay": False,
        }
    )

    stall_entities = generate_scaled_entities(SEED, f"{RUN_ID}-stall", SIM_END, 1.0)
    stall_plan = choose_stall_vehicle(stall_entities)
    stall_route = run_dir / "stall.rou.xml"
    write_route_file_with_stall(stall_entities, stall_plan, stall_route)
    stall_result = run_physical_scenario("stall", run_dir, stall_route, True)
    physical_summary["stall"] = {k: v for k, v in stall_result.items() if k != "stop_out_path"}
    stopinfo = ET.parse(stall_result["stop_out_path"]).getroot()
    stall_stop = next(
        s for s in stopinfo.findall("stopinfo") if s.get("id") == stall_plan.vehicle_id
    )
    stall_started = float(stall_stop.get("started"))
    stall_ended = float(stall_stop.get("ended"))
    physical_summary["stall"]["measured_stop_started_s"] = stall_started
    physical_summary["stall"]["measured_stop_ended_s"] = stall_ended
    ground_truth.append(
        {
            "scenario_id": "p03-05-stall",
            "scenario_type": "stall",
            "onset": _iso(anchor, stall_started),
            "end": _iso(anchor, stall_ended),
            "affected_entities": [stall_plan.vehicle_id, stall_plan.edge_id],
            "description": f"{stall_plan.vehicle_id} stalled on {stall_plan.edge_id} for "
            f"{stall_plan.duration_s}s (measured stop start/end from SUMO stop-output).",
            "truth_label": "simulated",
            "synthetic_overlay": False,
        }
    )

    for scenario_result in physical_summary.values():
        if scenario_result["teleports"] != 0:
            raise SystemExit(f"expected zero teleports, got {scenario_result}")
        if scenario_result["collisions"] != 0:
            raise SystemExit(f"expected zero collisions, got {scenario_result}")

    overlays = build_overlays(NET_FILE, NOD_FILE, RUN_ID, anchor)
    for gt in overlays["ground_truth"]:
        ground_truth.append({**gt, "synthetic_overlay": True})

    ground_truth.sort(key=lambda g: g["scenario_id"])

    ground_truth_path = run_dir / "ground_truth.jsonl"
    with ground_truth_path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in ground_truth:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    overlay_events_path = run_dir / "overlay_events.jsonl"
    with overlay_events_path.open("w", encoding="utf-8", newline="\n") as stream:
        for event in overlays["events"]:
            stream.write(json.dumps(event, sort_keys=True) + "\n")

    physical_summary_path = run_dir / "physical_summary.json"
    physical_summary_path.write_text(
        json.dumps(physical_summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    return {
        "ground_truth_path": ground_truth_path,
        "overlay_events_path": overlay_events_path,
        "physical_summary_path": physical_summary_path,
        "scenario_count": len(ground_truth),
        "overlay_event_count": len(overlays["events"]),
        "physical_summary": physical_summary,
    }


def main() -> None:
    if not NET_FILE.is_file():
        raise SystemExit(f"missing {NET_FILE}; run P03.01's network build_and_verify.py first")
    OUTPUT_DIR.mkdir(exist_ok=True)

    run_a = build_one_run("run-a")
    run_b = build_one_run("run-b")

    for key in ("ground_truth_path", "overlay_events_path", "physical_summary_path"):
        text_a = run_a[key].read_text(encoding="utf-8")
        text_b = run_b[key].read_text(encoding="utf-8")
        if text_a != text_b:
            raise SystemExit(f"{key} is not byte-identical across two runs of the same seed/run_id")

    scenario_types = {
        json.loads(line)["scenario_type"]
        for line in run_a["ground_truth_path"].read_text(encoding="utf-8").splitlines()
    }
    required = {
        "normal",
        "peak",
        "event",
        "stall",
        "collision",
        "wrong_way",
        "flood",
        "visibility",
        "signal",
    }
    missing = required - scenario_types
    if missing:
        raise SystemExit(f"missing required scenario types: {sorted(missing)}")

    summary = {
        "seed": SEED,
        "run_id": RUN_ID,
        "anchor_utc": ANCHOR_UTC,
        "sim_end_seconds": SIM_END,
        "scenario_count": run_a["scenario_count"],
        "overlay_event_count": run_a["overlay_event_count"],
        "scenario_types": sorted(scenario_types),
        "physical_summary": run_a["physical_summary"],
        "deterministic": True,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
