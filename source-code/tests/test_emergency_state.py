"""P07.01: the call/assignment state machines and the CAD replay planner, on
hand-built records. Real-Postgres persistence and the full simulated dataset
are proven by verify_emergency.py (docs/evidence/p07_01_emergency.json)."""

from backend.emergency.cad_avl_adapter import plan_call_steps, plan_replay
from backend.repositories.emergency import ASSIGNMENT_TRANSITIONS, CALL_TRANSITIONS

CALL = {"call_id": "c1", "call_type": "ambulance", "reported_at": "2026-09-18T09:00:00.000Z"}
ASSIGNMENT = {
    "assignment_id": "a1",
    "call_id": "c1",
    "unit_id": "ambulance-1",
    "assigned_at": "2026-09-18T09:01:00.000Z",
    "acknowledged_at": "2026-09-18T09:01:30.000Z",
    "arrived_at": "2026-09-18T09:04:00.000Z",
    "cleared_at": "2026-09-18T09:10:00.000Z",
}


def test_call_state_machine_follows_the_contract_enum() -> None:
    assert CALL_TRANSITIONS["received"] == {"dispatched", "cancelled"}
    assert CALL_TRANSITIONS["on_scene"] == {"cleared"}
    assert CALL_TRANSITIONS["cleared"] == set()  # terminal


def test_assignment_state_machine_allows_unavailable_from_any_active_state() -> None:
    for status in ("assigned", "acknowledged", "en_route", "staged", "on_scene"):
        assert "unavailable" in ASSIGNMENT_TRANSITIONS[status]
    assert ASSIGNMENT_TRANSITIONS["clear"] == set()


def test_call_with_no_assignment_only_has_a_received_step() -> None:
    (step,) = plan_call_steps(CALL, [])
    assert step.to_status == "received"


def test_call_steps_derive_from_its_assignments_timestamps_in_order() -> None:
    steps = plan_call_steps(CALL, [ASSIGNMENT])
    assert [s.to_status for s in steps] == [
        "received",
        "dispatched",
        "unit_assigned",
        "en_route",
        "on_scene",
        "cleared",
    ]
    times = [s.at for s in steps]
    assert times == sorted(times)


def test_call_does_not_clear_until_every_assignment_has() -> None:
    still_open = dict(ASSIGNMENT, cleared_at=None)
    steps = plan_call_steps(CALL, [still_open])
    assert "cleared" not in [s.to_status for s in steps]


def test_full_replay_plan_interleaves_call_and_assignment_steps_in_time_order() -> None:
    steps = plan_replay([CALL], [ASSIGNMENT])
    assert [(s.entity, s.to_status) for s in steps][:4] == [
        ("call", "received"),
        ("call", "dispatched"),
        ("call", "unit_assigned"),
        ("assignment", "assigned"),
    ]
    times = [s.at for s in steps]
    assert times == sorted(times)
    assert {(s.entity, s.to_status) for s in steps} >= {
        ("call", "received"),
        ("call", "cleared"),
        ("assignment", "assigned"),
        ("assignment", "clear"),
    }


def test_two_calls_interleave_by_real_time_not_by_call_order() -> None:
    early = {"call_id": "c2", "call_type": "fire", "reported_at": "2026-09-18T08:59:00.000Z"}
    steps = plan_replay([CALL, early], [ASSIGNMENT])
    assert steps[0].entity_id == "c2"  # the earlier call, even though it is listed second
