"""Phase 06 VRU (pedestrian/cyclist) conflict dataset: real SUMO runs with
pedestrians crossing at the network's marked crossings, cyclists in the bike
lanes and passenger vehicles, with full-rate trajectories around every
intersection.

Runs inside the pinned ghcr.io/eclipse-sumo/sumo image (see
source-code/simulator/network/run_container.sh for the digest); needs
source-code/simulator/network/output/district.net.xml (P03.01).

Why a new dataset: P03.02's pedestrians walk single sidewalk edges (they never
cross a road) and P04/P06.01's runs carry no VRUs, so neither can contain a
vehicle-VRU conflict. Here pedestrians route between random sidewalk edges via
SUMO's intermodal router, which sends them over the guessed crossings.

Per seed (P03.08's seed->split assignment: train 5 / validation 2 / test 2) two
independent 30-minute runs at 0.25 s steps:
- peak     1500 veh/h, 1200 pedestrians/h, 200 cyclists/h
- offpeak   600 veh/h,  300 pedestrians/h,  50 cyclists/h  (thin sites: the
           regime where a k-anonymity floor suppresses most windows)

Behavioural mix (modelling assumptions, not measured populations):
- 25% of drivers are `passenger-assertive` (jmCrossingGap 1 m, impatience 1,
  sigma 0.7) - SUMO's default driver always leaves a 10 m gap to a pedestrian,
  which would produce almost no conflicts to detect;
- 25% of pedestrians are `ped-slow` (0.8 m/s, e.g. mobility-limited) - the
  detector never sees this class; it exists so bias against slow crossers can
  be measured.

Only trajectory rows within SITE_RADIUS_M of an intersection centre are kept
(streamed out of SUMO's FCD output, never written whole). That is the scope of
the conflict analysis: intersection sites.

Ground truth stays out of any feature: `tracks.csv.gz` is the clean trajectory
record used only to derive *realized* post-encroachment times (truth.py);
`sub` (vehicle/person type) is analysis-only.

Outputs per run under output/<label>/<split>/<run_id>/: tracks.csv.gz,
run.json; plus output/<label>/sites.json and dataset_manifest.json (sha256 of
every file).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[1]
sys.path.insert(0, str(SOURCE / "models" / "dataset"))
import build_edge_dataset as p04  # noqa: E402  (sets up simulator helper import paths)

OUTPUT_DIR = HERE / "output"
NET_FILE = p04.NET_FILE
VEHICLE_ROUTES = p04.VEHICLE_ROUTES

SIM_END = 1800
STEP_S = 0.25
SITE_RADIUS_M = 40.0
ANCHOR_UTC = p04.ANCHOR_UTC
GEOMETRY_VERSION = p04.GEOMETRY_VERSION

# (name, vehicles/h, pedestrians/h, cyclists/h)
RUN_SPECS: list[tuple[str, int, int, int]] = [("peak", 1500, 1200, 200), ("offpeak", 600, 300, 50)]
ASSERTIVE_SHARE = 0.25
SLOW_PED_SHARE = 0.25

VTYPES = """    <vType id="passenger" vClass="passenger" length="4.5" maxSpeed="16.7"/>
    <vType id="passenger-assertive" vClass="passenger" length="4.5" maxSpeed="16.7" jmCrossingGap="1" impatience="1.0" sigma="0.7"/>
    <vType id="cyclist" vClass="bicycle" length="1.8" maxSpeed="6.0"/>
    <vType id="ped-normal" vClass="pedestrian"/>
    <vType id="ped-slow" vClass="pedestrian" maxSpeed="0.8"/>
