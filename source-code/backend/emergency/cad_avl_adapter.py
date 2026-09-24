"""P07.01: CAD/AVL adapter - turns P03.04's simulated CAD export (one final
record per call/assignment, as a legacy CAD feed often reports) into the
platform's own live state machine (backend/repositories/emergency.py),
replayed in the timeline's real chronological order, and pushes AVL position
telemetry through the real P05.05 ingestion path unchanged.

Two timestamps the CAD export does not distinguish are collapsed onto the
evidence that exists, and this is stated rather than hidden:
- `dispatched` and `unit_assigned` both land at the assignment's own
  `assigned_at` (the export has one instant for "a unit was assigned", not a
  separate "call was dispatched to CAD" moment);
- an assignment's `en_route` lands at its own `acknowledged_at` (the export
  has no separate "unit began moving" timestamp).

A call is `cleared` once every one of its assignments has cleared (multi-unit
calls exist in principle even though this dataset's calls each have one).
No `cancelled` call or `unavailable` assignment exists in P03.04's output
(every call resolves cleanly), so those two transitions are proven separately
with synthetic, explicitly labelled records in `verify_emergency.py`.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.repositories import emergency as repo  # noqa: E402

ADAPTER_ACTOR = "cad-avl-adapter"


def _parse(ts: str) -> datetime:
    from edge.timeutil import parse_ts

    return parse_ts(ts)


@dataclass
class _Step:
    at: datetime
    order: int  # tie-break: within one timestamp, calls step before their own assignments' matching step
    entity: str  # "call" | "assignment"
    entity_id: str
    to_status: str
    note: str
    call_id: str | None = None  # for assignment steps, to build the call afterwards if needed


def plan_call_steps(call: dict, assignments: list[dict]) -> list[_Step]:
    steps = [
        _Step(_parse(call["reported_at"]), 0, "call", call["call_id"], "received", "call received")
    ]
    if not assignments:
        return steps
    dispatch_at = min(_parse(a["assigned_at"]) for a in assignments)
    steps.append(
        _Step(dispatch_at, 1, "call", call["call_id"], "dispatched", "unit assignment decided")
    )
    steps.append(
        _Step(dispatch_at, 2, "call", call["call_id"], "unit_assigned", "unit(s) assigned")
    )
    if all("acknowledged_at" in a and a["acknowledged_at"] for a in assignments):
        en_route_at = min(_parse(a["acknowledged_at"]) for a in assignments)
        steps.append(
            _Step(en_route_at, 1, "call", call["call_id"], "en_route", "first unit en route")
        )
    if all("arrived_at" in a and a["arrived_at"] for a in assignments):
        on_scene_at = min(_parse(a["arrived_at"]) for a in assignments)
        steps.append(
            _Step(on_scene_at, 1, "call", call["call_id"], "on_scene", "first unit on scene")
        )
    if all("cleared_at" in a and a["cleared_at"] for a in assignments):
        cleared_at = max(_parse(a["cleared_at"]) for a in assignments)
        steps.append(
            _Step(cleared_at, 9, "call", call["call_id"], "cleared", "all assigned units clear")
        )
    return steps


def plan_assignment_steps(assignment: dict) -> list[_Step]:
    aid = assignment["assignment_id"]
    steps = [
        _Step(
            _parse(assignment["assigned_at"]),
            3,
            "assignment",
            aid,
            "assigned",
            f"unit {assignment['unit_id']} assigned",
            assignment["call_id"],
        )
    ]
    if assignment.get("acknowledged_at"):
        steps.append(
            _Step(
                _parse(assignment["acknowledged_at"]),
                0,
                "assignment",
                aid,
                "acknowledged",
                "unit acknowledged",
                assignment["call_id"],
            )
        )
        steps.append(
            _Step(
                _parse(assignment["acknowledged_at"]),
                2,
                "assignment",
                aid,
                "en_route",
                "unit en route (same instant as acknowledgement in this feed)",
                assignment["call_id"],
            )
        )
    if assignment.get("arrived_at"):
        steps.append(
            _Step(
                _parse(assignment["arrived_at"]),
                0,
                "assignment",
                aid,
                "on_scene",
                "unit on scene",
                assignment["call_id"],
            )
        )
    if assignment.get("cleared_at"):
        steps.append(
            _Step(
                _parse(assignment["cleared_at"]),
                8,
                "assignment",
                aid,
                "clear",
                "unit clear",
                assignment["call_id"],
            )
        )
    return steps


def plan_replay(calls: list[dict], assignments: list[dict]) -> list[_Step]:
    by_call: dict[str, list[dict]] = defaultdict(list)
    for a in assignments:
        by_call[a["call_id"]].append(a)
    steps: list[_Step] = []
    for call in calls:
        steps += plan_call_steps(call, by_call.get(call["call_id"], []))
    for a in assignments:
        steps += plan_assignment_steps(a)
    return sorted(steps, key=lambda s: (s.at, s.order, s.entity, s.entity_id))


@dataclass
class ReplayReport:
    calls_created: int = 0
    assignments_created: int = 0
    transitions: int = 0
    errors: list[str] = field(default_factory=list)


def replay(
    conn: psycopg.Connection, calls: list[dict], assignments: list[dict], actor: str = ADAPTER_ACTOR
) -> ReplayReport:
    """Walks the derived timeline in real chronological order, creating/transitioning
    through `backend/repositories/emergency.py` exactly as a live CAD feed would."""
    report = ReplayReport()
    known_calls: set[str] = set()
    known_assignments: set[str] = set()
    by_id = {c["call_id"]: c for c in calls}
    for step in plan_replay(calls, assignments):
        try:
            if step.entity == "call":
                if step.to_status == "received":
                    c = by_id[step.entity_id]
                    repo.create_call(
                        conn,
                        c["call_type"],
                        c["call_subtype"],
                        c["priority"],
                        c["location"],
                        c["geometry_version"],
                        c["source_reliability"],
                        c["truth_label"],
                        actor,
                        at=step.at,
                        call_id=c["call_id"],
                    )
                    known_calls.add(step.entity_id)
                    report.calls_created += 1
                else:
                    repo.transition_call(
                        conn, step.entity_id, step.to_status, actor, step.note, at=step.at
                    )
                    report.transitions += 1
            else:
                a = next(x for x in assignments if x["assignment_id"] == step.entity_id)
                if step.to_status == "assigned":
                    repo.create_assignment(
                        conn,
                        a["call_id"],
                        a["unit_id"],
                        a["agency"],
                        a["capability"],
                        a["route_alternatives"],
                        a["truth_label"],
                        actor,
                        at=step.at,
                        assignment_id=a["assignment_id"],
                    )
                    known_assignments.add(step.entity_id)
                    report.assignments_created += 1
                else:
                    repo.transition_assignment(
                        conn, step.entity_id, step.to_status, actor, step.note, at=step.at
                    )
                    report.transitions += 1
        except (repo.InvalidTransition, repo.NotFound) as exc:  # noqa: PERF203 - clarity over micro-speed in a replay loop
            report.errors.append(
                f"{step.entity}:{step.entity_id} -> {step.to_status} @ {step.at.isoformat()}: {exc}"
            )
        except psycopg.errors.UniqueViolation as exc:
            conn.rollback()
            report.errors.append(
                f"{step.entity}:{step.entity_id} -> {step.to_status} @ {step.at.isoformat()}: already exists ({exc.diag.message_primary})"
            )
    return report
