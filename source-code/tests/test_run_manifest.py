"""P03.07: validate the immutable run manifest and replay equivalence.

source-code/simulator/manifest/build_and_verify.py needs no SUMO/Docker
itself, but it indexes P03.03/P03.04/P03.05's (Docker-generated) and
P03.06's (host-generated) output/run-a/ directories, so this test skips if
any of those upstream outputs are missing rather than trying to regenerate
a full SUMO pipeline.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SIMULATOR_DIR = Path(__file__).resolve().parents[1] / "simulator"
MANIFEST_DIR = SIMULATOR_DIR / "manifest"
sys.path.insert(0, str(MANIFEST_DIR))

from build_manifest import SOURCE_FILES, verify_manifest  # noqa: E402
from replay import load_event_stream_files, replay  # noqa: E402

RUN_DIR = MANIFEST_DIR / "output"
MANIFEST_PATH = RUN_DIR / "manifest.json"
REPORT_PATH = RUN_DIR / "replay_report.json"


def _upstream_ready() -> bool:
    return all(
        (SIMULATOR_DIR / stage / "output" / "run-a" / relative_path).is_file()
        for stage, relative_path, _ in SOURCE_FILES
    )


def _ensure_generated() -> None:
    if not _upstream_ready():
        pytest.skip(
            "upstream stage output missing; run sensors/emergency/scenarios "
            "run_container.sh and faults/build_and_verify.py first"
        )
    if not (MANIFEST_PATH.is_file() and REPORT_PATH.is_file()):
        subprocess.run(
            [sys.executable, str(MANIFEST_DIR / "build_and_verify.py")],
            check=True,
            cwd=MANIFEST_DIR,
        )


def test_manifest_verifies_clean_against_disk() -> None:
    _ensure_generated()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert verify_manifest(manifest) == []


def test_manifest_sha256_changes_if_a_source_hash_is_tampered() -> None:
    _ensure_generated()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    tampered = json.loads(json.dumps(manifest))
    tampered["sources"][0]["sha256"] = "0" * 64
    problems = verify_manifest(tampered)
    assert any("manifest_sha256 mismatch" in p or "hash mismatch" in p for p in problems)


def test_manifest_lists_all_twelve_source_files() -> None:
    _ensure_generated()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert len(manifest["sources"]) == len(SOURCE_FILES)
    assert sum(1 for s in manifest["sources"] if s["is_event_stream"]) == 4


def test_replay_report_shows_zero_rejections_on_clean_data() -> None:
    _ensure_generated()
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["replay_rejected_count"] == 0
    assert report["replay_accepted_count"] == report["total_events"]


def test_replay_is_deterministic_and_duplicate_safe() -> None:
    _ensure_generated()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    events = load_event_stream_files(manifest)

    first = replay(events)
    second = replay(events)
    assert first["accepted_sha256"] == second["accepted_sha256"]
    assert first["accepted_ids"] == second["accepted_ids"]

    duplicated = replay(events + events)
    assert duplicated["accepted_sha256"] == first["accepted_sha256"]
    assert duplicated["accepted_count"] == first["accepted_count"]
    assert duplicated["rejected_count"] == len(events) + first["rejected_count"]


def test_no_duplicate_event_ids_across_all_stages() -> None:
    """Regression guard: P03.07 caught a real cross-fault event_id collision
    in P03.06 (three faults sharing one device all reset their sequence
    number to 0/1, so uuid5(namespace, f"{run_id}:{device_id}:{seq}")
    collided); this pins that it stays fixed."""
    _ensure_generated()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    events = load_event_stream_files(manifest)
    event_ids = [e["event_id"] for e in events]
    assert len(event_ids) == len(set(event_ids))
