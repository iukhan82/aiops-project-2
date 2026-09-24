"""P03.08: build disjoint train/validation/test datasets and prove leakage
safety. Pure Python, no SUMO/Docker needed (reuses P03.05's seed-
parametrized demand generator and P03.06's seed-parametrized fault
generator - both need only the already-built
source-code/simulator/network/output/district.net.xml, P03.01). Run
directly:

    python source-code/simulator/datasets/build_and_verify.py

Nine seeds, assigned to disjoint splits by construction (each seed appears
in exactly one split's SEEDS list below); every unit's run_id embeds its
split and seed (`p03-08-<split>-seed-<seed>`), which is what actually makes
every entity_id/event_id split-unique - the disjoint SEEDS lists alone don't
guarantee that without it.

"Splits differ across seeds/routes/demand/faults" (P03.08's acceptance
text) means: the specific demand records (which entity got which route at
which depart time) and fault records (which device, what onset/values)
differ across splits, not that the finite vocabulary of route IDs or fault
types is disjoint - with 9 shared route templates and 7 required fault
types, disjoint *vocabulary* across splits would be impossible and is not
what a leakage check should mean here. See README.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIMULATOR_DIR = HERE.parent
NETWORK_DIR = SIMULATOR_DIR / "network"
NET_FILE = NETWORK_DIR / "output" / "district.net.xml"
NOD_FILE = NETWORK_DIR / "plain" / "district.nod.xml"
OUTPUT_DIR = HERE / "output"

sys.path.insert(0, str(HERE))
from generate_dataset_units import build_unit  # noqa: E402

SPLIT_SEEDS: dict[str, list[int]] = {
    "train": [20260918, 20260919, 20260920, 20260921, 20260922],
    "validation": [20260923, 20260924],
    "test": [20260925, 20260926],
}


def build_one_run(run_label: str) -> dict[str, object]:
    run_dir = OUTPUT_DIR / run_label
    run_dir.mkdir(parents=True, exist_ok=True)

    split_files: dict[str, dict[str, Path]] = {}
    split_stats: dict[str, dict[str, int]] = {}

    for split, seeds in SPLIT_SEEDS.items():
        split_dir = run_dir / split
        split_dir.mkdir(exist_ok=True)
        demand_records: list[dict] = []
        fault_ground_truth: list[dict] = []
        fault_events: list[dict] = []

        for seed in seeds:
            unit = build_unit(NET_FILE, NOD_FILE, split, seed)
            demand_records.extend(unit["demand"])
            fault_ground_truth.extend(unit["fault_ground_truth"])
            fault_events.extend(unit["fault_events"])

        demand_records.sort(key=lambda r: (r["seed"], r["entity_id"]))
        fault_ground_truth.sort(key=lambda r: (r["seed"], r["scenario_id"]))
        fault_events.sort(
            key=lambda e: (e["observation_time"], e["device_id"], e["sequence_number"])
        )

        paths = {
            "demand": split_dir / "demand_entities.jsonl",
            "fault_ground_truth": split_dir / "fault_ground_truth.jsonl",
            "fault_events": split_dir / "fault_events.jsonl",
        }
        for key, records in (
            ("demand", demand_records),
            ("fault_ground_truth", fault_ground_truth),
            ("fault_events", fault_events),
        ):
            with paths[key].open("w", encoding="utf-8", newline="\n") as stream:
                for record in records:
                    stream.write(json.dumps(record, sort_keys=True) + "\n")

        split_files[split] = paths
        split_stats[split] = {
            "seeds": seeds,
            "demand_count": len(demand_records),
            "fault_ground_truth_count": len(fault_ground_truth),
            "fault_event_count": len(fault_events),
        }

    return {"split_files": split_files, "split_stats": split_stats}


def _ids(path: Path, id_field: str) -> set[str]:
    return {
        json.loads(line)[id_field] for line in path.read_text(encoding="utf-8").splitlines() if line
    }


def check_leakage(split_files: dict[str, dict[str, Path]]) -> dict[str, object]:
    problems: list[str] = []

    seed_sets = {split: set(SPLIT_SEEDS[split]) for split in SPLIT_SEEDS}
    splits = list(seed_sets)
    for i, a in enumerate(splits):
        for b in splits[i + 1 :]:
            overlap = seed_sets[a] & seed_sets[b]
            if overlap:
                problems.append(f"seed overlap between {a} and {b}: {sorted(overlap)}")

    for id_field, key in (("entity_id", "demand"), ("event_id", "fault_events")):
        per_split_ids = {split: _ids(files[key], id_field) for split, files in split_files.items()}
        for i, a in enumerate(splits):
            for b in splits[i + 1 :]:
                overlap = per_split_ids[a] & per_split_ids[b]
                if overlap:
                    problems.append(
                        f"{key} {id_field} overlap between {a} and {b}: {sorted(overlap)[:5]}"
                    )

    content_hashes = {
        split: hash(files["fault_events"].read_text(encoding="utf-8"))
        for split, files in split_files.items()
    }
    if len(set(content_hashes.values())) != len(content_hashes):
        problems.append(
            "two splits produced byte-identical fault_events content (seeds did not vary output)"
        )

    return {"problems": problems, "passed": not problems}


def main() -> None:
    if not NET_FILE.is_file():
        raise SystemExit(f"missing {NET_FILE}; run P03.01's network build_and_verify.py first")
    OUTPUT_DIR.mkdir(exist_ok=True)

    run_a = build_one_run("run-a")
    run_b = build_one_run("run-b")

    for split in SPLIT_SEEDS:
        for key in ("demand", "fault_ground_truth", "fault_events"):
            path_a = run_a["split_files"][split][key]
            path_b = run_b["split_files"][split][key]
            if path_a.read_text(encoding="utf-8") != path_b.read_text(encoding="utf-8"):
                raise SystemExit(
                    f"{split}/{key} is not byte-identical across two runs of the same seeds"
                )

    leakage = check_leakage(run_a["split_files"])
    if not leakage["passed"]:
        raise SystemExit(f"leakage check failed: {leakage['problems']}")

    summary = {
        "splits": SPLIT_SEEDS,
        "split_stats": run_a["split_stats"],
        "leakage_check": leakage,
        "deterministic": True,
    }
    (OUTPUT_DIR / "split_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
