"""P03.08: one dataset "unit" per seed - a demand-entity bundle plus a
device/platform-fault bundle - reusing P03.05's seed-parametrized demand
generator and P03.06's now-seed-parametrized fault generator (extended for
this task so faults genuinely vary by seed, not just by run_id string; see
source-code/simulator/faults/generate_fault_events.py).

Deliberately does not include the SUMO-simulated content (P03.03 sensor
telemetry, P03.04 AVL, P03.05's physical scenarios) - multiplying real SUMO
runs across many seeds is out of scope for this pass (see README.md); those
stages' existing single-seed output remains available as reference material,
just not part of this split.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

SIMULATOR_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIMULATOR_DIR / "scenarios"))
from generate_physical_demand import generate_scaled_entities  # noqa: E402

sys.path.insert(0, str(SIMULATOR_DIR / "faults"))
from generate_fault_events import build_faults  # noqa: E402

SIM_END = 600
ANCHOR_UTC = "2026-09-18T09:00:00Z"


def run_id_for(split: str, seed: int) -> str:
    return f"p03-08-{split}-seed-{seed}"


def _entity_to_dict(entity, seed: int, split: str, run_id: str) -> dict:
    return {
        "seed": seed,
        "split": split,
        "run_id": run_id,
        "kind": entity.kind,
        "entity_id": entity.entity_id,
        "depart_s": entity.depart,
        "route_id": entity.route_id,
        "walk_edges": list(entity.walk_edges) if entity.walk_edges else None,
        "stops": entity.stops,
    }


def build_unit(net_file: Path, nod_file: Path, split: str, seed: int) -> dict[str, list[dict]]:
    run_id = run_id_for(split, seed)
    anchor = datetime.fromisoformat(ANCHOR_UTC.replace("Z", "+00:00")).astimezone(timezone.utc)

    entities = generate_scaled_entities(seed, run_id, SIM_END, scale=1.0)
    demand_records = [_entity_to_dict(e, seed, split, run_id) for e in entities]

    fault_result = build_faults(net_file, nod_file, run_id, anchor, seed)
    fault_ground_truth = [
        {**gt, "seed": seed, "split": split} for gt in fault_result["ground_truth"]
    ]
    # fault_events keep their exact observation-envelope/v1 shape (no extra
    # fields - additionalProperties is false); seed/split membership comes
    # from which split's output file a record lives in, not an in-record tag.
    fault_events = fault_result["events"]

    return {
        "demand": demand_records,
        "fault_ground_truth": fault_ground_truth,
        "fault_events": fault_events,
    }
