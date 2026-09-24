"""P03.07: minimal ordered/idempotent/duplicate-safe replay over the
manifest's event streams, per docs/PROJECT_CONTEXT.md's engineering
invariant ("Replay is ordered, acknowledged, bounded, idempotent, and
duplicate-safe"). This is a simulator-layer proof of the mechanism, not the
platform's real ingestion path (that is Phase 05/07 backend work) - it
operates on the already-recorded event files, not a live stream.

Acceptance rule per event: reject if `event_id` was already accepted
(duplicate-safe); reject if its `observation_time` is earlier than the last
accepted event for the same `device_id` (ordered per device). Otherwise
accept.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

SIMULATOR_DIR = Path(__file__).resolve().parents[1]


def load_event_stream_files(manifest: dict[str, object]) -> list[dict]:
    events: list[dict] = []
    for source in manifest["sources"]:  # type: ignore[index]
        if not source["is_event_stream"]:
            continue
        path = SIMULATOR_DIR / source["path"]
        for line in path.read_text(encoding="utf-8").splitlines():
            if line:
                events.append(json.loads(line))
    return events


def replay(events: list[dict]) -> dict[str, object]:
    ordered = sorted(
        events, key=lambda e: (e["observation_time"], e["device_id"], e["sequence_number"])
    )

    seen_ids: set[str] = set()
    last_time_by_device: dict[str, str] = {}
    accepted_ids: list[str] = []
    rejected: list[dict[str, str]] = []

    for event in ordered:
        event_id = event["event_id"]
        device_id = event["device_id"]
        observation_time = event["observation_time"]

        if event_id in seen_ids:
            rejected.append({"event_id": event_id, "reason": "duplicate"})
            continue
        if device_id in last_time_by_device and observation_time < last_time_by_device[device_id]:
            rejected.append({"event_id": event_id, "reason": "out_of_order"})
            continue

        seen_ids.add(event_id)
        last_time_by_device[device_id] = observation_time
        accepted_ids.append(event_id)

    accepted_sha256 = hashlib.sha256("\n".join(accepted_ids).encode("utf-8")).hexdigest()

    return {
        "input_count": len(events),
        "accepted_count": len(accepted_ids),
        "rejected_count": len(rejected),
        "rejected_reasons": sorted({r["reason"] for r in rejected}),
        "accepted_sha256": accepted_sha256,
        "accepted_ids": accepted_ids,
    }
