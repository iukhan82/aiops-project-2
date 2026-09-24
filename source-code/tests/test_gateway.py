"""P05.03: gateway pure-logic unit tests (schema validation, topic parsing).

End-to-end behavior (real MQTT/Kafka, partitioning, backpressure across a
genuine broker outage, replay) needs the real P05.01/P05.02 Docker stack and
is proven by source-code/backend/gateway/verify_gateway.py, not here -
see docs/evidence/p05_03_gateway.json and source-code/infra/README.md.
"""

from backend.gateway.gateway import device_id_from_topic, load_schema, validate_envelope


def _valid_event() -> dict:
    return {
        "schema_version": "1.0.0",
        "event_id": "8f14e45f-ceea-467e-adde-3fb5ba90a0d2",
        "event_type": "traffic.loop_detector.count",
        "device_id": "corridor-a-int-03-loop-01",
        "agency_scope": "city-traffic-ops",
        "observation_time": "2026-09-19T18:00:00Z",
        "ingest_time": "2026-09-19T18:00:00Z",
        "sequence_number": 1,
        "clock_quality": "synced",
        "geometry_version": "2026-09-18.1",
        "location": {
            "coordinate_reference": "EPSG:4326",
            "latitude": 31.5204,
            "longitude": 74.3587,
        },
        "measurements": [
            {
                "name": "vehicle_count",
                "value": 1,
                "unit": "count",
                "quality": "valid",
                "confidence": 0.95,
            }
        ],
        "truth_label": "simulated",
        "privacy_classification": "none",
        "retention_class": "standard",
    }


def test_valid_envelope_passes() -> None:
    schema = load_schema()
    assert validate_envelope(_valid_event(), schema) is None


def test_missing_required_field_is_rejected() -> None:
    schema = load_schema()
    event = _valid_event()
    del event["measurements"]
    reason = validate_envelope(event, schema)
    assert reason is not None
    assert "measurements" in reason


def test_wrong_type_is_rejected() -> None:
    schema = load_schema()
    event = _valid_event()
    event["sequence_number"] = "not-a-number"
    assert validate_envelope(event, schema) is not None


def test_additional_property_is_rejected() -> None:
    schema = load_schema()
    event = _valid_event()
    event["unexpected_field"] = "nope"
    assert validate_envelope(event, schema) is not None


def test_device_id_from_topic_parses_telemetry_topic() -> None:
    assert (
        device_id_from_topic("devices/corridor-a-int-03-loop-01/telemetry")
        == "corridor-a-int-03-loop-01"
    )


def test_device_id_from_topic_rejects_other_shapes() -> None:
    assert device_id_from_topic("devices/foo/ack") is None
    assert device_id_from_topic("foo/bar") is None
    assert device_id_from_topic("devices/telemetry") is None
