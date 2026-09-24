"""P08.07: what a dispatcher does through the UI, on top of the P07.01 state machines.

`backend/repositories/emergency.py` owns the legal transitions of a call and of an assignment separately (a call can have several
units). A dispatcher thinks of one thing moving forward, so this service keeps the call in step with its assignments the way the
CAD adapter's replay does: assigning a unit dispatches the call, a unit en route puts the call en route, a unit on scene puts it on
scene, and the call clears when every assignment has cleared. It never moves a call backwards.

Route alternatives come from the same router and live state (closures, hazards, measured travel time) the routing API uses, so a
route offered here honours the incidents on the network right now.
"""

from __future__ import annotations

import math
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.repositories import emergency as repo  # noqa: E402
from backend.routing.route_service import route as compute_routes  # noqa: E402

CALL_ORDER = ["received", "dispatched", "unit_assigned", "en_route", "on_scene", "cleared"]
CALL_TYPES = ("ambulance", "fire", "police")
# What each unit type is and can do, as recorded in the CAD export (P03.04) and the device registry.
UNIT_PROFILES = {
    "ambulance": ("ems-dispatch", ["als", "transport"]),
    "fire-engine": ("fire-dispatch", ["engine", "rescue"]),
    "police": ("police-dispatch", ["patrol", "traffic_control"]),
}
UNIT_FITS_CALL = {"ambulance": "ambulance", "fire": "fire-engine", "police": "police"}
# A position older than this is history, not where the unit is. The dispatcher then says where it is, rather than the system guessing.
MAX_POSITION_AGE_S = 600


class DispatchError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def unit_profile(unit_id: str) -> tuple[str, list[str]]:
    for prefix, (agency, capability) in UNIT_PROFILES.items():
        if unit_id.startswith(prefix):
            return agency, capability
    raise DispatchError("unknown_unit", f"{unit_id!r} is not a unit this system dispatches")


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371000 * math.asin(math.sqrt(a))


def nearest_intersection(
    conn: psycopg.Connection, latitude: float, longitude: float, geometry: str
) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT intersection_id, ST_Y(location::geometry), ST_X(location::geometry) FROM intersections WHERE geometry_version = %s",
            (geometry,),
        )
        rows = cur.fetchall()
    if not rows:
        raise DispatchError("no_network", "no intersections are loaded for this geometry version")
    return min(rows, key=lambda r: _haversine_m(latitude, longitude, r[1], r[2]))[0]


