"""P11.06: the cached validator gives the same answer as jsonschema.validate, and is fast enough to matter."""

import json
import time
import uuid
from pathlib import Path

import jsonschema
import pytest
from backend import schema_validation

SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "contracts"
        / "observation-envelope"
        / "v1"
        / "schema.json"
    ).read_text(encoding="utf-8")
)


def event(**changes):
    base = {
        "schema_version": "1.0.0", "event_id": str(uuid.uuid4()), "event_type": "traffic.loop_detector.count", "device_id": "d1", "agency_scope": "a",
        "observation_time": "2026-09-25T19:00:00.000Z", "ingest_time": "2026-09-25T19:00:00.000Z", "sequence_number": 1, "clock_quality": "synced",
        "geometry_version": "g", "location": {"coordinate_reference": "EPSG:4326", "latitude": 1.0, "longitude": 2.0},
        "measurements": [{"name": "vehicle_count", "value": 3, "unit": "count", "quality": "valid", "confidence": 0.9}],
        "truth_label": "simulated", "privacy_classification": "none", "retention_class": "standard",
    }  # fmt: skip
    return {**base, **changes}


def test_a_valid_event_has_no_error():
    assert schema_validation.first_error(SCHEMA, event()) is None


@pytest.mark.parametrize(
    "bad",
    [
        event(sequence_number=-1),
        event(clock_quality="sometimes"),
        {k: v for k, v in event().items() if k != "device_id"},
        event(location={"coordinate_reference": "EPSG:3857", "latitude": 1.0, "longitude": 2.0}),
        event(unexpected="field"),
    ],
)
def test_an_invalid_event_gets_the_message_jsonschema_validate_would_raise(bad):
    with pytest.raises(jsonschema.ValidationError) as raised:
        jsonschema.validate(instance=bad, schema=SCHEMA)
    error = schema_validation.first_error(SCHEMA, bad)
    assert error is not None
    assert error.message == raised.value.message


def test_the_validator_is_reused_and_faster_than_validating_from_scratch():
    good = event()
    started = time.perf_counter()
    for _ in range(200):
        jsonschema.validate(instance=good, schema=SCHEMA)
    slow = time.perf_counter() - started
    schema_validation.first_error(SCHEMA, good)
    started = time.perf_counter()
    for _ in range(200):
        schema_validation.first_error(SCHEMA, good)
    fast = time.perf_counter() - started
    assert fast * 5 < slow, (slow, fast)
