"""Phase 06 traffic-intelligence dataset: long, time-varying-demand SUMO runs
with loop-detector telemetry (model/service input), SUMO's own edge-wide
measurements (independent ground truth for KPI validation and congestion
labels) and physically simulated blockage incidents (labels).

Runs inside the pinned ghcr.io/eclipse-sumo/sumo image (see
source-code/simulator/network/run_container.sh for the digest); needs
source-code/simulator/network/output/district.net.xml (P03.01).

Built because no dataset long enough for 5/15/30-minute forecasting, or with
edge-wide ground truth for congestion/spillback, existed: P03's runs are
600 s and P04's are 900 s. Reuses P03.08's seed->split assignment (train 5 /
validation 2 / test 2 seeds), so a seed appears in exactly one split, and
reuses P04's blockage physics/labelling helpers unchanged.

Per seed, four independent 180-minute runs (30 s loop intervals):
- am-peak           incident-free, one Gaussian morning peak
- pm-double         incident-free, two smaller peaks
- midday-steady     incident-free, low sinusoidal demand (hard negatives)
- am-peak-blockage  the am-peak demand shape plus three physical blockages
                    (two stopped vehicles, one per general lane, 25/45/70 m
                    past a corridor loop) whose measured onset/end are the labels

Demand is passenger vehicles only (no cyclists/pedestrians/transit): this
dataset is for vehicle KPIs, forecasting and congestion, not VRU conflicts
(P06.06 uses P03.02's VRU trajectories). Loops sit on one of each corridor
edge's two general lanes, so counts are per instrumented lane.

Ground truth kept out of features (per docs/PROJECT_CONTEXT.md):
- truth.jsonl     SUMO edgeData per (edge, 30 s): speed, density, waiting time...
- labels.jsonl / incidents.jsonl  blockage labels, from SUMO's stop-output

Outputs (per run, under output/<label>/<split>/<run_id>/) plus
output/<label>/dataset_manifest.json (sha256 for every file) and
output/<label>/segments.json (edge topology from the real net file).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import subprocess
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[1]
SIM = SOURCE / "simulator"
sys.path.insert(0, str(SOURCE / "models" / "dataset"))
import build_edge_dataset as p04  # noqa: E402  (sets up simulator helper import paths)

OUTPUT_DIR = HERE / "output"
NET_FILE = p04.NET_FILE
NOD_FILE = p04.NOD_FILE
VTYPES_FILE = p04.VTYPES_FILE

DemandEntity = p04.DemandEntity
StallPlan = p04.StallPlan
VEHICLE_ROUTES = p04.VEHICLE_ROUTES

ANCHOR_UTC = p04.ANCHOR_UTC
SIM_END = 10800
INTERVAL_S = p04.INTERVAL_S
GEOMETRY_VERSION = p04.GEOMETRY_VERSION
LOOP_POS = p04.LOOP_POS
INCIDENT_WINDOW_S = (2400.0, 7800.0)

# (name, demand profile, incidents per run)
RUN_SPECS: list[tuple[str, str, int]] = [
    ("am-peak", "am_peak", 0),
    ("pm-double", "pm_double", 0),
    ("midday-steady", "midday", 0),
    ("am-peak-blockage", "am_peak", 3),
]


def sample_profile(rng: random.Random, profile: str) -> dict:
    """Per-run demand parameters, vehicles/minute across the whole network."""
    if profile == "am_peak":
        return {
            "base": rng.uniform(14.0, 24.0),
            "peak": rng.uniform(35.0, 62.0),
            "t0": rng.uniform(70.0, 95.0),
            "sigma": rng.uniform(16.0, 26.0),
        }
    if profile == "pm_double":
        return {
            "base": rng.uniform(14.0, 24.0),
            "peak": rng.uniform(25.0, 45.0),
            "t0": rng.uniform(38.0, 55.0),
            "sigma": rng.uniform(11.0, 17.0),
            "peak2": rng.uniform(35.0, 58.0),
            "t1": rng.uniform(108.0, 132.0),
            "sigma2": rng.uniform(11.0, 17.0),
        }
    return {
        "base": rng.uniform(28.0, 38.0),
        "amp": rng.uniform(3.0, 7.0),
        "period": rng.uniform(30.0, 50.0),
        "phase": rng.uniform(0.0, 2 * math.pi),
    }


def rate_per_min(profile: str, t_min: float, p: dict) -> float:
    if profile == "am_peak":
        return p["base"] + p["peak"] * math.exp(-((t_min - p["t0"]) ** 2) / (2 * p["sigma"] ** 2))
    if profile == "pm_double":
        return (
            p["base"]
            + p["peak"] * math.exp(-((t_min - p["t0"]) ** 2) / (2 * p["sigma"] ** 2))
            + p["peak2"] * math.exp(-((t_min - p["t1"]) ** 2) / (2 * p["sigma2"] ** 2))
        )
    return max(1.0, p["base"] + p["amp"] * math.sin(2 * math.pi * t_min / p["period"] + p["phase"]))


def vehicle_entities(rng: random.Random, run_id: str, profile: str, params: dict) -> list:
    """Inhomogeneous Poisson departures (thinning), uniform over P03.02's routes."""
    grid = [rate_per_min(profile, m / 2.0, params) for m in range(0, SIM_END // 30 + 1)]
    rate_max = max(grid) * 1.05
    entities = []
    t = 0.0
    index = 0
    while True:
        t += rng.expovariate(rate_max / 60.0)
        if t >= SIM_END:
            break
        if rng.random() <= rate_per_min(profile, t / 60.0, params) / rate_max:
            route_id, _ = rng.choice(VEHICLE_ROUTES)
            entities.append(
                DemandEntity("vehicle", f"{run_id}-veh-{index:05d}", int(t), route_id, None, None)
            )
            index += 1
    return entities


def plan_incidents_long(rng: random.Random, run_id: str, loop_devices: list[dict], count: int):
    """Same blockage physics as P04's plan_incidents, onsets spread across the long run."""
    routes = dict(VEHICLE_ROUTES)
    by_corridor: dict[str, list[dict]] = {}
    for device in loop_devices:
        by_corridor.setdefault(device["location"]["corridor_id"], []).append(device)
    entities, plans, planned = [], [], []
    for corridor in sorted(by_corridor)[:count]:
        device = rng.choice(sorted(by_corridor[corridor], key=lambda d: d["device_id"]))
        edge = p04._edge_of(device)
        target_onset = rng.uniform(*INCIDENT_WINDOW_S)
        duration = rng.choice([120, 180, 240, 300, 420])
        candidates = sorted(rid for rid, edges in routes.items() if edge in edges)
        route_id = rng.choice(candidates)
        index = routes[route_id].index(edge)
        lead = index * p04.EDGE_LENGTH_ESTIMATE_M / p04.TRAVEL_SPEED_ESTIMATE_M_S
        depart = max(0, round(target_onset - lead))
        incident_id = f"{run_id}-inc-{corridor}"
        stall_pos = rng.choice(p04.STALL_DISTANCES_M)
        blockers = []
        for lane_index, extra_depart, extra_duration in ((2, 0, 0), (3, 2, 10)):
            vehicle_id = f"{incident_id}-blk-l{lane_index}"
            blockers.append(vehicle_id)
            entities.append(
                DemandEntity("vehicle", vehicle_id, depart + extra_depart, route_id, None, None)
            )
            plans.append(
                StallPlan(
                    vehicle_id=vehicle_id,
                    edge_id=edge,
                    lane_id=f"{edge}_{lane_index}",
                    start_pos=LOOP_POS + stall_pos,
                    end_pos=LOOP_POS + stall_pos + p04.STALL_LENGTH_M,
                    duration_s=duration + extra_duration,
                )
            )
        planned.append(
            {
                "incident_id": incident_id,
                "corridor_id": corridor,
                "device_id": device["device_id"],
                "edge_id": edge,
                "stall_distance_from_loop_m": stall_pos,
                "blocker_vehicle_ids": blockers,
            }
        )
    return entities, plans, planned


def write_configs(run_dir: Path, route_file: Path, loop_devices: list[dict], seed: int) -> Path:
    add = ["<additional>"]
    for device in loop_devices:
        add.append(
            f'    <inductionLoop id="{device["device_id"]}" lane="{device["location"]["lane_id"]}" '
            f'pos="{LOOP_POS}" freq="{INTERVAL_S}" file="loop-output.xml"/>'
        )
    add.append(
        f'    <edgeData id="truth" freq="{INTERVAL_S}" file="edgedata.xml" excludeEmpty="true"/>'
    )
    add.append("</additional>")
    add_file = run_dir / "detectors.add.xml"
    add_file.write_text("\n".join(add) + "\n", encoding="utf-8", newline="\n")

    sumocfg = run_dir / "run.sumocfg"
    sumocfg.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <input>
        <net-file value="{NET_FILE}"/>
        <route-files value="{route_file}"/>
        <additional-files value="{VTYPES_FILE},{add_file}"/>
    </input>
    <time><begin value="0"/><end value="{SIM_END}"/></time>
    <processing><time-to-teleport value="-1"/></processing>
    <random_number><seed value="{seed}"/></random_number>
    <output>
        <statistic-output value="{run_dir / "statistics.xml"}"/>
        <stop-output value="{run_dir / "stopinfo.xml"}"/>
    </output>
</configuration>
""",
        encoding="utf-8",
    )
    return sumocfg


def _num(value: str | None) -> float | None:
    try:
        return round(float(value), 4) if value is not None else None
    except ValueError:
        return None


def parse_truth(path: Path) -> list[dict]:
    rows = []
    for interval in ET.parse(path).getroot().findall("interval"):
        begin, end = float(interval.get("begin")), float(interval.get("end"))
        for edge in interval.findall("edge"):
            rows.append(
                {
                    "edge_id": edge.get("id"),
                    "begin_s": begin,
                    "end_s": end,
                    "speed_m_s": _num(edge.get("speed")),
                    "speed_relative": _num(edge.get("speedRelative")),
                    "density_veh_km": _num(edge.get("density")),
                    "occupancy_pct": _num(edge.get("occupancy")),
                    "waiting_s": _num(edge.get("waitingTime")),
                    "time_loss_s": _num(edge.get("timeLoss")),
                    "travel_time_s": _num(edge.get("traveltime")),
                    "entered": int(edge.get("entered", 0)),
                    "departed": int(edge.get("departed", 0)),
                    "left": int(edge.get("left", 0)),
                    "arrived": int(edge.get("arrived", 0)),
                    "sampled_seconds": _num(edge.get("sampledSeconds")),
                }
            )
    return rows


def build_segments() -> list[dict]:
    root = ET.parse(NET_FILE).getroot()
    segments = []
    for edge in root.findall("edge"):
        edge_id = edge.get("id")
        if edge_id.startswith(":") or edge.get("function") == "internal":
            continue
        from_node, to_node = edge.get("from"), edge.get("to")
        lanes = [
            {
                "index": int(lane.get("index")),
                "length_m": float(lane.get("length")),
                "speed_m_s": float(lane.get("speed")),
            }
            for lane in edge.findall("lane")
        ]
        same_corridor = from_node[4] == to_node[4]
        segments.append(
            {
                "edge_id": edge_id,
                "from_node": from_node,
                "to_node": to_node,
                "corridor_id": f"corridor-{from_node[4]}" if same_corridor else None,
                "direction": ("east" if from_node[5:] < to_node[5:] else "west")
                if same_corridor
                else "cross",
                "order": (
                    int(from_node[5:]) if from_node[5:] < to_node[5:] else 5 - int(from_node[5:])
                )
                if same_corridor
                else None,
                "lanes": lanes,
            }
        )
    return sorted(segments, key=lambda s: s["edge_id"])


def write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def build_run(task: tuple) -> dict:
    split, seed, spec_index, spec, loop_devices, out_root = task
    name, profile, incident_count = spec
    out_root = Path(out_root)
    run_id = f"p06-{split}-s{seed}-{name}"
    run_dir = out_root / split / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    anchor = datetime.fromisoformat(ANCHOR_UTC.replace("Z", "+00:00")).astimezone(timezone.utc)
    derived_seed = seed * 100 + spec_index
    profile_params = sample_profile(random.Random(f"{derived_seed}:{name}:intel-profile"), profile)
    entities = vehicle_entities(
        random.Random(f"{derived_seed}:{name}:intel-demand"), run_id, profile, profile_params
    )

    plans: list = []
    planned: list[dict] = []
    if incident_count:
        extra, plans, planned = plan_incidents_long(
            random.Random(f"{derived_seed}:{name}:intel-incidents"),
            run_id,
            loop_devices,
            incident_count,
        )
        entities = sorted(entities + extra, key=lambda e: (e.depart, e.entity_id))

    route_file = run_dir / "demand.rou.xml"
    p04.write_route_file(entities, route_file)
    if plans:
        p04.inject_stops(route_file, plans)
    sumocfg = write_configs(run_dir, route_file, loop_devices, derived_seed)
    subprocess.run(
        ["sumo", "-c", str(sumocfg), "--no-step-log", "--duration-log.disable"],
        check=True,
        capture_output=True,
        text=True,
        cwd=run_dir,
    )

    stats = ET.parse(run_dir / "statistics.xml").getroot()
    vehicles = stats.find("vehicles")
    teleports = int(stats.find("teleports").get("total", "-1"))
    collisions = int(stats.find("safety").get("collisions", "-1"))
    incidents = p04.measured_incidents(run_dir, planned)

    events = p04.traffic_events(
        loop_devices,
        p04.parse_loop_intervals(run_dir / "loop-output.xml"),
        anchor,
        run_id,
        p04.SequenceCounter(),
        f"intel-{run_id}",
        "traffic.loop_detector.count",
        "vehicle_count",
    )
    events.sort(key=lambda e: (e["observation_time"], e["device_id"], e["sequence_number"]))
    meta = {"run_id": run_id, "split": split, "seed": seed, "run_spec": name, "profile": profile}
    write_jsonl(run_dir / "events.jsonl", events)
    write_jsonl(
        run_dir / "truth.jsonl", [{**meta, **row} for row in parse_truth(run_dir / "edgedata.xml")]
    )
    if incident_count:
        labels = p04.label_rows(
            events,
            loop_devices,
            incidents,
            {**meta, "scenario": "blockage", "demand_scale": None},
            anchor,
        )
        write_jsonl(run_dir / "labels.jsonl", labels)
        write_jsonl(run_dir / "incidents.jsonl", [{**meta, **i} for i in incidents])
    (run_dir / "demand_summary.json").write_text(
        json.dumps(
            {
                **meta,
                "vehicles_generated": len(entities),
                "profile_params": {k: round(v, 4) for k, v in profile_params.items()},
                "rate_samples_per_min": [
                    round(rate_per_min(profile, m, profile_params), 3)
                    for m in range(0, SIM_END // 60, 5)
                ],
            },
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    # bulky SUMO intermediates are not part of the dataset
    for junk in (
        "loop-output.xml",
        "edgedata.xml",
        "statistics.xml",
        "stopinfo.xml",
        "run.sumocfg",
        "detectors.add.xml",
        "demand.rou.xml",
    ):
        (run_dir / junk).unlink(missing_ok=True)

    return {
        "run_id": run_id,
        "split": split,
        "run_spec": name,
        "events": len(events),
        "vehicles_generated": len(entities),
        "inserted": int(vehicles.get("inserted", -1)),
        "running_at_end": int(vehicles.get("running", -1)),
        "waiting_at_end": int(vehicles.get("waiting", -1)),
        "incidents_planned": len(planned),
        "incidents_valid": sum(1 for i in incidents if i["valid"]),
        "positive_labels": sum(r["label"] for r in labels) if incident_count else 0,
        "teleports": teleports,
        "collisions": collisions,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_all(label: str, only_seed: int | None, only_spec: str | None, workers: int) -> dict:
    out_root = OUTPUT_DIR / label
    catalog = p04.build_catalog(NET_FILE, NOD_FILE)
    loop_devices = [d for d in catalog["devices"] if d["device_type"] == "inductive_loop"]
    split_seeds = p04._load_split_seeds()
    out_root.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_root / "devices.jsonl", loop_devices)
    (out_root / "segments.json").write_text(
        json.dumps(build_segments(), indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )

    tasks = []
    for split, seeds in split_seeds.items():
        for seed in seeds:
            if only_seed is not None and seed != only_seed:
                continue
            for index, spec in enumerate(RUN_SPECS):
                if only_spec is not None and spec[0] != only_spec:
                    continue
                tasks.append((split, seed, index, spec, loop_devices, str(out_root)))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        runs = list(pool.map(build_run, tasks))

    files = sorted(
        p for p in out_root.rglob("*") if p.is_file() and p.name != "dataset_manifest.json"
    )
    hashes = {str(p.relative_to(out_root)).replace("\\", "/"): _sha256(p) for p in files}
    digest = hashlib.sha256(
        "\n".join(f"{k}:{v}" for k, v in sorted(hashes.items())).encode()
    ).hexdigest()
    manifest = {
        "schema": "intelligence-dataset-manifest-v1",
        "geometry_version": GEOMETRY_VERSION,
        "anchor_utc": ANCHOR_UTC,
        "sim_end_s": SIM_END,
        "interval_s": INTERVAL_S,
        "split_seeds": split_seeds,
        "run_specs": [{"name": n, "profile": pr, "incidents": i} for n, pr, i in RUN_SPECS],
        "dataset_sha256": digest,
        "files": hashes,
        "runs": sorted(runs, key=lambda r: r["run_id"]),
    }
    (out_root / "dataset_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("seed", nargs="?", type=int, default=None)
    parser.add_argument("--only", default=None, help="only this run spec name")
    parser.add_argument("--workers", type=int, default=int(os.environ.get("BUILD_WORKERS", "4")))
    args = parser.parse_args()
    if not NET_FILE.is_file():
        raise SystemExit(f"missing {NET_FILE}; run P03.01's network build first")
    OUTPUT_DIR.mkdir(exist_ok=True)

    smoke = args.seed is not None or args.only is not None
    first = build_all("smoke" if smoke else "run-a", args.seed, args.only, args.workers)
    summary = {"dataset_sha256": first["dataset_sha256"], "runs": first["runs"]}
    if not smoke:
        # Determinism proof on one seed's four runs built independently a second time.
        twin_seed = first["split_seeds"]["train"][0]
        twin = build_all("run-b", twin_seed, None, args.workers)
        differing = sorted(k for k, v in twin["files"].items() if first["files"].get(k) != v)
        if differing:
            raise SystemExit(
                f"twin rebuild of seed {twin_seed} is not byte-identical: {differing[:5]}"
            )
        summary["deterministic_twin_seed"] = twin_seed
        summary["deterministic_twin_files_identical"] = len(twin["files"])
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
