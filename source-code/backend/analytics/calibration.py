"""P06.01: per-segment lane share, estimated on the TRAIN split only.

Each corridor edge has four lanes and one loop, on one of its two general
lanes. Scaling the loop's count by "1 / number of general lanes" assumes an
even split, which SUMO's lane discipline does not produce (measured share
ranges roughly 0.2-0.7 by segment). The share is a fitted parameter, so it is
fitted on train runs only and evaluated on validation/test - the same
discipline as every model in this project - and stored as a small committed
artifact with its dataset hash so a stale calibration is detectable.

In a real deployment this is the short-term reference-count calibration
operators do for any loop network; it is not a claim about real roads.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from backend.analytics.topology import edge_of_lane, load_segments_json

ARTIFACT_PATH = Path(__file__).resolve().parent / "artifacts" / "lane_share.json"
SHARE_MIN, SHARE_MAX = 0.05, 1.0


def estimate_lane_share(dataset_root: Path, split: str = "train") -> dict:
    manifest = json.loads((dataset_root / "dataset_manifest.json").read_text(encoding="utf-8"))
    corridor_edges = {
        s.edge_id for s in load_segments_json(dataset_root / "segments.json") if s.is_corridor
    }
    loop, truth = Counter(), Counter()
    runs = sorted(p for p in (dataset_root / split).iterdir() if p.is_dir())
    for run in runs:
        for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            count = next(m["value"] for m in event["measurements"] if m["name"] == "vehicle_count")
            loop[edge_of_lane(event["location"]["lane_id"])] += count
        for line in (run / "truth.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row["edge_id"] in corridor_edges:
                truth[row["edge_id"]] += row["entered"] + row["departed"]
    shares = {
        edge: round(min(SHARE_MAX, max(SHARE_MIN, loop[edge] / truth[edge])), 4)
        for edge in sorted(corridor_edges)
        if truth[edge] > 0
    }
    return {
        "method": "share = sum(loop vehicle_count) / sum(SUMO edgeData entered+departed), TRAIN runs only",
        "dataset_sha256": manifest["dataset_sha256"],
        "fitted_on_split": split,
        "fitted_on_seeds": manifest["split_seeds"][split],
        "runs": len(runs),
        "lane_share": shares,
    }


def write_artifact(artifact: dict, path: Path = ARTIFACT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_lane_shares(path: Path = ARTIFACT_PATH) -> dict[str, float]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))["lane_share"]


if __name__ == "__main__":
    root = (
        Path(__file__).resolve().parents[2] / "models" / "intelligence_dataset" / "output" / "run-a"
    )
    artifact = estimate_lane_share(root)
    write_artifact(artifact)
    print(json.dumps(artifact["lane_share"], indent=1))
