"""P04.01: edge intake validation - invalid/stale/missing/duplicate behavior."""

import copy
import random
from datetime import timedelta

import jsonschema
import pytest
from edge_helpers import GEOMETRY, T0, device, event, make_registry, make_validator

from edge.validation import DeviceRegistry, Status

NOW = T0 + timedelta(seconds=1)


def test_valid_event_is_accepted_fresh_with_usable_measurements() -> None:
    verdict = make_validator().validate(event(), NOW)
    assert verdict.status is Status.ACCEPTED
    assert verdict.fresh is True
    assert verdict.flags == ()
    assert verdict.measurements == {"vehicle_count": 3, "occupancy": 0.1, "mean_speed": 10.0}


@pytest.mark.parametrize("raw", [None, [], "text", 7, 3.5, True, b"bytes"])
def test_non_object_input_is_rejected_not_raised(raw: object) -> None:
    verdict = make_validator().validate(raw, NOW)
    assert verdict.status is Status.REJECTED
    assert verdict.reasons == ("not_object",)


def _mutations():
    def drop(field):
        def apply(e):
            del e[field]

        return apply

    def setv(field, value):
        def apply(e):
            e[field] = value

        return apply

    return {
        "missing_event_id": drop("event_id"),
        "missing_measurements": drop("measurements"),
        "empty_measurements": setv("measurements", []),
        "extra_property": setv("surprise", 1),
        "bad_clock_quality": setv("clock_quality", "maybe"),
        "seq_string": setv("sequence_number", "1"),
        "seq_negative": setv("sequence_number", -1),
        "seq_bool": setv("sequence_number", True),
        "bad_truth_label": setv("truth_label", "guessed"),
        "location_missing_lat": lambda e: e["location"].pop("latitude"),
        "lat_out_of_range": lambda e: e["location"].__setitem__("latitude", 123.0),
        "measurement_no_unit": lambda e: e["measurements"][0].pop("unit"),
        "confidence_above_one": lambda e: e["measurements"][0].__setitem__("confidence", 1.5),
        "confidence_negative": lambda e: e["measurements"][0].__setitem__("confidence", -0.1),
        "measurement_bad_quality": lambda e: e["measurements"][0].__setitem__("quality", "great"),
    }


@pytest.mark.parametrize("name", sorted(_mutations()))
def test_schema_violations_are_rejected_with_detail(name: str) -> None:
    raw = event()
    _mutations()[name](raw)
    verdict = make_validator().validate(raw, NOW)
    assert verdict.status is Status.REJECTED
    assert verdict.reasons == ("schema_invalid",)
    assert verdict.detail


def test_bad_event_id_and_timestamps_are_rejected() -> None:
    v = make_validator()
    raw = event()
    raw["event_id"] = "not-a-uuid"
    assert v.validate(raw, NOW).reasons == ("bad_event_id",)

    raw = event()
    raw["observation_time"] = "2026-09-18T09:00:00"  # naive: no UTC offset
    assert v.validate(raw, NOW).reasons == ("bad_timestamp",)

    raw = event()
    raw["ingest_time"] = "yesterday"
    assert v.validate(raw, NOW).reasons == ("bad_timestamp",)


def test_future_timestamp_rejected_beyond_skew_but_tolerated_within() -> None:
    v = make_validator()
    assert v.validate(event(obs=NOW + timedelta(seconds=30)), NOW).reasons == ("future_timestamp",)
    within = v.validate(event(seq=1, obs=NOW + timedelta(seconds=3)), NOW)
    assert within.usable


@pytest.mark.parametrize(
    ("kwargs", "transport", "reason"),
    [
        ({"device_id": "loop-unknown"}, None, "unknown_device"),
        ({"device_id": "loop-dead"}, None, "device_inactive"),
        ({"device_id": "loop-fire"}, None, "agency_mismatch"),
        ({}, "loop-b", "identity_mismatch"),
        ({"device_id": "cyc-a"}, None, "event_type_device_mismatch"),
        ({"event_type": "made.up.type"}, None, "event_type_device_mismatch"),
    ],
)
def test_identity_and_registry_rejections(kwargs: dict, transport: str | None, reason: str) -> None:
    raw = event(**kwargs)
    if kwargs.get("device_id") == "loop-fire":
        raw["agency_scope"] = "city-traffic-ops"  # registry says fire-dispatch
    verdict = make_validator().validate(raw, NOW, transport_identity=transport)
    assert verdict.reasons == (reason,)


def test_geometry_mismatch_and_disallowed_truth_label_rejected() -> None:
    v = make_validator()
    raw = event()
    raw["geometry_version"] = "2099-01-01.1"
    assert v.validate(raw, NOW).reasons == ("geometry_version_mismatch",)
    raw = event()
    raw["truth_label"] = "inferred"  # edge's own outputs must not re-enter intake
    assert v.validate(raw, NOW).reasons == ("truth_label_not_allowed",)


