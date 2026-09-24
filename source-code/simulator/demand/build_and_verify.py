"""P03.02: prove the demand/clock generator and its simulation are both
deterministic, and that vehicles, cyclists, pedestrians and transit are all
present with a correct, reproducible sim-time to UTC clock mapping.

Runs inside the pinned ghcr.io/eclipse-sumo/sumo image (see
source-code/simulator/network/run_container.sh for the exact digest);
requires source-code/simulator/network/output/district.net.xml to already
exist (P03.01's build_and_verify.py).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_demand import generate  # noqa: E402

SEED = 20260918
RUN_ID = "p03-02-verify"
ANCHOR_UTC = "2026-09-18T09:00:00Z"
SIM_END = 600

HERE = Path(__file__).resolve().parent
NETWORK_DIR = HERE.parent / "network"
NET_FILE = NETWORK_DIR / "output" / "district.net.xml"
VTYPES_FILE = NETWORK_DIR / "demand" / "vtypes.add.xml"
STOPS_FILE = HERE / "stops.add.xml"
OUTPUT_DIR = HERE / "output"

_HEADER_COMMENT = re.compile(r"<!--.*?-->\s*", re.DOTALL)


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, capture_output=True, text=True, **kwargs)


def verify_generator_determinism() -> dict[str, object]:
    dir_a = OUTPUT_DIR / "gen-a"
    dir_b = OUTPUT_DIR / "gen-b"
    manifest_a = generate(SEED, RUN_ID, ANCHOR_UTC, SIM_END, dir_a)
    manifest_b = generate(SEED, RUN_ID, ANCHOR_UTC, SIM_END, dir_b)

    route_a = (dir_a / f"{RUN_ID}.rou.xml").read_text(encoding="utf-8")
    route_b = (dir_b / f"{RUN_ID}.rou.xml").read_text(encoding="utf-8")
    if route_a != route_b:
        raise SystemExit("generator produced non-identical route files for the same seed/run_id")
    if manifest_a != manifest_b:
        raise SystemExit("generator produced non-identical manifests for the same seed/run_id")

    counts = manifest_a["entity_counts"]
    for kind in ("vehicle", "cyclist", "pedestrian", "transit"):
        if counts.get(kind, 0) <= 0:
            raise SystemExit(f"expected at least one {kind}, got {counts.get(kind, 0)}")

    return manifest_a


def verify_clock_mapping(manifest: dict[str, object]) -> None:
    anchor = datetime.fromisoformat(str(manifest["anchor_utc"]).replace("Z", "+00:00"))
    for sample in manifest["clock_samples"]:  # type: ignore[index]
        expected = (anchor + timedelta(seconds=sample["depart_s"])).astimezone(timezone.utc)
        expected_str = expected.strftime("%Y-%m-%dT%H:%M:%SZ")
        if expected_str != sample["observation_time"]:
            raise SystemExit(
                f"clock mapping mismatch for {sample['entity_id']}: "
                f"expected {expected_str}, manifest has {sample['observation_time']}"
            )


def write_sumocfg(
    route_file: Path, sumocfg_path: Path, fcd_out: Path, stats_out: Path, stop_out: Path
) -> None:
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
        <fcd-output value="{fcd_out}"/>
        <stop-output value="{stop_out}"/>
        <statistic-output value="{stats_out}"/>
    </output>
</configuration>
""",
        encoding="utf-8",
    )


def semantic_hash(path: Path) -> str:
    import hashlib

    text = path.read_text(encoding="utf-8")
    text = _HEADER_COMMENT.sub("", text, count=1)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_sumo_twice(route_file: Path) -> dict[str, object]:
    results = []
    for label in ("sim-run-1", "sim-run-2"):
        fcd_out = OUTPUT_DIR / f"{label}-fcd.xml"
        stats_out = OUTPUT_DIR / f"{label}-statistics.xml"
        stop_out = OUTPUT_DIR / f"{label}-stopinfo.xml"
        sumocfg = OUTPUT_DIR / f"{label}.sumocfg"
        write_sumocfg(route_file, sumocfg, fcd_out, stats_out, stop_out)
        run(["sumo", "-c", str(sumocfg), "--no-step-log", "--duration-log.disable"])
        results.append(
            {
                "label": label,
                "fcd_semantic_sha256": semantic_hash(fcd_out),
                "stop_semantic_sha256": semantic_hash(stop_out),
            }
        )
        # demo-statistics.xml is deliberately excluded from the determinism
        # comparison: its <performance> element carries real wall-clock
        # timing (clockBegin/clockEnd/realTimeFactor) that legitimately
        # differs between runs and is not part of the simulation result.

    deterministic = (
        results[0]["fcd_semantic_sha256"] == results[1]["fcd_semantic_sha256"]
        and results[0]["stop_semantic_sha256"] == results[1]["stop_semantic_sha256"]
    )
    if not deterministic:
        raise SystemExit(
            "simulation output was not identical across two runs of the same demand/seed"
        )

    stats = ET.parse(OUTPUT_DIR / "sim-run-1-statistics.xml").getroot()
    teleports = int(stats.find("teleports").get("total", "-1"))  # type: ignore[union-attr]
    collisions = int(stats.find("safety").get("collisions", "-1"))  # type: ignore[union-attr]
    if teleports != 0:
        raise SystemExit(f"expected zero teleports, got {teleports}")
    if collisions != 0:
        raise SystemExit(f"expected zero collisions, got {collisions}")

    stops = ET.parse(OUTPUT_DIR / "sim-run-1-stopinfo.xml").getroot()
    completed_stop_ids = {s.get("busStop") for s in stops.findall("stopinfo")}
    expected_stop_ids = {"stop-line1-a", "stop-line1-b", "stop-line2-b", "stop-line2-c"}
    if not expected_stop_ids.issubset(completed_stop_ids):
        raise SystemExit(
            f"missing completed transit stops: {expected_stop_ids - completed_stop_ids}"
        )

    return {
        "deterministic_replay": deterministic,
        "teleports_total": teleports,
        "collisions": collisions,
        "transit_stops_completed": sorted(completed_stop_ids),
        "runs": results,
    }


def main() -> None:
    if not NET_FILE.is_file():
        raise SystemExit(f"missing {NET_FILE}; run P03.01's network build_and_verify.py first")
    OUTPUT_DIR.mkdir(exist_ok=True)

    manifest = verify_generator_determinism()
    verify_clock_mapping(manifest)

    route_file = OUTPUT_DIR / "gen-a" / f"{RUN_ID}.rou.xml"
    simulation = run_sumo_twice(route_file)

    summary = {
        "seed": SEED,
        "run_id": RUN_ID,
        "anchor_utc": ANCHOR_UTC,
        "entity_counts": manifest["entity_counts"],
        "clock_samples": manifest["clock_samples"],
        "generator_deterministic": True,
        "simulation": simulation,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
