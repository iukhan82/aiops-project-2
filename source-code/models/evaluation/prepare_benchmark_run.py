"""P04.09 step 1: assemble one sustained-load event stream for the container
benchmark, from the held-out TEST split's real SUMO-derived loop telemetry.

Concatenates every test-split run's `events.jsonl` (24 runs, ~8600+ events
over their SUMO 900s windows) and sorts by `observation_time`. This measures
throughput/latency/resource use under sustained load, not detection
accuracy - accuracy is already measured once, honestly, in
`models/registry/traffic-safety-blockage/1.0.0/comparison_test.json`
(P04.03's single test-split opening) and is cited, not recomputed, by
`build_report.py`. Mixing several independent SUMO runs' traffic onto the
same 18 shared device ids is a deliberate, documented simplification for a
performance benchmark: it does not represent one coherent traffic
scenario, only a realistic sustained volume of well-formed events.

    python source-code/models/evaluation/prepare_benchmark_run.py
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2]
DATASET_ROOT = SOURCE / "models" / "dataset" / "output" / "run-a"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
REGISTRY = SOURCE / "models" / "registry"


def main() -> None:
    if not DATASET_ROOT.is_dir():
        raise SystemExit(f"missing {DATASET_ROOT}; run models/dataset/run_container.sh first")
    OUTPUT_DIR.mkdir(exist_ok=True)

    events: list[dict] = []
    run_count = 0
    for run_dir in sorted((DATASET_ROOT / "test").iterdir()):
        events_file = run_dir / "events.jsonl"
        if not events_file.is_file():
            continue
        run_count += 1
        events.extend(
            json.loads(line)
            for line in events_file.read_text(encoding="utf-8").splitlines()
            if line
        )
    if not events:
        raise SystemExit("no test-split events found")
    events.sort(key=lambda e: (e["observation_time"], e["device_id"], e["sequence_number"]))

    events_path = OUTPUT_DIR / "benchmark_events.jsonl"
    with events_path.open("w", encoding="utf-8", newline="\n") as stream:
        for event in events:
            stream.write(json.dumps(event, sort_keys=True) + "\n")

    shutil.copy(DATASET_ROOT / "devices.jsonl", OUTPUT_DIR / "devices.jsonl")

    config = {
        "site_id": "benchmark",
        "runtime_device_id": "edge-runtime-benchmark",
        "geometry_version": "2026-09-18.1",
        "registry_path": "/data/devices.jsonl",
        "baseline_path": "/data/registry/baseline/baseline_v1.json",
        "model_dir": "/data/registry/traffic-safety-blockage/1.0.0",
        "boot_id": "benchmark",
    }
    (OUTPUT_DIR / "benchmark_config.json").write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "runs_concatenated": run_count,
                "total_events": len(events),
                "span_start": events[0]["observation_time"],
                "span_end": events[-1]["observation_time"],
                "output": str(events_path.relative_to(SOURCE)),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
