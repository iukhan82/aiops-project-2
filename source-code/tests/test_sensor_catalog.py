"""P03.03: validate the generated sensor device catalog and observation
events against contracts/device/v1 and contracts/observation-envelope/v1.

The catalog/events are produced by
source-code/simulator/sensors/build_and_verify.py inside the pinned SUMO
container (see source-code/simulator/sensors/run_container.sh); this test
reads that already-generated, git-ignored output rather than regenerating it,
since SUMO/Docker are not available in the plain pytest venv.
"""

import json
from pathlib import Path

import jsonschema
import pytest

CONTRACTS_ROOT = Path(__file__).resolve().parents[1] / "contracts"
RUN_DIR = Path(__file__).resolve().parents[1] / "simulator" / "sensors" / "output" / "run-a"
DEVICES_PATH = RUN_DIR / "devices.jsonl"
EVENTS_PATH = RUN_DIR / "observations.jsonl"

_REQUIRED_CATEGORIES = {"traffic", "vru", "signal", "weather", "road"}
_REQUIRED_DEVICE_TYPES = {
    "inductive_loop",
    "cycle_counter",
    "crossing_detector",
    "signal_controller",
    "weather_station",
    "road_condition_sensor",
}


def _require_generated_output() -> None:
    if not DEVICES_PATH.is_file() or not EVENTS_PATH.is_file():
        pytest.skip(
            "sensor catalog not generated; run source-code/simulator/sensors/run_container.sh first"
        )


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_devices_conform_to_device_contract() -> None:
    _require_generated_output()
    schema = json.loads(
        (CONTRACTS_ROOT / "device" / "v1" / "schema.json").read_text(encoding="utf-8")
    )
    devices = _load_jsonl(DEVICES_PATH)
    assert devices, "device catalog is empty"
    for device in devices:
        jsonschema.validate(instance=device, schema=schema)

    device_types = {d["device_type"] for d in devices}
    missing = _REQUIRED_DEVICE_TYPES - device_types
    assert not missing, f"missing device types in catalog: {sorted(missing)}"


def test_events_conform_to_observation_envelope_contract() -> None:
    _require_generated_output()
    schema = json.loads(
        (CONTRACTS_ROOT / "observation-envelope" / "v1" / "schema.json").read_text(encoding="utf-8")
    )
    events = _load_jsonl(EVENTS_PATH)
    assert events, "observation event stream is empty"
    for event in events:
        jsonschema.validate(instance=event, schema=schema)


def test_events_cover_all_required_sensor_categories() -> None:
    _require_generated_output()
    events = _load_jsonl(EVENTS_PATH)
    categories = {event["event_type"].split(".")[0] for event in events}
    missing = _REQUIRED_CATEGORIES - categories
    assert not missing, f"missing required sensor categories: {sorted(missing)}"


def test_every_measurement_carries_unit_quality_and_confidence() -> None:
    _require_generated_output()
    events = _load_jsonl(EVENTS_PATH)
    for event in events:
        assert event["measurements"], f"{event['event_id']} has no measurements"
        for measurement in event["measurements"]:
            assert measurement["unit"], f"{event['event_id']} measurement missing unit"
            assert measurement["quality"] in {"valid", "suspect", "invalid"}
            assert 0.0 <= measurement["confidence"] <= 1.0


def test_every_event_carries_identity_location_and_provenance() -> None:
    _require_generated_output()
    events = _load_jsonl(EVENTS_PATH)
    for event in events:
        assert event["device_id"]
        assert event["event_id"]
        location = event["location"]
        assert location["coordinate_reference"] == "EPSG:4326"
        assert -90 <= location["latitude"] <= 90
        assert -180 <= location["longitude"] <= 180
        assert event["provenance"]["producer"]
        assert event["provenance"]["pipeline_version"]


def test_device_catalog_and_events_are_byte_identical_across_runs() -> None:
    run_b_devices = RUN_DIR.parent / "run-b" / "devices.jsonl"
    run_b_events = RUN_DIR.parent / "run-b" / "observations.jsonl"
    if not run_b_devices.is_file() or not run_b_events.is_file():
        pytest.skip("second run output not present; run_container.sh produces both run-a and run-b")
    _require_generated_output()
    assert DEVICES_PATH.read_text(encoding="utf-8") == run_b_devices.read_text(encoding="utf-8")
    assert EVENTS_PATH.read_text(encoding="utf-8") == run_b_events.read_text(encoding="utf-8")


def test_sequence_numbers_are_contiguous_and_chronological_per_device() -> None:
    """Regression guard: P04.01's edge validator found crossing-detector
    events whose sequence numbers were assigned in generation order rather
    than observation-time order (a clearance can follow the next demand)."""
    _require_generated_output()
    by_device: dict[str, list[dict]] = {}
    for event in _load_jsonl(EVENTS_PATH):
        by_device.setdefault(event["device_id"], []).append(event)
    for device_id, events in by_device.items():
        events.sort(key=lambda e: e["observation_time"])
        assert [e["sequence_number"] for e in events] == list(range(len(events))), device_id
