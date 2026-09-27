"""P10.05: run ONE operational run in its own process (its own OTel service name = Prometheus `job`).

    python capture_run.py --run-id r-20260918-normal --seed 20260918 --scenario normal \\
        --otlp http://127.0.0.1:9091/api/v1/otlp/v1/metrics --out run.json

The process pushes the real instruments' metrics to the given OTLP endpoint for `RUN_SECONDS` and writes a small JSON
file with the plan and the wall-clock start/end, which is how the builder later knows where to read the series back.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--replicate", type=int, default=0)
    parser.add_argument("--otlp", required=True, help="Prometheus OTLP metrics endpoint")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    os.environ["OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"] = args.otlp
    os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
    os.environ.pop("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", None)

    from backend import observability
    from models.operations_dataset.load import RUN_SECONDS, WARMUP_SECONDS, Load, make_plan

    observability.configure(args.run_id)
    plan = make_plan(args.seed, args.scenario, args.replicate)
    start, end = Load(plan).run()
    Path(args.out).write_text(
        json.dumps(
            {
                "run_id": args.run_id,
                "seed": args.seed,
                "scenario": args.scenario,
                "replicate": args.replicate,
                "plan": plan.as_dict(),
                "run_seconds": RUN_SECONDS,
                "warmup_seconds": WARMUP_SECONDS,
                "start_epoch": start,
                "end_epoch": end,
                "python": platform.python_version(),
                "pid": os.getpid(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
