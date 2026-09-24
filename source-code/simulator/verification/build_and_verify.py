"""P03.09: run the cross-stage automated invariant checks and require all
four SUMO/labeled-overlay stages (P03.03/P03.04/P03.05/P03.06) to actually
be present - a partial "skipped" result does not count as a pass here, only
verify_bounds.py's per-check-group tolerance for a partial re-run does. Run
directly, no container needed:

    python source-code/simulator/verification/build_and_verify.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"

sys.path.insert(0, str(HERE))
from verify_bounds import run_all_checks  # noqa: E402


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    report = run_all_checks()

    skipped = [stage for stage, result in report["stages"].items() if "skipped" in result]
    if skipped:
        raise SystemExit(
            "cannot certify a full pass: these stages have no output to check "
            f"(regenerate them first): {skipped}"
        )
    if not report["passed"]:
        raise SystemExit(f"bounds check failed: {report['problems']}")

    (OUTPUT_DIR / "bounds_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    summary = {
        "stages_checked": list(report["stages"]),
        "events_checked": sum(
            result.get("checked_events", 0) for result in report["stages"].values()
        ),
        "total_problems": report["total_problems"],
        "passed": report["passed"],
        "network_bbox": report["bbox"],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
