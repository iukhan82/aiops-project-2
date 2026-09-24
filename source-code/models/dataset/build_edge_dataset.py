"""Edge-model dataset: real SUMO loop-detector telemetry with physically
simulated road-blockage incidents and separate ground truth.

Runs inside the pinned ghcr.io/eclipse-sumo/sumo image (see
source-code/simulator/network/run_container.sh for the digest); needs
source-code/simulator/network/output/district.net.xml (P03.01).

Reuses the P03.08 seed->split assignment (train 5 / validation 2 / test 2
seeds), so a seed appears in exactly one split; every run_id embeds its
split and seed, so no event_id/entity_id can cross a split.

Per seed, twelve independent SUMO runs (900 s each): four incident-free runs
at 1x/6x/12x/24x demand (the dense ones are the hard negatives for any
congestion-vs-blockage detector) and eight blockage runs (two each at
3x/6x/12x/24x), each with one incident on each of the three corridors.

A blockage is two stopped passenger vehicles, one per general lane, on the
same corridor edge 25/45/70 m past its loop detector (drawn per incident). It is physical: SUMO
queues traffic behind it (teleporting disabled so queues are not silently
erased). Ground-truth onset/end come from SUMO's own stop-output
(`started`/`ended` of both blockers; the incident is active on
[max(started), min(ended)]), never from what was planned.

Outputs (per run, under output/<split>/<run_id>/):
- events.jsonl  observation-envelope/v1 loop-detector events (model input)
- labels.jsonl  per (device, interval) ground truth (kept out of features)
- incidents.jsonl measured incident windows
plus output/dataset_manifest.json with a sha256 for every file.

Physical limit (by design, not hidden): a single loop only sees the queue
once it grows back to the detector, so incidents farther from the loop, or
at low flow, are inherently detected late or not at all. Labels are ground
truth (blockage active), not 'detectable yet'; evaluation reports detection
delay separately.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[1]
SIM = SOURCE / "simulator"
NET_FILE = SIM / "network" / "output" / "district.net.xml"
NOD_FILE = SIM / "network" / "plain" / "district.nod.xml"
VTYPES_FILE = SIM / "network" / "demand" / "vtypes.add.xml"
STOPS_FILE = SIM / "demand" / "stops.add.xml"
OUTPUT_DIR = HERE / "output"

for sub in ("sensors", "scenarios", "demand"):
    sys.path.insert(0, str(SIM / sub))
from build_sensor_catalog import build_catalog  # noqa: E402
from generate_demand import VEHICLE_ROUTES, DemandEntity, write_route_file  # noqa: E402
from generate_observations import SequenceCounter, parse_loop_intervals, traffic_events  # noqa: E402
from generate_physical_demand import StallPlan, generate_scaled_entities, inject_stops  # noqa: E402

ANCHOR_UTC = "2026-09-18T09:00:00Z"
SIM_END = 900
INTERVAL_S = 30
LOOP_POS = 10
STALL_DISTANCES_M = (25, 45, 70)  # blockage position past the loop (loop at pos 10)
STALL_LENGTH_M = 6
LABEL_MIN_OVERLAP_S = 15.0
EDGE_LENGTH_ESTIMATE_M = 290.0
TRAVEL_SPEED_ESTIMATE_M_S = 11.0
GEOMETRY_VERSION = "2026-09-18.1"

# (name, demand scale, incidents per run)
RUN_SPECS: list[tuple[str, float, int]] = [
    ("normal-1x", 1.0, 0),
    ("dense-6x", 6.0, 0),
    ("dense-12x", 12.0, 0),
    ("dense-24x", 24.0, 0),
    ("blockage-3x-a", 3.0, 3),
    ("blockage-3x-b", 3.0, 3),
    ("blockage-6x-a", 6.0, 3),
    ("blockage-6x-b", 6.0, 3),
    ("blockage-12x-a", 12.0, 3),
    ("blockage-12x-b", 12.0, 3),
    ("blockage-24x-a", 24.0, 3),
    ("blockage-24x-b", 24.0, 3),
]


def _load_split_seeds() -> dict[str, list[int]]:
    path = SIM / "datasets" / "build_and_verify.py"
    spec = importlib.util.spec_from_file_location("p0308_build_and_verify", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["p0308_build_and_verify"] = module
    spec.loader.exec_module(module)
    return module.SPLIT_SEEDS


def _iso(anchor: datetime, offset_s: float) -> str:
    from datetime import timedelta

    dt = anchor + timedelta(seconds=offset_s)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _edge_of(device: dict) -> str:
    return device["location"]["lane_id"].rsplit("_", 1)[0]


def plan_incidents(
    rng: random.Random, run_id: str, loop_devices: list[dict], count: int
) -> tuple[list[DemandEntity], list[StallPlan], list[dict]]:
    """One incident per corridor a/b/c (first `count` of them)."""
    routes = dict(VEHICLE_ROUTES)
    by_corridor: dict[str, list[dict]] = {}
    for device in loop_devices:
        by_corridor.setdefault(device["location"]["corridor_id"], []).append(device)

    entities: list[DemandEntity] = []
    plans: list[StallPlan] = []
    planned: list[dict] = []
    for corridor in sorted(by_corridor)[:count]:
        device = rng.choice(sorted(by_corridor[corridor], key=lambda d: d["device_id"]))
        edge = _edge_of(device)
        target_onset = rng.uniform(150.0, 500.0)
        duration = rng.choice([90, 120, 150, 180, 240])
        candidates = sorted(rid for rid, edges in routes.items() if edge in edges)
        route_id = rng.choice(candidates)
        index = routes[route_id].index(edge)
        lead = index * EDGE_LENGTH_ESTIMATE_M / TRAVEL_SPEED_ESTIMATE_M_S
        depart = max(0, round(target_onset - lead))
        incident_id = f"{run_id}-inc-{corridor}"
        stall_pos = rng.choice(STALL_DISTANCES_M)
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
                    end_pos=LOOP_POS + stall_pos + STALL_LENGTH_M,
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
    loops = ["<additional>"]
    for device in loop_devices:
        loops.append(
            f'    <inductionLoop id="{device["device_id"]}" lane="{device["location"]["lane_id"]}" '
            f'pos="{LOOP_POS}" freq="{INTERVAL_S}" file="loop-output.xml"/>'
        )
    loops.append("</additional>")
    loops_file = run_dir / "loops.add.xml"
    loops_file.write_text("\n".join(loops) + "\n", encoding="utf-8", newline="\n")

    sumocfg = run_dir / "run.sumocfg"
    sumocfg.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <input>
        <net-file value="{NET_FILE}"/>
        <route-files value="{route_file}"/>
        <additional-files value="{VTYPES_FILE},{STOPS_FILE},{loops_file}"/>
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


def measured_incidents(run_dir: Path, planned: list[dict]) -> list[dict]:
    stops = {
        s.get("id"): (float(s.get("started")), float(s.get("ended")))
        for s in ET.parse(run_dir / "stopinfo.xml").getroot().findall("stopinfo")
        if s.get("started") and s.get("ended")
    }
    out = []
    for incident in planned:
        windows = [stops[v] for v in incident["blocker_vehicle_ids"] if v in stops]
        record = dict(incident)
        if len(windows) == len(incident["blocker_vehicle_ids"]):
            onset = max(w[0] for w in windows)
            end = min(w[1] for w in windows)
            record.update(valid=end > onset, onset_s=onset, end_s=end)
        else:
            record.update(valid=False, onset_s=None, end_s=None)
        out.append(record)
    return out


def label_rows(
    events: list[dict],
    loop_devices: list[dict],
    incidents: list[dict],
    meta: dict,
    anchor: datetime,
) -> list[dict]:
    edge_by_device = {d["device_id"]: _edge_of(d) for d in loop_devices}
    rows = []
    for event in events:
        end_s = (
            datetime.fromisoformat(event["observation_time"].replace("Z", "+00:00")) - anchor
        ).total_seconds()
        begin_s = end_s - INTERVAL_S
        label, incident_id, stall_distance = 0, None, None
        for incident in incidents:
            if not incident["valid"] or incident["edge_id"] != edge_by_device[event["device_id"]]:
                continue
            overlap = min(end_s, incident["end_s"]) - max(begin_s, incident["onset_s"])
            if overlap >= LABEL_MIN_OVERLAP_S:
                label, incident_id = 1, incident["incident_id"]
                stall_distance = incident["stall_distance_from_loop_m"]
        rows.append(
            {
                **meta,
                "event_id": event["event_id"],
                "device_id": event["device_id"],
                "interval_end": event["observation_time"],
                "label": label,
                "incident_id": incident_id,
                "stall_distance_from_loop_m": stall_distance,
            }
        )
    return rows


def build_run(
    split: str,
    seed: int,
    spec_index: int,
    spec: tuple[str, float, int],
    loop_devices: list[dict],
    out_root: Path,
) -> dict:
    name, scale, incident_count = spec
    run_id = f"p04-{split}-s{seed}-{name}"
    run_dir = out_root / split / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    anchor = datetime.fromisoformat(ANCHOR_UTC.replace("Z", "+00:00")).astimezone(timezone.utc)
    derived_seed = seed * 100 + spec_index
    rng = random.Random(f"{derived_seed}:{name}:edge-dataset")

    entities = generate_scaled_entities(derived_seed, run_id, SIM_END, scale)
    plans: list[StallPlan] = []
    planned: list[dict] = []
    if incident_count:
        extra, plans, planned = plan_incidents(rng, run_id, loop_devices, incident_count)
        entities = sorted(entities + extra, key=lambda e: (e.depart, e.entity_id))

    route_file = run_dir / "demand.rou.xml"
    write_route_file(entities, route_file)
    if plans:
        inject_stops(route_file, plans)
    sumocfg = write_configs(run_dir, route_file, loop_devices, derived_seed)
    subprocess.run(
        ["sumo", "-c", str(sumocfg), "--no-step-log", "--duration-log.disable"],
        check=True,
        capture_output=True,
        text=True,
        cwd=run_dir,
    )

    stats = ET.parse(run_dir / "statistics.xml").getroot()
    teleports = int(stats.find("teleports").get("total", "-1"))
    collisions = int(stats.find("safety").get("collisions", "-1"))
    incidents = measured_incidents(run_dir, planned)

    seq = SequenceCounter()
    events = traffic_events(
        loop_devices,
        parse_loop_intervals(run_dir / "loop-output.xml"),
        anchor,
        run_id,
        seq,
        f"edge-dataset-{run_id}",
        "traffic.loop_detector.count",
        "vehicle_count",
    )
    events.sort(key=lambda e: (e["observation_time"], e["device_id"], e["sequence_number"]))
    meta = {
        "run_id": run_id,
        "split": split,
        "seed": seed,
        "scenario": "blockage" if incident_count else name.split("-")[0],
        "run_spec": name,
        "demand_scale": scale,
    }
    labels = label_rows(events, loop_devices, incidents, meta, anchor)

    for filename, records in (
        ("events.jsonl", events),
        ("labels.jsonl", labels),
        ("incidents.jsonl", [{**meta, **i} for i in incidents]),
    ):
        with (run_dir / filename).open("w", encoding="utf-8", newline="\n") as stream:
            for record in records:
                stream.write(json.dumps(record, sort_keys=True) + "\n")

    return {
        "run_id": run_id,
        "split": split,
        "events": len(events),
        "positive_labels": sum(r["label"] for r in labels),
        "incidents_planned": len(planned),
        "incidents_valid": sum(1 for i in incidents if i["valid"]),
        "teleports": teleports,
        "collisions": collisions,
    }


def build_all(label: str, only_seed: int | None = None) -> dict:
    out_root = OUTPUT_DIR / label
    catalog = build_catalog(NET_FILE, NOD_FILE)
    loop_devices = [d for d in catalog["devices"] if d["device_type"] == "inductive_loop"]
    split_seeds = _load_split_seeds()

    out_root.mkdir(parents=True, exist_ok=True)
    with (out_root / "devices.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for device in loop_devices:
            stream.write(json.dumps(device, sort_keys=True) + "\n")

    runs = []
    for split, seeds in split_seeds.items():
        for seed in seeds:
            if only_seed is not None and seed != only_seed:
                continue
            for index, spec in enumerate(RUN_SPECS):
                runs.append(build_run(split, seed, index, spec, loop_devices, out_root))

    files = sorted(p for p in out_root.rglob("*") if p.is_file() and p.suffix in {".jsonl"})
    hashes = {str(p.relative_to(out_root)).replace("\\", "/"): _sha256(p) for p in files}
    digest = hashlib.sha256(
        "\n".join(f"{k}:{v}" for k, v in sorted(hashes.items())).encode()
    ).hexdigest()
    manifest = {
        "schema": "edge-dataset-manifest-v1",
        "geometry_version": GEOMETRY_VERSION,
        "anchor_utc": ANCHOR_UTC,
        "sim_end_s": SIM_END,
        "interval_s": INTERVAL_S,
        "split_seeds": split_seeds,
        "run_specs": [{"name": n, "scale": s, "incidents": i} for n, s, i in RUN_SPECS],
        "dataset_sha256": digest,
        "files": hashes,
        "runs": runs,
    }
    (out_root / "dataset_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    if not NET_FILE.is_file():
        raise SystemExit(f"missing {NET_FILE}; run P03.01's network build first")
    only_seed = int(sys.argv[1]) if len(sys.argv) > 1 else None
    OUTPUT_DIR.mkdir(exist_ok=True)

    first = build_all("run-a", only_seed)
    second = build_all("run-b", only_seed)
    if first["files"] != second["files"]:
        differing = sorted(k for k in first["files"] if first["files"][k] != second["files"].get(k))
        raise SystemExit(f"dataset is not byte-identical across two builds: {differing[:5]}")

    runs = first["runs"]
    by_split: dict[str, dict[str, int]] = {}
    for run in runs:
        agg = by_split.setdefault(
            run["split"],
            {
                "runs": 0,
                "events": 0,
                "positive_labels": 0,
                "incidents_valid": 0,
                "incidents_planned": 0,
            },
        )
        agg["runs"] += 1
        for key in ("events", "positive_labels", "incidents_valid", "incidents_planned"):
            agg[key] += run[key]
    summary = {
        "dataset_sha256": first["dataset_sha256"],
        "deterministic": True,
        "by_split": by_split,
        "teleports_total": sum(r["teleports"] for r in runs),
        "collisions_total": sum(r["collisions"] for r in runs),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
