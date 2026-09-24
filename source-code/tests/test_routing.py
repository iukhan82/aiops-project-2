"""P07.02: the router on a small hand-built graph shaped like the real
network (a loop with a shortcut). Real-network routing and the live-state
wiring (incidents + KPIs) are proven by backend/routing/verify_routing.py
(docs/evidence/p07_02_routing.json)."""

from backend.analytics.topology import Segment
from backend.routing.graph import EdgeState, RoadGraph
from backend.routing.router import closed_segments_on_route, fastest_safe_routes

# A -> B -> C -> D (the "long way") and A -> S -> D (a shortcut), plus the reverse
# of the long way so a closure has somewhere to detour to.
SEGS = [
    Segment("a-b", "A", "B", None, "cross", None, 300.0, 15.0),
    Segment("b-c", "B", "C", None, "cross", None, 300.0, 15.0),
    Segment("c-d", "C", "D", None, "cross", None, 300.0, 15.0),
    Segment("a-s", "A", "S", None, "cross", None, 200.0, 15.0),
    Segment("s-d", "S", "D", None, "cross", None, 200.0, 15.0),
    Segment("b-s", "B", "S", None, "cross", None, 150.0, 15.0),
]
GEOM = "test.1"


def graph() -> RoadGraph:
    return RoadGraph(SEGS)


def test_fastest_route_is_the_shortest_free_flow_path() -> None:
    (route,) = fastest_safe_routes(graph(), "A", "D", {}, GEOM, max_alternatives=1)
    assert route.edges == ("a-s", "s-d") and route.selected
    assert abs(route.distance_m - 400.0) < 1e-6


def test_no_route_between_disconnected_nodes_returns_nothing() -> None:
    assert fastest_safe_routes(graph(), "D", "A", {}, GEOM) == []


def test_same_origin_and_destination_returns_nothing() -> None:
    assert fastest_safe_routes(graph(), "A", "A", {}, GEOM) == []


def test_a_closed_segment_is_never_used_even_if_it_is_the_fastest() -> None:
    state = {"a-s": EdgeState(closed=True, closed_reason="collision on a-s")}
    (route,) = fastest_safe_routes(graph(), "A", "D", state, GEOM, max_alternatives=1)
    assert "a-s" not in route.edges
    assert route.edges == ("a-b", "b-s", "s-d")


def test_a_hazard_segment_is_penalized_but_still_passable_and_labelled() -> None:
    state = {"a-s": EdgeState(hazard=True, hazard_reason="congestion on a-s")}
    routes = fastest_safe_routes(graph(), "A", "D", state, GEOM, max_alternatives=2)
    fastest = routes[0]
    assert (
        "a-s" not in fastest.edges
    )  # the network reroutes around a mild hazard when a comparable path exists
    direct_state = {"a-s": EdgeState(hazard=True, hazard_reason="congestion on a-s")}
    only_direct = RoadGraph([SEGS[3], SEGS[4]])  # A-S-D only: no detour exists
    (forced,) = fastest_safe_routes(only_direct, "A", "D", direct_state, GEOM, max_alternatives=1)
    assert forced.edges == ("a-s", "s-d") and any(
        "through_hazard" in c for c in forced.constraints_applied
    )


def test_eta_uses_real_travel_time_never_the_inflated_search_cost() -> None:
    state = {"a-s": EdgeState(hazard=True, hazard_reason="x", travel_time_s=20.0)}
    only_direct = RoadGraph([SEGS[3], SEGS[4]])
    (route,) = fastest_safe_routes(only_direct, "A", "D", state, GEOM, max_alternatives=1)
    assert abs(route.eta_seconds - (20.0 + 200.0 / 15.0)) < 1e-6  # not 4x-penalized


def test_uncertainty_uses_measured_buffer_index_when_available_else_the_default_margin() -> None:
    measured = {"a-s": EdgeState(travel_time_s=100.0, buffer_index=0.3)}
    only_direct = RoadGraph([SEGS[3], SEGS[4]])
    (route,) = fastest_safe_routes(only_direct, "A", "D", measured, GEOM, max_alternatives=1)
    expected = 100.0 * 0.3 + (200.0 / 15.0) * 0.15
    assert abs(route.eta_uncertainty_seconds - expected) < 1e-6


def test_alternatives_are_genuinely_different_routes_not_the_same_path_twice() -> None:
    routes = fastest_safe_routes(graph(), "A", "D", {}, GEOM, max_alternatives=3)
    assert len(routes) >= 2
    edge_sets = [set(r.edges) for r in routes]
    assert edge_sets[0] != edge_sets[1]
    assert sum(r.selected for r in routes) == 1 and routes[0].selected


def test_vehicle_class_is_recorded_as_a_constraint() -> None:
    (route,) = fastest_safe_routes(
        graph(), "A", "D", {}, GEOM, max_alternatives=1, vehicle_class="ambulance"
    )
    assert "vehicle_class:ambulance" in route.constraints_applied


def test_closed_segments_on_route_reports_only_segments_that_are_actually_closed() -> None:
    (route,) = fastest_safe_routes(graph(), "A", "D", {}, GEOM, max_alternatives=1)
    assert closed_segments_on_route(route, {}) == []
    now_closed = {route.edges[0]: EdgeState(closed=True, closed_reason="x")}
    assert closed_segments_on_route(route, now_closed) == [route.edges[0]]


def test_route_matches_the_emergency_unit_assignment_contract_shape() -> None:
    (route,) = fastest_safe_routes(graph(), "A", "D", {}, GEOM, max_alternatives=1)
    record = route.as_record()
    assert set(record) == {
        "route_id",
        "geometry_version",
        "distance_m",
        "eta_seconds",
        "eta_uncertainty_seconds",
        "constraints_applied",
        "selected",
    }
    assert record["selected"] is True and record["distance_m"] > 0 and record["eta_seconds"] > 0
