"""P07.03: the staging dependency ordering, pure logic, no database. Real
transitions, the actual staging block/release and handover records against
Postgres are proven by backend/emergency/verify_cross_agency.py
(docs/evidence/p07_03_cross_agency.json)."""

import pytest

from backend.emergency.cross_agency import StagingStep, StagingViolation, resolve_staging_order

ROUTE = {
    "route_id": "r",
    "distance_m": 1,
    "eta_seconds": 1,
    "eta_uncertainty_seconds": 1,
    "geometry_version": "g",
    "selected": True,
}


def step(unit: str, agency: str, after: str | None = None) -> StagingStep:
    return StagingStep(unit, agency, ["x"], ROUTE, after)


def test_units_with_no_dependency_can_be_ordered_in_any_relative_order() -> None:
    ordered = resolve_staging_order(
        [step("police-1", "police-dispatch"), step("fire-1", "fire-dispatch")]
    )
    assert {s.unit_id for s in ordered} == {"police-1", "fire-1"}


def test_a_staged_unit_is_ordered_after_the_unit_it_waits_on() -> None:
    ordered = resolve_staging_order(
        [step("fire-1", "fire-dispatch", after="police-1"), step("police-1", "police-dispatch")]
    )
    assert [s.unit_id for s in ordered] == ["police-1", "fire-1"]


def test_a_three_deep_staging_chain_orders_correctly() -> None:
    ordered = resolve_staging_order(
        [
            step("ambulance-1", "ems-dispatch", after="fire-1"),
            step("fire-1", "fire-dispatch", after="police-1"),
            step("police-1", "police-dispatch"),
        ]
    )
    assert [s.unit_id for s in ordered] == ["police-1", "fire-1", "ambulance-1"]


def test_staging_behind_an_unknown_unit_is_rejected() -> None:
    with pytest.raises(StagingViolation):
        resolve_staging_order([step("fire-1", "fire-dispatch", after="nonexistent")])


def test_a_circular_staging_dependency_is_rejected() -> None:
    with pytest.raises(StagingViolation):
        resolve_staging_order([step("a", "x", after="b"), step("b", "x", after="a")])
