"""P07.09: the pure classification rules and the contract shape of a stored
outcome (no database, no simulator). The real pipeline - real Postgres, a live
SUMO session, a physical undo read back from TraCI - is proven by
backend/control/verify_outcomes.py (docs/evidence/p07_09_outcomes.json)."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from backend.control.outcome_verification import Metric, classify
from backend.repositories.outcomes import _measurement_records, to_contract

SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[1] / "contracts" / "outcome" / "v1" / "schema.json"
    ).read_text(encoding="utf-8")
)
SAFETY, EFFECT = 20.0, 10.0


def m(value: float | None) -> Metric:
    return Metric("waiting_time", value, "vehicle_s")


def test_a_large_increase_in_a_lower_is_better_metric_is_unsafe() -> None:
    result = classify(m(10), m(400), SAFETY, EFFECT)
    assert result.classification == "unsafe" and result.delta == 390


def test_a_large_decrease_in_a_lower_is_better_metric_is_effective() -> None:
    assert classify(m(100), m(60), SAFETY, EFFECT).classification == "effective"


def test_a_change_inside_the_noise_band_is_ineffective_in_both_directions() -> None:
    assert classify(m(50), m(58), SAFETY, EFFECT).classification == "ineffective"
    assert classify(m(50), m(45), SAFETY, EFFECT).classification == "ineffective"


def test_a_missing_measurement_is_unknown_never_effective() -> None:
    assert classify(m(None), m(10), SAFETY, EFFECT).classification == "unknown"
    assert classify(m(10), m(None), SAFETY, EFFECT).classification == "unknown"
    assert classify(m(None), m(None), SAFETY, EFFECT).delta is None


def test_a_higher_is_better_metric_reverses_the_direction() -> None:
    assert classify(m(100), m(40), SAFETY, EFFECT, higher_is_worse=False).classification == "unsafe"
    assert (
        classify(m(100), m(160), SAFETY, EFFECT, higher_is_worse=False).classification
        == "effective"
    )


def test_the_thresholds_are_strict_so_exactly_on_the_line_is_not_a_finding() -> None:
    assert classify(m(0), m(SAFETY), SAFETY, EFFECT).classification == "ineffective"
    assert classify(m(EFFECT), m(0), SAFETY, EFFECT).classification == "ineffective"


def test_a_missing_value_is_recorded_as_a_zero_sample_count_not_an_invented_number() -> None:
    assert _measurement_records([m(None)]) == [
        {"name": "waiting_time_sample_count", "value": 0, "unit": "samples"}
    ]
    assert _measurement_records([m(3)]) == [
        {"name": "waiting_time", "value": 3.0, "unit": "vehicle_s"}
    ]


def _row(classification: str, rolled_back: bool, escalation: str | None, post: list) -> tuple:
    t = lambda minute: datetime(2026, 9, 20, 9, minute, tzinfo=timezone.utc)  # noqa: E731
    detail = {
        "pre_measurements": _measurement_records([m(10)]),
        "post_measurements": post,
        "escalation_reason": escalation,
    }
    return (
        "5a4b3c2d-1e0f-4a9b-8c7d-6e5f4a3b2c1d",
        "2e1a4c3b-6f5d-4e2a-8b3c-9d0e1f2a3b4c",
        t(0),
        t(2),
        t(3),
        t(5),
        classification,
        t(6),
        "outcome-verifier-service",
        rolled_back,
        detail,
    )


@pytest.mark.parametrize(
    "classification,rolled_back,escalation,post",
    [
        ("effective", False, None, _measurement_records([m(4)])),
        ("unsafe", True, None, _measurement_records([m(400)])),
        (
            "unknown",
            False,
            "insufficient evidence to classify: missing measurement",
            _measurement_records([m(None)]),
        ),
    ],
)
def test_a_stored_outcome_is_contract_valid_including_the_unknown_case(
    classification, rolled_back, escalation, post
) -> None:
    errors = [
        e.message
        for e in Draft202012Validator(SCHEMA).iter_errors(
            to_contract(_row(classification, rolled_back, escalation, post))
        )
    ]
    assert errors == []