def test_duplicate_event_id_and_sequence_conflict_and_out_of_order() -> None:
    v = make_validator()
    first = event(seq=5)
    assert v.validate(first, NOW).usable
    assert v.validate(copy.deepcopy(first), NOW).reasons == ("duplicate_event_id",)

    conflicting = event(seq=5, obs=T0 + timedelta(seconds=1))  # same seq, different event
    assert v.validate(conflicting, NOW + timedelta(seconds=2)).reasons == ("sequence_conflict",)

    v2 = make_validator()
    assert v2.validate(event(seq=10), NOW).usable
    assert v2.validate(
        event(seq=9, obs=T0 + timedelta(seconds=2)), NOW + timedelta(seconds=3)
    ).reasons == ("out_of_order_sequence",)


def test_sequence_gap_is_flagged_with_size_but_accepted() -> None:
    v = make_validator()
    assert v.validate(event(seq=0), NOW).status is Status.ACCEPTED
    gap = v.validate(event(seq=4, obs=T0 + timedelta(seconds=30)), NOW + timedelta(seconds=31))
    assert gap.status is Status.FLAGGED
    assert "sequence_gap:3" in gap.flags
    assert gap.usable


def test_devices_have_independent_sequences() -> None:
    v = make_validator()
    assert v.validate(event("loop-a", seq=7), NOW).usable
    assert v.validate(event("loop-b", seq=0), NOW).usable


def test_stale_event_is_accepted_with_flag_but_not_fresh() -> None:
    old = event(obs=T0)
    verdict = make_validator().validate(old, T0 + timedelta(seconds=600))
    assert verdict.status is Status.FLAGGED
    assert "stale" in verdict.flags
    assert verdict.fresh is False
    assert verdict.usable


def test_high_ingest_lag_and_unsynced_clock_are_flagged_and_not_fresh() -> None:
    v = make_validator()
    laggy = v.validate(event(seq=0, lag_s=90.0), NOW + timedelta(seconds=100))
    assert "ingest_lag_high" in laggy.flags
    unsynced = v.validate(
        event(seq=1, obs=T0 + timedelta(seconds=30), clock="drifting"), NOW + timedelta(seconds=31)
    )
    assert "clock_not_synced" in unsynced.flags
    assert unsynced.fresh is False


def test_masked_measurements_never_reach_usable_values() -> None:
    v = make_validator()
    invalid = v.validate(event(seq=0, quality="invalid"), NOW)
    assert invalid.measurements == {}
    assert {"measurement_invalid:vehicle_count", "measurement_invalid:occupancy"} <= set(
        invalid.flags
    )

    suspect = v.validate(
        event(seq=1, obs=T0 + timedelta(seconds=30), quality="suspect"), NOW + timedelta(seconds=31)
    )
    assert suspect.measurements == {}

    low = v.validate(
        event(seq=2, obs=T0 + timedelta(seconds=60), confidence=0.2), NOW + timedelta(seconds=61)
    )
    assert low.measurements == {}
    assert "low_confidence:vehicle_count" in low.flags


def test_missing_speed_is_valid_not_an_error() -> None:
    """P03.03: mean_speed is omitted when no vehicle crossed the loop."""
    verdict = make_validator().validate(event(count=0, occ=0.0, speed=None), NOW)
    assert verdict.status is Status.ACCEPTED
    assert "mean_speed" not in verdict.measurements


def test_event_id_memory_is_bounded() -> None:
    v = make_validator(seen_event_id_capacity=8)
    for i in range(50):
        v.validate(event(seq=i, obs=T0 + timedelta(seconds=i)), T0 + timedelta(seconds=i + 1))
    assert len(v._seen_ids) == 8


def test_registry_rejects_invalid_device_records() -> None:
    bad = device("loop-x", "inductive_loop")
    bad["device_type"] = "flux_capacitor"
    with pytest.raises(jsonschema.ValidationError):
        DeviceRegistry([bad])
    assert len(make_registry()) == 5


def test_fuzzed_garbage_never_raises() -> None:
    rng = random.Random(20260918)
    v = make_validator()
    base = event()
    for _ in range(400):
        raw = copy.deepcopy(base)
        for _ in range(rng.randint(1, 4)):
            key = rng.choice(list(raw))
            raw[key] = rng.choice([None, 1, -1, "x", [], {}, 3.14, True, "2026-13-45T99:99:99Z"])
        verdict = v.validate(raw, NOW)
        assert verdict.status in Status
        assert verdict.usable or verdict.reasons
    assert GEOMETRY  # helper import used
