"""P03.06: build and verify device/platform fault scenarios with separate
ground truth - silence, stuck, clock, network, model, service, storage.

Pure Python: no SUMO invocation (these are platform/AIOps-layer faults, not
road physics - see README.md and generate_fault_events.py). Only reads the
already-built source-code/simulator/network/output/district.net.xml (P03.01)
to place the four sensor-catalog-based faults on real device locations.
Runs directly, no container needed:

    python source-code/simulator/faults/build_and_verify.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIMULATOR_DIR = HERE.parent
NETWORK_DIR = SIMULATOR_DIR / "network"
NET_FILE = NETWORK_DIR / "output" / "district.net.xml"
NOD_FILE = NETWORK_DIR / "plain" / "district.nod.xml"
OUTPUT_DIR = HERE / "output"

sys.path.insert(0, str(HERE))
from generate_fault_events import build_faults  # noqa: E402

SEED = 20260918
RUN_ID = "p03-06-faults"
ANCHOR_UTC = "2026-09-18T09:00:00Z"

REQUIRED_FAULT_TYPES = {"silence", "stuck", "clock", "network", "model", "service", "storage"}


def build_one_run(run_label: str) -> dict[str, object]:
    run_dir = OUTPUT_DIR / run_label
    run_dir.mkdir(parents=True, exist_ok=True)
    anchor = datetime.fromisoformat(ANCHOR_UTC.replace("Z", "+00:00")).astimezone(timezone.utc)

    result = build_faults(NET_FILE, NOD_FILE, RUN_ID, anchor, SEED)

    ground_truth_path = run_dir / "ground_truth.jsonl"
    with ground_truth_path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in result["ground_truth"]:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    events_path = run_dir / "fault_events.jsonl"
    with events_path.open("w", encoding="utf-8", newline="\n") as stream:
        for event in result["events"]:
            stream.write(json.dumps(event, sort_keys=True) + "\n")

    device_path = run_dir / "aiops_agent_device.json"
    device_path.write_text(
        json.dumps(result["aiops_agent_device"], indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    return {
        "ground_truth_path": ground_truth_path,
        "events_path": events_path,
        "device_path": device_path,
        "fault_count": len(result["ground_truth"]),
        "event_count": len(result["events"]),
    }


def main() -> None:
    if not NET_FILE.is_file():
        raise SystemExit(f"missing {NET_FILE}; run P03.01's network build_and_verify.py first")
    OUTPUT_DIR.mkdir(exist_ok=True)

    run_a = build_one_run("run-a")
    run_b = build_one_run("run-b")

    for key in ("ground_truth_path", "events_path", "device_path"):
        text_a = run_a[key].read_text(encoding="utf-8")
        text_b = run_b[key].read_text(encoding="utf-8")
        if text_a != text_b:
            raise SystemExit(f"{key} is not byte-identical across two runs of the same run_id")

    fault_types = {
        json.loads(line)["scenario_type"]
        for line in run_a["ground_truth_path"].read_text(encoding="utf-8").splitlines()
    }
    missing = REQUIRED_FAULT_TYPES - fault_types
    if missing:
        raise SystemExit(f"missing required fault types: {sorted(missing)}")

    summary = {
        "run_id": RUN_ID,
        "anchor_utc": ANCHOR_UTC,
        "fault_count": run_a["fault_count"],
        "event_count": run_a["event_count"],
        "fault_types": sorted(fault_types),
        "deterministic": True,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
