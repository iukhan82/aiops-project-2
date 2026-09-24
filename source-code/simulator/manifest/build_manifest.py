"""P03.07: immutable run manifest over the JSON/event output already
produced by P03.03-P03.06's `build_and_verify.py` runs.

Indexes each stage's `output/run-a/` (the git-ignored files those scripts
write - regenerate a stage first if you want the manifest to reflect a fresh
run). Pure Python, no SUMO/Docker dependency; reads what is already on disk.

Immutability contract: `manifest_sha256` is a hash over the sorted list of
per-file `(path, sha256)` pairs, so any single-byte change to any listed
file, or to the set of listed files, changes it. `build_manifest` and
`verify_manifest` must agree byte-for-byte on the same input files.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

SIMULATOR_DIR = Path(__file__).resolve().parents[1]

# (stage, relative path under <stage>/output/run-a/, is_event_stream)
SOURCE_FILES: list[tuple[str, str, bool]] = [
    ("sensors", "devices.jsonl", False),
    ("sensors", "observations.jsonl", True),
    ("emergency", "calls.jsonl", False),
    ("emergency", "assignments.jsonl", False),
    ("emergency", "devices.jsonl", False),
    ("emergency", "avl_events.jsonl", True),
    ("scenarios", "ground_truth.jsonl", False),
    ("scenarios", "overlay_events.jsonl", True),
    ("scenarios", "physical_summary.json", False),
    ("faults", "ground_truth.jsonl", False),
    ("faults", "fault_events.jsonl", True),
    ("faults", "aiops_agent_device.json", False),
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record_count(path: Path) -> int:
    if path.suffix == ".jsonl":
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line)
    return 1


def build_manifest(geometry_version: str, run_labels: dict[str, str]) -> dict[str, object]:
    """`run_labels` maps stage -> which output/<label>/ to index (default 'run-a')."""
    sources: list[dict[str, object]] = []
    missing: list[str] = []

    for stage, relative_path, is_event_stream in SOURCE_FILES:
        label = run_labels.get(stage, "run-a")
        path = SIMULATOR_DIR / stage / "output" / label / relative_path
        if not path.is_file():
            missing.append(str(path))
            continue
        sources.append(
            {
                "stage": stage,
                "path": f"{stage}/output/{label}/{relative_path}",
                "sha256": _sha256(path),
                "record_count": _record_count(path),
                "is_event_stream": is_event_stream,
            }
        )

    if missing:
        raise SystemExit(
            "missing source files for manifest (run each stage's build first): "
            + ", ".join(missing)
        )

    sources.sort(key=lambda s: s["path"])
    digest_input = "\n".join(f"{s['path']}:{s['sha256']}" for s in sources)
    manifest_sha256 = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()

    return {
        "schema": "traffic-sim-run-manifest-v1",
        "geometry_version": geometry_version,
        "run_labels": {stage: run_labels.get(stage, "run-a") for stage, _, _ in SOURCE_FILES},
        "sources": sources,
        "manifest_sha256": manifest_sha256,
    }


def verify_manifest(manifest: dict[str, object]) -> list[str]:
    """Returns a list of problems; empty means the manifest still matches disk."""
    problems: list[str] = []
    for source in manifest["sources"]:  # type: ignore[index]
        path = SIMULATOR_DIR / source["path"]
        if not path.is_file():
            problems.append(f"missing: {source['path']}")
            continue
        actual_sha = _sha256(path)
        if actual_sha != source["sha256"]:
            problems.append(
                f"hash mismatch: {source['path']} (manifest {source['sha256']}, disk {actual_sha})"
            )

    digest_input = "\n".join(f"{s['path']}:{s['sha256']}" for s in manifest["sources"])  # type: ignore[index]
    recomputed = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    if recomputed != manifest["manifest_sha256"]:
        problems.append(
            f"manifest_sha256 mismatch: recorded {manifest['manifest_sha256']}, recomputed {recomputed}"
        )
    return problems


if __name__ == "__main__":
    manifest = build_manifest("2026-09-18.1", {})
    print(json.dumps(manifest, indent=2, sort_keys=True))
