"""P07.07: pure pre-emption planning logic (corridor derivation, session
lifecycle), on a hand-built grid. Real TraCI actuation (movement-aware phase
selection, clearance timing measured by the independent signal-safety monitor,
restoration to the exact original program) is proven by
backend/control/verify_preemption.py and P07.10's verify_scenarios.py."""

from backend.analytics.topology import Segment
from backend.control.preemption import CorridorStep, PreemptionSession, corridor_from_route

SEGS = {
    "int-a1_int-a2": Segment(
        "int-a1_int-a2", "int-a1", "int-a2", "corridor-a", "east", 1, 300.0, 15.0
    ),
    "int-a2_int-a3": Segment(
        "int-a2_int-a3", "int-a2", "int-a3", "corridor-a", "east", 2, 300.0, 15.0
    ),
    "int-a3_int-a4": Segment(
        "int-a3_int-a4", "int-a3", "int-a4", "corridor-a", "east", 3, 300.0, 15.0
    ),
    "int-a4_int-b4": Segment("int-a4_int-b4", "int-a4", "int-b4", None, "cross", None, 300.0, 12.0),
}
CONTROLLED = {"int-a2", "int-a3", "int-a4"}  # int-a1 deliberately not a controlled light


def test_corridor_derives_a_step_per_controlled_junction_the_route_passes_through() -> None:
    route = ("int-a1_int-a2", "int-a2_int-a3", "int-a3_int-a4", "int-a4_int-b4")
    steps = corridor_from_route(route, SEGS, CONTROLLED)
    assert [s.tl_id for s in steps] == ["int-a2", "int-a3", "int-a4"]
    assert steps[0].entry_edge == "int-a1_int-a2" and steps[0].exit_edge == "int-a2_int-a3"


def test_an_uncontrolled_junction_produces_no_step() -> None:
    route = ("int-a1_int-a2",)
    assert (
        corridor_from_route(route, SEGS, CONTROLLED) == []
    )  # no second edge, nothing to derive a junction from


def test_a_disconnected_edge_pair_is_not_treated_as_a_junction() -> None:
    disjoint = ("int-a1_int-a2", "int-a3_int-a4")  # does not actually connect (skips int-a2_int-a3)
    assert corridor_from_route(disjoint, SEGS, CONTROLLED) == []


def test_session_begins_with_the_first_step_active() -> None:
    session = PreemptionSession(
        "call-1", [CorridorStep("int-a2", "e1", "e2"), CorridorStep("int-a3", "e2", "e3")]
    )
    session.begin("int-a2", "0")
    assert session.status == "active" and session.steps[session.active_index].tl_id == "int-a2"
    assert session.original_programs == {"int-a2": "0"}


def test_completing_the_last_step_marks_the_session_completed() -> None:
    session = PreemptionSession("call-1", [CorridorStep("int-a2", "e1", "e2")])
    session.begin("int-a2", "0")
    session.complete_current()
    assert session.status == "completed"


def test_completing_a_non_final_step_stays_active_for_the_next_one() -> None:
    session = PreemptionSession(
        "call-1", [CorridorStep("int-a2", "e1", "e2"), CorridorStep("int-a3", "e2", "e3")]
    )
    session.begin("int-a2", "0")
    session.complete_current()
    assert session.status == "active"
    session.begin("int-a3", "0")
    assert session.active_index == 1


def test_abort_restores_every_touched_intersection_to_its_own_original_program() -> None:
    session = PreemptionSession(
        "call-1", [CorridorStep("int-a2", "e1", "e2"), CorridorStep("int-a3", "e2", "e3")]
    )
    session.begin("int-a2", "prog-a2")
    session.complete_current()
    session.begin("int-a3", "prog-a3")
    restored = session.abort()
    assert (
        set(restored) == {("int-a2", "prog-a2"), ("int-a3", "prog-a3")}
        and session.status == "aborted"
    )


def test_begin_is_idempotent_about_the_original_program_it_records() -> None:
    session = PreemptionSession("call-1", [CorridorStep("int-a2", "e1", "e2")])
    session.begin("int-a2", "prog-a2")
    session.begin("int-a2", "some-other-program")  # must never overwrite the real original
    assert session.original_programs["int-a2"] == "prog-a2"
