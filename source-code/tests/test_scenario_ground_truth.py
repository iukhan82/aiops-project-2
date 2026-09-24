"""P03.05: validate scenario ground truth and overlay observation events.

Ground truth (`ground_truth.jsonl`) is a plain internal JSON shape, not a
formal versioned contract - Phase 02's 15-contract set is already closed and
this is SIM-scoped scenario labeling, not a new platform interface. Overlay
events (`overlay_events.jsonl`) reuse contracts/observation-envelope/v1
exactly, so those are schema-validated.

Reads the already-generated, git-ignored output from
source-code/simulator/scenarios/build_and_verify.py (see
source-code/simulator/scenarios/run_container.sh) rather than regenerating
it, since SUMO/Docker are not available in the plain pytest venv.
"""

import json
from pathlib import Path

import jsonschema
import pytest

CONTRACTS_ROOT = Path(__file__).resolve().parents[1] / "contracts"
RUN_DIR = Path(__file__).resolve().parents[1] / "simulator" / "scenarios" / "output" / "run-a"
GROUND_TRUTH_PATH = RUN_DIR / "ground_truth.jsonl"
OVERLAY_EVENTS_PATH = RUN_DIR / "overlay_events.jsonl"
PHYSICAL_SUMMARY_PATH = RUN_DIR / "physical_summary.json"

_REQUIRED_SCENARIO_TYPES = {
    "normal",
    "peak",
    "event",
    "stall",
    "collision",
    "wrong_way",
    "flood",
    "visibility",
    "signal",
}
_GROUND_TRUTH_REQUIRED_FIELDS = {
    "scenario_id",
    "scenario_type",
    "onset",
    "end",
    "affected_entities",
    "description",
    "truth_label",
    "synthetic_overlay",
}


def _require_generated_output() -> None:
    if not all(
        p.is_file() for p in (GROUND_TRUTH_PATH, OVERLAY_EVENTS_PATH, PHYSICAL_SUMMARY_PATH)
    ):
        pytest.skip(
            "scenarios not generated; run source-code/simulator/scenarios/run_container.sh first"
        )


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_all_required_scenario_types_have_ground_truth() -> None:
    _require_generated_output()
    records = _load_jsonl(GROUND_TRUTH_PATH)
    assert records, "no ground truth records generated"
    types = {r["scenario_type"] for r in records}
    missing = _REQUIRED_SCENARIO_TYPES - types
    assert not missing, f"missing ground truth for scenario types: {sorted(missing)}"


def test_ground_truth_records_have_required_fields_and_valid_onset_before_end() -> None:
    _require_generated_output()
    records = _load_jsonl(GROUND_TRUTH_PATH)
    for record in records:
        missing_fields = _GROUND_TRUTH_REQUIRED_FIELDS - record.keys()
        assert not missing_fields, f"{record.get('scenario_id')} missing fields: {missing_fields}"
        assert record["onset"] <= record["end"], f"{record['scenario_id']} has onset after end"
        assert isinstance(record["affected_entities"], list)
        assert record["truth_label"] == "simulated"


def test_physical_scenarios_are_synthetic_overlay_false_and_overlays_true() -> None:
    _require_generated_output()
    records = {r["scenario_type"]: r for r in _load_jsonl(GROUND_TRUTH_PATH)}
    for physical_type in {"normal", "peak", "event", "stall"}:
        assert records[physical_type]["synthetic_overlay"] is False
    for overlay_type in {"collision", "wrong_way", "flood", "visibility", "signal"}:
        assert records[overlay_type]["synthetic_overlay"] is True


def test_physical_scenarios_had_zero_teleports_and_collisions() -> None:
    _require_generated_output()
    summary = json.loads(PHYSICAL_SUMMARY_PATH.read_text(encoding="utf-8"))
    for scenario_type in ("normal", "peak", "event", "stall"):
        assert summary[scenario_type]["teleports"] == 0
        assert summary[scenario_type]["collisions"] == 0


def test_peak_demand_scenario_loaded_more_vehicles_than_normal() -> None:
    _require_generated_output()
    summary = json.loads(PHYSICAL_SUMMARY_PATH.read_text(encoding="utf-8"))
    assert summary["peak"]["loaded"] > summary["normal"]["loaded"]


def test_overlay_events_conform_to_observation_envelope_contract() -> None:
    _require_generated_output()
    schema = json.loads(
        (CONTRACTS_ROOT / "observation-envelope" / "v1" / "schema.json").read_text(encoding="utf-8")
    )
    events = _load_jsonl(OVERLAY_EVENTS_PATH)
    assert events, "no overlay events generated"
    for event in events:
        jsonschema.validate(instance=event, schema=schema)


def test_each_overlay_scenario_has_exactly_two_events() -> None:
    _require_generated_output()
    events = _load_jsonl(OVERLAY_EVENTS_PATH)
    by_correlation: dict[str, int] = {}
    for event in events:
        by_correlation[event["correlation_id"]] = by_correlation.get(event["correlation_id"], 0) + 1
    for scenario_type in ("collision", "wrong_way", "flood", "visibility", "signal"):
        key = f"scenario-p03-05-{scenario_type}"
        assert by_correlation.get(key) == 2, (
            f"{key} has {by_correlation.get(key)} events, expected 2"
        )


def test_records_are_byte_identical_across_runs() -> None:
    run_b = RUN_DIR.parent / "run-b"
    if not all(
        (run_b / name).is_file()
        for name in ("ground_truth.jsonl", "overlay_events.jsonl", "physical_summary.json")
    ):
        pytest.skip("second run output not present; run_container.sh produces both run-a and run-b")
    _require_generated_output()
    for name, run_a_path in (
        ("ground_truth.jsonl", GROUND_TRUTH_PATH),
        ("overlay_events.jsonl", OVERLAY_EVENTS_PATH),
        ("physical_summary.json", PHYSICAL_SUMMARY_PATH),
    ):
        assert run_a_path.read_text(encoding="utf-8") == (run_b / name).read_text(encoding="utf-8")
