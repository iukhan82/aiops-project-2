"""P07.08: the corridor-id selection for a route that starts on a cross-street
edge (pure logic, no database). Corridor derivation and session lifecycle are
already covered by tests/test_preemption.py (the same functions). Real TraCI
actuation and the balanced benefit/harm measurement are proven by
backend/control/verify_transit_priority.py
(docs/evidence/p07_08_transit_priority.json)."""

from backend.analytics.topology import Segment
from backend.control.preemption import pick_corridor_id

SEGS = {
    "int-a1_int-b1": Segment("int-a1_int-b1", "int-a1", "int-b1", None, "cross", None, 300.0, 12.0),
    "int-b1_int-b2": Segment(
        "int-b1_int-b2", "int-b1", "int-b2", "corridor-b", "east", 1, 300.0, 15.0
    ),
    "int-b2_int-b3": Segment(
        "int-b2_int-b3", "int-b2", "int-b3", "corridor-b", "east", 2, 300.0, 15.0
    ),
}


def test_picks_the_first_route_edges_real_corridor_skipping_a_leading_cross_street() -> None:
    route = ("int-a1_int-b1", "int-b1_int-b2", "int-b2_int-b3")
    assert pick_corridor_id(route, SEGS, fallback="int-b1") == "corridor-b"


def test_falls_back_when_no_edge_on_the_route_has_a_corridor() -> None:
    route = ("int-a1_int-b1",)
    assert pick_corridor_id(route, SEGS, fallback="int-b1") == "int-b1"


def test_a_route_that_starts_on_the_corridor_needs_no_fallback() -> None:
    route = ("int-b1_int-b2", "int-b2_int-b3")
    assert pick_corridor_id(route, SEGS, fallback="unused") == "corridor-b"
