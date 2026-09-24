"""P03.06: validate device/platform fault ground truth and events.

Unlike P03.03-P03.05, source-code/simulator/faults/build_and_verify.py needs
no SUMO/Docker (these are platform-layer faults, not road physics), so it
runs directly on the host venv - this test can regenerate the output itself
if missing, rather than only reading pre-generated files.
"""

import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

CONTRACTS_ROOT = Path(__file__).resolve().parents[1] / "contracts"
FAULTS_DIR = Path(__file__).resolve().parents[1] / "simulator" / "faults"
NET_FILE = (
    Path(__file__).resolve().parents[1] / "simulator" / "network" / "output" / "district.net.xml"
)
RUN_DIR = FAULTS_DIR / "output" / "run-a"
GROUND_TRUTH_PATH = RUN_DIR / "ground_truth.jsonl"
EVENTS_PATH = RUN_DIR / "fault_events.jsonl"
DEVICE_PATH = RUN_DIR / "aiops_agent_device.json"

_REQUIRED_FAULT_TYPES = {"silence", "stuck", "clock", "network", "model", "service", "storage"}
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


def _ensure_generated() -> None:
    if GROUND_TRUTH_PATH.is_file() and EVENTS_PATH.is_file() and DEVICE_PATH.is_file():
        return
    if not NET_FILE.is_file():
        pytest.skip("network not built (gitignored); run simulator/network/run_container.sh first")
    subprocess.run(
        [sys.executable, str(FAULTS_DIR / "build_and_verify.py")],
        check=True,
        cwd=FAULTS_DIR.parents[2],
    )
    if not (GROUND_TRUTH_PATH.is_file() and EVENTS_PATH.is_file() and DEVICE_PATH.is_file()):
        pytest.skip("fault scenario output still missing after attempting to generate it")


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_all_required_fault_types_have_ground_truth() -> None:
    _ensure_generated()
    records = _load_jsonl(GROUND_TRUTH_PATH)
    assert records, "no fault ground truth generated"
    types = {r["scenario_type"] for r in records}
    missing = _REQUIRED_FAULT_TYPES - types
    assert not missing, f"missing ground truth for fault types: {sorted(missing)}"


def test_ground_truth_records_have_required_fields_and_valid_onset_before_end() -> None:
    _ensure_generated()
    for record in _load_jsonl(GROUND_TRUTH_PATH):
        missing_fields = _GROUND_TRUTH_REQUIRED_FIELDS - record.keys()
        assert not missing_fields, f"{record.get('scenario_id')} missing fields: {missing_fields}"
        assert record["onset"] <= record["end"]
        assert record["synthetic_overlay"] is True


def test_events_conform_to_observation_envelope_contract() -> None:
    _ensure_generated()
    schema = json.loads(
        (CONTRACTS_ROOT / "observation-envelope" / "v1" / "schema.json").read_text(encoding="utf-8")
    )
    events = _load_jsonl(EVENTS_PATH)
    assert events, "no fault events generated"
    for event in events:
        jsonschema.validate(instance=event, schema=schema)


def test_aiops_agent_device_conforms_to_device_contract() -> None:
    _ensure_generated()
    schema = json.loads(
        (CONTRACTS_ROOT / "device" / "v1" / "schema.json").read_text(encoding="utf-8")
    )
    device = json.loads(DEVICE_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(instance=device, schema=schema)
    assert device["device_type"] == "aiops_agent"


def test_each_fault_has_exactly_two_events() -> None:
    _ensure_generated()
    events = _load_jsonl(EVENTS_PATH)
    by_correlation: dict[str, int] = {}
    for event in events:
        by_correlation[event["correlation_id"]] = by_correlation.get(event["correlation_id"], 0) + 1
    for fault_type in _REQUIRED_FAULT_TYPES:
        key = f"fault-p03-06-{fault_type}"
        assert by_correlation.get(key) == 2, (
            f"{key} has {by_correlation.get(key)} events, expected 2"
        )


def test_clock_fault_sets_drifting_clock_quality() -> None:
    _ensure_generated()
    events = [e for e in _load_jsonl(EVENTS_PATH) if e["correlation_id"] == "fault-p03-06-clock"]
    onset = min(events, key=lambda e: e["sequence_number"])
    assert onset["clock_quality"] == "drifting"


def test_records_are_byte_identical_across_runs() -> None:
    run_b = RUN_DIR.parent / "run-b"
    if not all(
        (run_b / name).is_file()
        for name in ("ground_truth.jsonl", "fault_events.jsonl", "aiops_agent_device.json")
    ):
        pytest.skip(
            "second run output not present; build_and_verify.py produces both run-a and run-b"
        )
    _ensure_generated()
    for name, run_a_path in (
        ("ground_truth.jsonl", GROUND_TRUTH_PATH),
        ("fault_events.jsonl", EVENTS_PATH),
        ("aiops_agent_device.json", DEVICE_PATH),
    ):
        assert run_a_path.read_text(encoding="utf-8") == (run_b / name).read_text(encoding="utf-8")
