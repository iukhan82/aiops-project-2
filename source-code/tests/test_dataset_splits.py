"""P03.08: validate disjoint train/validation/test dataset splits and their
leakage checks. Pure Python, no SUMO/Docker needed - regenerates the output
itself if missing, same as P03.06/P03.07's tests.
"""

import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

CONTRACTS_ROOT = Path(__file__).resolve().parents[1] / "contracts"
DATASETS_DIR = Path(__file__).resolve().parents[1] / "simulator" / "datasets"
NET_FILE = (
    Path(__file__).resolve().parents[1] / "simulator" / "network" / "output" / "district.net.xml"
)
RUN_DIR = DATASETS_DIR / "output" / "run-a"
SPLIT_SUMMARY_PATH = DATASETS_DIR / "output" / "split_summary.json"
SPLITS = ("train", "validation", "test")


def _ensure_generated() -> None:
    if SPLIT_SUMMARY_PATH.is_file() and all((RUN_DIR / s).is_dir() for s in SPLITS):
        return
    if not NET_FILE.is_file():
        pytest.skip("network not built (gitignored); run simulator/network/run_container.sh first")
    subprocess.run(
        [sys.executable, str(DATASETS_DIR / "build_and_verify.py")],
        check=True,
        cwd=DATASETS_DIR,
    )
    if not SPLIT_SUMMARY_PATH.is_file():
        pytest.skip("dataset split output still missing after attempting to generate it")


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_leakage_check_passed() -> None:
    _ensure_generated()
    summary = json.loads(SPLIT_SUMMARY_PATH.read_text(encoding="utf-8"))
    assert summary["leakage_check"]["passed"] is True
    assert summary["leakage_check"]["problems"] == []


def test_seed_sets_are_pairwise_disjoint() -> None:
    _ensure_generated()
    summary = json.loads(SPLIT_SUMMARY_PATH.read_text(encoding="utf-8"))
    seed_sets = {split: set(seeds) for split, seeds in summary["splits"].items()}
    all_seeds = [seed for seeds in seed_sets.values() for seed in seeds]
    assert len(all_seeds) == len(set(all_seeds)), "a seed appears in more than one split"
    assert len(seed_sets) == 3
    for seeds in seed_sets.values():
        assert len(seeds) >= 2


def test_no_entity_id_or_event_id_crosses_split_boundaries() -> None:
    _ensure_generated()
    entity_ids_by_split: dict[str, set[str]] = {}
    event_ids_by_split: dict[str, set[str]] = {}
    for split in SPLITS:
        entity_ids_by_split[split] = {
            r["entity_id"] for r in _load_jsonl(RUN_DIR / split / "demand_entities.jsonl")
        }
        event_ids_by_split[split] = {
            e["event_id"] for e in _load_jsonl(RUN_DIR / split / "fault_events.jsonl")
        }
    for ids_by_split, label in (
        (entity_ids_by_split, "entity_id"),
        (event_ids_by_split, "event_id"),
    ):
        for i, a in enumerate(SPLITS):
            for b in SPLITS[i + 1 :]:
                overlap = ids_by_split[a] & ids_by_split[b]
                assert not overlap, f"{label} overlap between {a} and {b}: {overlap}"


def test_all_splits_are_nonempty_and_cover_all_fault_types() -> None:
    _ensure_generated()
    required_fault_types = {"silence", "stuck", "clock", "network", "model", "service", "storage"}
    for split in SPLITS:
        demand = _load_jsonl(RUN_DIR / split / "demand_entities.jsonl")
        ground_truth = _load_jsonl(RUN_DIR / split / "fault_ground_truth.jsonl")
        assert demand, f"{split} has no demand records"
        assert ground_truth, f"{split} has no fault ground truth"
        fault_types_present = {g["scenario_type"] for g in ground_truth}
        assert required_fault_types <= fault_types_present, (
            f"{split} missing fault types: {required_fault_types - fault_types_present}"
        )


def test_fault_events_conform_to_observation_envelope_contract() -> None:
    _ensure_generated()
    schema = json.loads(
        (CONTRACTS_ROOT / "observation-envelope" / "v1" / "schema.json").read_text(encoding="utf-8")
    )
    for split in SPLITS:
        for event in _load_jsonl(RUN_DIR / split / "fault_events.jsonl"):
            jsonschema.validate(instance=event, schema=schema)


def test_splits_are_not_byte_identical_to_each_other() -> None:
    _ensure_generated()
    contents = [
        (RUN_DIR / split / "fault_events.jsonl").read_text(encoding="utf-8") for split in SPLITS
    ]
    assert len(set(contents)) == len(contents), "two splits produced identical fault_events content"


def test_splits_are_byte_identical_across_two_full_runs() -> None:
    run_b = RUN_DIR.parent / "run-b"
    if not all((run_b / split).is_dir() for split in SPLITS):
        pytest.skip(
            "second run output not present; build_and_verify.py produces both run-a and run-b"
        )
    _ensure_generated()
    for split in SPLITS:
        for filename in ("demand_entities.jsonl", "fault_ground_truth.jsonl", "fault_events.jsonl"):
            a = (RUN_DIR / split / filename).read_text(encoding="utf-8")
            b = (run_b / split / filename).read_text(encoding="utf-8")
            assert a == b, f"{split}/{filename} differs across two runs of the same seeds"
