"""P03.07: build the immutable run manifest and prove replay equivalence.

Pure Python, no SUMO/Docker needed; reads the already-generated output of
P03.03-P03.06's build_and_verify.py runs. Run directly:

    python source-code/simulator/manifest/build_and_verify.py

Proves:
- `verify_manifest` finds zero problems against the manifest `build_manifest`
  just wrote (immutability round-trip: recorded hashes match disk, and the
  top-level `manifest_sha256` matches a fresh recomputation).
- Replaying the manifest's merged event streams twice, independently,
  produces the exact same accepted-event sequence (same `accepted_sha256`) -
  deterministic replay.
- Replaying the event set with every event duplicated produces the *same*
  `accepted_sha256` and the *same* `accepted_count` as the single-copy
  replay, with every extra copy rejected as `"duplicate"` - idempotent,
  duplicate-safe replay, not just "runs twice without crashing".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"

sys.path.insert(0, str(HERE))
from build_manifest import build_manifest, verify_manifest  # noqa: E402
from replay import load_event_stream_files, replay  # noqa: E402

GEOMETRY_VERSION = "2026-09-18.1"


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    manifest = build_manifest(GEOMETRY_VERSION, {})
    manifest_path = OUTPUT_DIR / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    problems = verify_manifest(manifest)
    if problems:
        raise SystemExit(f"manifest failed self-verification: {problems}")

    reloaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems_after_reload = verify_manifest(reloaded)
    if problems_after_reload:
        raise SystemExit(
            f"manifest failed verification after reload from disk: {problems_after_reload}"
        )

    events = load_event_stream_files(manifest)
    if not events:
        raise SystemExit("no event-stream records found across manifest sources")

    replay_1 = replay(events)
    replay_2 = replay(events)
    if replay_1["accepted_sha256"] != replay_2["accepted_sha256"]:
        raise SystemExit(
            "replay is not deterministic across two independent runs of the same events"
        )
    if replay_1["accepted_ids"] != replay_2["accepted_ids"]:
        raise SystemExit("replay produced the same hash but a different accepted-id sequence")

    duplicated_events = events + events
    replay_duplicated = replay(duplicated_events)
    if replay_duplicated["accepted_sha256"] != replay_1["accepted_sha256"]:
        raise SystemExit(
            "replaying duplicated input changed the accepted sequence (not duplicate-safe)"
        )
    if replay_duplicated["accepted_count"] != replay_1["accepted_count"]:
        raise SystemExit("replaying duplicated input changed the accepted count (not idempotent)")
    if replay_duplicated["rejected_count"] != len(events):
        raise SystemExit(
            f"expected exactly {len(events)} duplicate rejections, got {replay_duplicated['rejected_count']}"
        )
    if replay_duplicated["rejected_reasons"] != ["duplicate"]:
        raise SystemExit(f"unexpected rejection reasons: {replay_duplicated['rejected_reasons']}")

    report = {
        "manifest_sha256": manifest["manifest_sha256"],
        "source_file_count": len(manifest["sources"]),
        "event_stream_count": sum(1 for s in manifest["sources"] if s["is_event_stream"]),
        "total_events": len(events),
        "replay_accepted_count": replay_1["accepted_count"],
        "replay_rejected_count": replay_1["rejected_count"],
        "replay_accepted_sha256": replay_1["accepted_sha256"],
        "replay_deterministic": True,
        "replay_idempotent_under_duplication": True,
        "manifest_verified": True,
    }
    (OUTPUT_DIR / "replay_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
