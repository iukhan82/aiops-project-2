"""P07.04: the recommendation shape and hard safety-bounds enforcement, on
hand-built alternatives. Real diversion/signal generators against live
incidents and KPI data are proven by backend/control/verify_recommendations.py
(docs/evidence/p07_04_recommendations.json)."""

import pytest

from backend.control.engine import Alternative, Metric, SafetyBounds, UnsafeAlternative, enforce

BOUNDS = SafetyBounds(min_pedestrian_clearance_s=7.0, max_signal_deviation_s=20.0)


def alt(deviation=0.0, clearance=None, confidence=0.5) -> Alternative:
    return Alternative(
        "a1",
        "test alternative",
        (Metric("benefit", 1.0, "s"),),
        (Metric("harm", 1.0, "s"),),
        confidence,
        signal_deviation_s=deviation,
        pedestrian_clearance_s=clearance,
    )


def test_an_alternative_within_bounds_is_kept() -> None:
    kept = enforce([alt(deviation=10.0, clearance=7.0)], BOUNDS)
    assert len(kept) == 1


def test_an_alternative_exceeding_the_signal_deviation_bound_is_dropped() -> None:
    within, over = alt(deviation=10.0), alt(deviation=25.0)
    kept = enforce([within, over], BOUNDS)
    assert kept == [within]


def test_an_alternative_below_the_pedestrian_clearance_floor_is_dropped() -> None:
    safe, unsafe = alt(clearance=7.0), alt(clearance=5.0)
    kept = enforce([safe, unsafe], BOUNDS)
    assert kept == [safe]


def test_an_alternative_that_does_not_touch_pedestrian_clearance_is_unaffected_by_that_bound() -> (
    None
):
    kept = enforce([alt(clearance=None)], BOUNDS)
    assert len(kept) == 1


def test_when_every_alternative_is_unsafe_the_engine_raises_rather_than_emitting_nothing() -> None:
    with pytest.raises(UnsafeAlternative):
        enforce([alt(deviation=99.0), alt(clearance=1.0)], BOUNDS)


def test_alternative_record_matches_the_recommendation_contract_shape() -> None:
    record = alt().as_record()
    assert set(record) == {
        "alternative_id",
        "description",
        "predicted_benefit",
        "predicted_harm",
        "confidence",
    }
    assert record["predicted_benefit"][0] == {"name": "benefit", "value": 1.0, "unit": "s"}


def test_safety_bounds_record_matches_the_recommendation_contract_shape() -> None:
    assert set(BOUNDS.as_record()) == {"min_pedestrian_clearance_s", "max_signal_deviation_s"}