def unit_position(conn: psycopg.Connection, unit_id: str) -> tuple[float, float, datetime] | None:
    """Where the unit last reported itself, from its AVL telemetry."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ST_Y(location::geometry), ST_X(location::geometry), observation_time FROM observation_events "
            "WHERE device_id = %s AND event_type = 'emergency.unit_position.avl' ORDER BY observation_time DESC LIMIT 1",
            (f"avl-{unit_id}",),
        )
        row = cur.fetchone()
    return (row[0], row[1], row[2]) if row else None


def create_call(
    conn: psycopg.Connection,
    call_type: str,
    call_subtype: str,
    priority: str,
    source_reliability: str,
    geometry: str,
    actor: str,
    intersection_id: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> str:
    if call_type not in CALL_TYPES:
        raise DispatchError(
            "invalid_call_type", f"call type must be one of {', '.join(CALL_TYPES)}"
        )
    location: dict = {"coordinate_reference": "EPSG:4326"}
    if intersection_id:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ST_Y(location::geometry), ST_X(location::geometry) FROM intersections WHERE intersection_id = %s AND geometry_version = %s",
                (intersection_id, geometry),
            )
            row = cur.fetchone()
        if row is None:
            raise DispatchError("unknown_location", f"no intersection {intersection_id!r}")
        location.update(
            {"intersection_id": intersection_id, "latitude": row[0], "longitude": row[1]}
        )
    elif latitude is not None and longitude is not None:
        location.update({"latitude": latitude, "longitude": longitude})
    else:
        raise DispatchError(
            "location_required", "a call needs an intersection or a latitude and longitude"
        )
    return repo.create_call(
        conn,
        call_type,
        call_subtype,
        priority,
        location,
        geometry,
        source_reliability,
        "operator_entered",
        actor,
    )


def advance_call_to(
    conn: psycopg.Connection, call_id: str, target: str, actor: str, note: str
) -> None:
    """Step the call forward through every legal state up to `target`; never backwards, never past a terminal state."""
    call = repo.get_call(conn, call_id)
    if call is None:
        raise repo.NotFound(call_id)
    current = call["status"]
    if current not in CALL_ORDER:
        return
    for status in CALL_ORDER[CALL_ORDER.index(current) + 1 : CALL_ORDER.index(target) + 1]:
        repo.transition_call(conn, call_id, status, actor, note)


def routes_for(
    conn: psycopg.Connection,
    origin_node: str,
    destination_node: str,
    geometry: str,
    vehicle_class: str | None = None,
) -> list[dict]:
    if origin_node == destination_node:
        return [
            {
                "route_id": f"route-{uuid.uuid4()}",
                "geometry_version": geometry,
                "distance_m": 0.0,
                "eta_seconds": 0.0,
                "eta_uncertainty_seconds": 0.0,
                "constraints_applied": ["already_at_scene_junction"],
                "selected": True,
            }
        ]
    alternatives = compute_routes(
        conn, origin_node, destination_node, geometry, datetime.now(timezone.utc), vehicle_class, 3
    )
    if not alternatives:
        raise DispatchError(
            "no_route",
            f"no route from {origin_node} to {destination_node}: a person has to decide how this unit gets there",
        )
    return [a.as_record() for a in alternatives]


def assign_unit(
    conn: psycopg.Connection, call_id: str, unit_id: str, actor: str, origin_node: str | None = None
) -> str:
    call = repo.get_call(conn, call_id)
    if call is None:
        raise repo.NotFound(call_id)
    if call["status"] in ("cleared", "cancelled"):
        raise DispatchError(
            "call_closed", f"the call is {call['status']}; a unit cannot be assigned to it"
        )
    if UNIT_FITS_CALL[call["call_type"]] not in unit_id:
        raise DispatchError(
            "unit_does_not_fit", f"{unit_id} is not a unit that answers a {call['call_type']} call"
        )
    if any(
        a["unit_id"] == unit_id and a["status"] not in ("clear", "unavailable")
        for a in repo.assignments_for_call(conn, call_id)
    ):
        raise DispatchError("already_assigned", f"{unit_id} is already assigned to this call")
    agency, capability = unit_profile(unit_id)
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM devices WHERE device_id = %s", (f"avl-{unit_id}",))
        if cur.fetchone() is None:
            raise DispatchError("unknown_unit", f"{unit_id} is not a registered unit")
    geometry = call["geometry_version"]
    if origin_node is None:
        position = unit_position(conn, unit_id)
        if position is None:
            raise DispatchError(
                "unit_position_unknown",
                f"{unit_id} has not reported a position, so its route cannot be worked out. Say which junction it is at.",
            )
        age = (datetime.now(timezone.utc) - position[2]).total_seconds()
        if age > MAX_POSITION_AGE_S:
            raise DispatchError(
                "unit_position_stale",
                f"{unit_id} last reported its position {int(age // 60)} minutes ago, too long to route from. Say which junction it is at.",
            )
        origin_node = nearest_intersection(conn, position[0], position[1], geometry)
    destination = nearest_intersection(conn, call["latitude"], call["longitude"], geometry)
    alternatives = routes_for(conn, origin_node, destination, geometry)
    assignment_id = repo.create_assignment(
        conn, call_id, unit_id, agency, capability, alternatives, "operator_entered", actor
    )
    advance_call_to(conn, call_id, "unit_assigned", actor, f"{unit_id} assigned")
    return assignment_id


def advance_assignment(
    conn: psycopg.Connection, assignment_id: str, to_status: str, actor: str, note: str | None
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT call_id FROM emergency_unit_assignments WHERE assignment_id = %s",
            (assignment_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise repo.NotFound(assignment_id)
    call_id = str(row[0])
    repo.transition_assignment(conn, assignment_id, to_status, actor, note)
    if to_status == "en_route":
        advance_call_to(conn, call_id, "en_route", actor, "unit en route")
    elif to_status == "on_scene":
        advance_call_to(conn, call_id, "on_scene", actor, "unit on scene")
    elif to_status == "clear":
        assignments = repo.assignments_for_call(conn, call_id)
        if all(a["status"] in ("clear", "unavailable") for a in assignments):
            advance_call_to(conn, call_id, "cleared", actor, "all assigned units clear")


def select_route(conn: psycopg.Connection, assignment_id: str, route_id: str, actor: str) -> dict:
    """Make one alternative the selected one. It is a decision, so it is recorded in the assignment's history."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, route_alternatives FROM emergency_unit_assignments WHERE assignment_id = %s FOR UPDATE",
            (assignment_id,),
        )
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            raise repo.NotFound(assignment_id)
        status, alternatives = row
        if status in ("clear", "unavailable"):
            conn.rollback()
            raise DispatchError(
                "assignment_closed", f"the assignment is {status}; its route can no longer change"
            )
        if route_id not in {a["route_id"] for a in alternatives}:
            conn.rollback()
            raise DispatchError(
                "unknown_route", f"{route_id!r} is not one of this assignment's route alternatives"
            )
        updated = [{**a, "selected": a["route_id"] == route_id} for a in alternatives]
        cur.execute(
            "UPDATE emergency_unit_assignments SET route_alternatives = %s WHERE assignment_id = %s",
            (Jsonb(updated), assignment_id),
        )
        cur.execute(
            "INSERT INTO emergency_assignment_transitions (assignment_id, from_status, to_status, changed_by, note) VALUES (%s, %s, %s, %s, %s)",
            (assignment_id, status, status, actor, f"route {route_id} selected"),
        )
    conn.commit()
    return {"assignment_id": assignment_id, "selected_route_id": route_id}