"""

ROW = re.compile(
    r'<(vehicle|person) id="([^"]+)" x="([^"]+)" y="([^"]+)" angle="[^"]+" type="([^"]+)" speed="([^"]+)"'
)
STEP_TAG = re.compile(r'<timestep time="([^"]+)"')


def load_sites(net_file: Path) -> list[dict]:
    root = ET.parse(net_file).getroot()
    return [
        {"site_id": j.get("id"), "x": float(j.get("x")), "y": float(j.get("y"))}
        for j in root.findall("junction")
        if j.get("type") != "internal" and j.get("id", "").startswith("int-")
    ]


def walkable_edges(net_file: Path) -> list[str]:
    edges = []
    for edge in ET.parse(net_file).getroot().findall("edge"):
        if edge.get("id", "").startswith(":") or edge.get("function") is not None:
            continue
        if any("pedestrian" in (lane.get("allow") or "") for lane in edge.findall("lane")):
            edges.append(edge.get("id"))
    return sorted(edges)


def write_routes(path: Path, seed: int, spec: tuple[str, int, int, int]) -> dict:
    name, veh_h, ped_h, cyc_h = spec
    rng = random.Random(f"{seed}:{name}:vru-dataset")
    walk = walkable_edges(NET_FILE)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<routes>", VTYPES.rstrip("\n")]
    for route_id, edges in VEHICLE_ROUTES:
        lines.append(f'    <route id="{route_id}" edges="{" ".join(edges)}"/>')
    entities: list[tuple[int, str]] = []
    counts = {
        "vehicle": 0,
        "assertive_vehicle": 0,
        "cyclist": 0,
        "pedestrian": 0,
        "slow_pedestrian": 0,
    }
    for i in range(round(veh_h * SIM_END / 3600)):
        route_id, _ = rng.choice(VEHICLE_ROUTES)
        depart = rng.randint(0, SIM_END - 1)
        vtype = "passenger-assertive" if rng.random() < ASSERTIVE_SHARE else "passenger"
        counts["vehicle"] += 1
        counts["assertive_vehicle"] += vtype == "passenger-assertive"
        entities.append(
            (
                depart,
                f'    <vehicle id="veh-{i:05d}" type="{vtype}" route="{route_id}" depart="{depart}"/>',
            )
        )
    for i in range(round(cyc_h * SIM_END / 3600)):
        route_id, _ = rng.choice(VEHICLE_ROUTES[:6])
        depart = rng.randint(0, SIM_END - 1)
        counts["cyclist"] += 1
        entities.append(
            (
                depart,
                f'    <vehicle id="cyc-{i:05d}" type="cyclist" route="{route_id}" depart="{depart}"/>',
            )
        )
    for i in range(round(ped_h * SIM_END / 3600)):
        origin, destination = rng.sample(walk, 2)
        depart = rng.randint(0, SIM_END - 1)
        vtype = "ped-slow" if rng.random() < SLOW_PED_SHARE else "ped-normal"
        counts["pedestrian"] += 1
        counts["slow_pedestrian"] += vtype == "ped-slow"
        entities.append(
            (
                depart,
                f'    <person id="ped-{i:05d}" type="{vtype}" depart="{depart}"><walk from="{origin}" to="{destination}"/></person>',
            )
        )
    entities.sort(key=lambda e: (e[0], e[1]))
    lines += [e[1] for e in entities] + ["</routes>"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return counts


def build_run(
    split: str, seed: int, spec: tuple[str, int, int, int], sites: list[dict], out_root: Path
) -> dict:
    name = spec[0]
    run_id = f"p06vru-{split}-s{seed}-{name}"
    run_dir = out_root / split / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    counts = write_routes(run_dir / "demand.rou.xml", seed, spec)
    sumo_seed = seed % 100000 + (7 if name == "offpeak" else 0)
    (run_dir / "run.sumocfg").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <input><net-file value="{NET_FILE}"/><route-files value="{run_dir / "demand.rou.xml"}"/></input>
    <time><begin value="0"/><end value="{SIM_END}"/><step-length value="{STEP_S}"/></time>
    <processing><time-to-teleport value="-1"/></processing>
    <random_number><seed value="{sumo_seed}"/></random_number>
    <output><statistic-output value="{run_dir / "statistics.xml"}"/><collision-output value="{run_dir / "collisions.xml"}"/></output>
</configuration>
""",
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        [
            "sumo",
            "-c",
            str(run_dir / "run.sumocfg"),
            "--no-step-log",
            "--duration-log.disable",
            "--collision.action",
            "warn",
            "--fcd-output",
            "/dev/stdout",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    rows = 0
    t = 0.0
    r2 = SITE_RADIUS_M**2
    with gzip.GzipFile(run_dir / "tracks.csv.gz", "wb", mtime=0) as gz:
        gz.write(b"t,id,cls,sub,x,y,speed,site\n")
        for line in proc.stdout:
            step = STEP_TAG.search(line)
            if step:
                t = float(step.group(1))
                continue
            m = ROW.search(line)
            if not m:
                continue
            x, y = float(m.group(3)), float(m.group(4))
            site = next(
                (s["site_id"] for s in sites if (x - s["x"]) ** 2 + (y - s["y"]) ** 2 <= r2), None
            )
            if site is None:
                continue
            kind, ident, sub, speed = m.group(1), m.group(2), m.group(5), m.group(6)
            cls = "ped" if kind == "person" else ("cyc" if sub == "cyclist" else "veh")
            gz.write(f"{t:.2f},{ident},{cls},{sub},{x:.2f},{y:.2f},{speed},{site}\n".encode())
            rows += 1
    if proc.wait() != 0:
        raise RuntimeError(f"sumo failed for {run_id}")
    stats = ET.parse(run_dir / "statistics.xml").getroot()
    collisions = ET.parse(run_dir / "collisions.xml").getroot().findall("collision")
    meta = {
        "run_id": run_id,
        "split": split,
        "seed": seed,
        "spec": name,
        "sim_end_s": SIM_END,
        "step_s": STEP_S,
        "site_radius_m": SITE_RADIUS_M,
        "demand": counts,
        "rows": rows,
        "teleports": int(stats.find("teleports").get("total", "-1")),
        "sumo_collisions": len(collisions),
        "sumo_collision_types": sorted({c.get("type", "?") for c in collisions}),
        "sumo_collisions_involving_a_person": sum(
            1
            for c in collisions
            if (c.get("victim", "").startswith("ped-") or c.get("collider", "").startswith("ped-"))
        ),
    }
    (run_dir / "run.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    for name_ in ("collisions.xml", "statistics.xml", "demand.rou.xml", "run.sumocfg"):
        (run_dir / name_).unlink()
    return meta


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _job(args: tuple) -> dict:
    return build_run(*args)


def build_all(label: str, only_seed: int | None, workers: int) -> dict:
    out_root = OUTPUT_DIR / label
    out_root.mkdir(parents=True, exist_ok=True)
    sites = load_sites(NET_FILE)
    (out_root / "sites.json").write_text(
        json.dumps(
            {"geometry_version": GEOMETRY_VERSION, "radius_m": SITE_RADIUS_M, "sites": sites},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    split_seeds = p04._load_split_seeds()
    jobs = [
        (split, seed, spec, sites, out_root)
        for split, seeds in split_seeds.items()
        for seed in seeds
        if only_seed is None or seed == only_seed
        for spec in RUN_SPECS
    ]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        runs = list(pool.map(_job, jobs))
    files = {
        str(p.relative_to(out_root)): _sha256(p)
        for p in sorted(out_root.rglob("*"))
        if p.is_file() and p.name != "dataset_manifest.json"
    }
    manifest = {
        "schema": "vru-conflict-dataset-v1",
        "geometry_version": GEOMETRY_VERSION,
        "sumo_image_note": "see run_container.sh (digest-pinned)",
        "runs": sorted(runs, key=lambda r: r["run_id"]),
        "dataset_sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
        "files_sha256": files,
    }
    (out_root / "dataset_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("seed", nargs="?", type=int, default=None)
    parser.add_argument("--label", default="run-a")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    manifest = build_all(args.label, args.seed, args.workers)
    print(
        json.dumps(
            {
                "runs": len(manifest["runs"]),
                "dataset_sha256": manifest["dataset_sha256"],
                "rows": sum(r["rows"] for r in manifest["runs"]),
                "collisions": sum(r["sumo_collisions"] for r in manifest["runs"]),
                "teleports": sum(r["teleports"] for r in manifest["runs"]),
            }
        )
    )


if __name__ == "__main__":
    main()
