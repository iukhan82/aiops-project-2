"""P03.04: validate the generated emergency call/assignment/device/AVL
records against contracts/emergency-call/v1, contracts/emergency-unit-
assignment/v1, contracts/device/v1 and contracts/observation-envelope/v1.

The records are produced by
source-code/simulator/emergency/build_and_verify.py inside the pinned SUMO
container (see source-code/simulator/emergency/run_container.sh); this test
reads that already-generated, git-ignored output rather than regenerating it,
since SUMO/Docker are not available in the plain pytest venv.
"""

import json
from pathlib import Path

import jsonschema
import pytest

CONTRACTS_ROOT = Path(__file__).resolve().parents[1] / "contracts"
RUN_DIR = Path(__file__).resolve().parents[1] / "simulator" / "emergency" / "output" / "run-a"
CALLS_PATH = RUN_DIR / "calls.jsonl"
ASSIGNMENTS_PATH = RUN_DIR / "assignments.jsonl"
DEVICES_PATH = RUN_DIR / "devices.jsonl"
AVL_PATH = RUN_DIR / "avl_events.jsonl"


def _require_generated_output() -> None:
    if not all(p.is_file() for p in (CALLS_PATH, ASSIGNMENTS_PATH, DEVICES_PATH, AVL_PATH)):
        pytest.skip(
            "emergency scenario not generated; run "
            "source-code/simulator/emergency/run_container.sh first"
        )


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _load_schema(*parts: str) -> dict:
    return json.loads((CONTRACTS_ROOT.joinpath(*parts) / "schema.json").read_text(encoding="utf-8"))


def test_calls_conform_to_emergency_call_contract() -> None:
    _require_generated_output()
    schema = _load_schema("emergency-call", "v1")
    calls = _load_jsonl(CALLS_PATH)
    assert calls, "no emergency calls generated"
    call_types = {c["call_type"] for c in calls}
    assert call_types == {"ambulance", "fire", "police"}
    for call in calls:
        jsonschema.validate(instance=call, schema=schema)


def test_assignments_conform_to_emergency_unit_assignment_contract() -> None:
    _require_generated_output()
    schema = _load_schema("emergency-unit-assignment", "v1")
    assignments = _load_jsonl(ASSIGNMENTS_PATH)
    calls = {c["call_id"] for c in _load_jsonl(CALLS_PATH)}
    assert assignments, "no emergency unit assignments generated"
    for assignment in assignments:
        jsonschema.validate(instance=assignment, schema=schema)
        assert assignment["call_id"] in calls
        assert assignment["route_alternatives"]


def test_devices_conform_to_device_contract() -> None:
    _require_generated_output()
    schema = _load_schema("device", "v1")
    devices = _load_jsonl(DEVICES_PATH)
    assert devices, "no emergency CAD/AVL devices generated"
    for device in devices:
        jsonschema.validate(instance=device, schema=schema)
        assert device["device_type"] == "emergency_cad_avl_adapter"


def test_avl_events_conform_to_observation_envelope_contract() -> None:
    _require_generated_output()
    schema = _load_schema("observation-envelope", "v1")
    events = _load_jsonl(AVL_PATH)
    assert events, "no AVL position events generated"
    for event in events:
        jsonschema.validate(instance=event, schema=schema)
        assert event["event_type"] == "emergency.unit_position.avl"


def test_no_patient_data_anywhere() -> None:
    _require_generated_output()
    for path in (CALLS_PATH, ASSIGNMENTS_PATH, DEVICES_PATH, AVL_PATH):
        text = path.read_text(encoding="utf-8").lower()
        assert "patient" not in text, f"{path} contains a 'patient' field or value"


def test_records_are_byte_identical_across_runs() -> None:
    run_b = RUN_DIR.parent / "run-b"
    if not all(
        (run_b / name).is_file()
        for name in ("calls.jsonl", "assignments.jsonl", "devices.jsonl", "avl_events.jsonl")
    ):
        pytest.skip("second run output not present; run_container.sh produces both run-a and run-b")
    _require_generated_output()
    for name, run_a_path in (
        ("calls.jsonl", CALLS_PATH),
        ("assignments.jsonl", ASSIGNMENTS_PATH),
        ("devices.jsonl", DEVICES_PATH),
        ("avl_events.jsonl", AVL_PATH),
    ):
        assert run_a_path.read_text(encoding="utf-8") == (run_b / name).read_text(encoding="utf-8")
