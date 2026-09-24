"""P03.09: validate the cross-stage automated invariant checks.

source-code/simulator/verification/build_and_verify.py needs no SUMO/Docker
itself, but checks P03.03/P03.04/P03.05/P03.06's (partly Docker-generated)
output, so this test skips if any of that upstream output is missing.
"""

import json
from pathlib import Path

import pytest

SIMULATOR_DIR = Path(__file__).resolve().parents[1] / "simulator"
VERIFICATION_DIR = SIMULATOR_DIR / "verification"
REPORT_PATH = VERIFICATION_DIR / "output" / "bounds_report.json"

_REQUIRED_STAGES = ("sensors", "emergency", "scenarios", "faults")


def _upstream_ready() -> bool:
    checks = {
        "sensors": SIMULATOR_DIR / "sensors" / "output" / "run-a" / "observations.jsonl",
        "emergency": SIMULATOR_DIR / "emergency" / "output" / "run-a" / "avl_events.jsonl",
        "scenarios": SIMULATOR_DIR / "scenarios" / "output" / "run-a" / "overlay_events.jsonl",
        "faults": SIMULATOR_DIR / "faults" / "output" / "run-a" / "fault_events.jsonl",
    }
    return all(path.is_file() for path in checks.values())


def _ensure_generated() -> None:
    if not _upstream_ready():
        pytest.skip(
            "upstream stage output missing; run sensors/emergency/scenarios "
            "run_container.sh and faults/build_and_verify.py first"
        )
    if not REPORT_PATH.is_file():
        import subprocess
        import sys

        subprocess.run(
            [sys.executable, str(VERIFICATION_DIR / "build_and_verify.py")],
            check=True,
            cwd=VERIFICATION_DIR,
        )


def test_bounds_report_shows_zero_problems() -> None:
    _ensure_generated()
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["total_problems"] == 0


def test_all_required_stages_were_checked_not_skipped() -> None:
    _ensure_generated()
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    for stage in _REQUIRED_STAGES:
        assert "skipped" not in report["stages"][stage], f"{stage} was skipped, not checked"
        assert report["stages"][stage]["checked_events"] > 0


def test_network_bbox_matches_known_district_extent() -> None:
    _ensure_generated()
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert tuple(report["bbox"]) == (0.0, 0.0, 900.0, 800.0)
