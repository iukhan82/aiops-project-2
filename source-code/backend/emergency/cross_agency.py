"""P07.03: cross-agency staging and handover for a multi-unit call.

P07.01's two state machines are generic and per-entity: they know a call's
own legal transitions and an assignment's own legal transitions, nothing
about how *one agency's* progress gates *another's*. That gating is a
dispatch policy, not a state-machine invariant, so it lives here as a small
layer on top of the repository rather than inside it: a scene is not safe
for fire until police has secured the perimeter, and not safe for EMS until
fire confirms it - real staged response, not simultaneous unstaged arrival.

`StagingStep.after` names the *unit_id* (not agency) it waits on, because two
units from the same agency can still have a real order (a second ambulance
staged behind the first). No patient/medical field exists anywhere in this
module, matching the platform-wide exclusion.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.repositories import emergency as repo  # noqa: E402

ACTOR = "cross-agency-dispatch"


class StagingViolation(Exception):
    """Raised when a unit is asked to proceed on-scene before the unit(s) it stages behind have arrived."""


@dataclass(frozen=True)
class StagingStep:
    unit_id: str
    agency: str
    capability: list[str]
    route: dict
    after: str | None = None  # unit_id this one stages behind, or None for the first on scene


@dataclass(frozen=True)
class HandoverStep:
    from_unit: str
    to_unit: str
    from_agency: str
    to_agency: str
    note: str


def resolve_staging_order(steps: list[StagingStep]) -> list[StagingStep]:
    """Topological order by `after`: a unit staged behind another is ordered after it. Pure function -
    no I/O - so the dependency logic is testable without a database. Raises StagingViolation for a
    circular or dangling `after` reference."""
    known = {s.unit_id for s in steps}
    for s in steps:
        if s.after is not None and s.after not in known:
            raise StagingViolation(f"{s.unit_id} stages behind unknown unit {s.after!r}")
    ordered: list[StagingStep] = []
    placed: set[str] = set()
    pending = list(steps)
    while pending:
        progressed = False
        for step in list(pending):
            if step.after is None or step.after in placed:
                ordered.append(step)
                placed.add(step.unit_id)
                pending.remove(step)
                progressed = True
        if not progressed:
            raise StagingViolation(
                f"circular `after` reference among {[s.unit_id for s in pending]}"
            )
    return ordered


def plan_assignments(
    conn: psycopg.Connection, call_id: str, steps: list[StagingStep], truth_label: str, at: datetime
) -> dict[str, str]:
    """Creates one `assigned` assignment per step, in dependency order (a unit is created only after
    the unit it stages behind exists), and returns unit_id -> assignment_id."""
    by_unit: dict[str, str] = {}
    t = at
    for step in resolve_staging_order(steps):
        by_unit[step.unit_id] = repo.create_assignment(
            conn,
            call_id,
            step.unit_id,
            step.agency,
            step.capability,
            [step.route],
            truth_label,
            ACTOR,
            at=t,
        )
        t += timedelta(seconds=5)
    return by_unit


def advance_to_scene(
    conn: psycopg.Connection,
    assignment_id: str,
    unit_id: str,
    prerequisite_unit: str | None,
    assignment_arrived: dict[str, bool],
    at: datetime,
) -> None:
    """Runs one unit through acknowledged -> en_route -> (staged if it has a prerequisite not yet on scene) -> on_scene.
    Raises StagingViolation if asked to go on_scene while its prerequisite has not yet arrived - the check this
    module exists for; the repository's own state machine has no concept of "another unit's" status."""
    repo.transition_assignment(
        conn, assignment_id, "acknowledged", ACTOR, f"{unit_id} acknowledged", at=at
    )
    repo.transition_assignment(
        conn, assignment_id, "en_route", ACTOR, f"{unit_id} en route", at=at + timedelta(seconds=10)
    )
    if prerequisite_unit is not None and not assignment_arrived.get(prerequisite_unit, False):
        repo.transition_assignment(
            conn,
            assignment_id,
            "staged",
            ACTOR,
            f"{unit_id} staged behind {prerequisite_unit}",
            at=at + timedelta(seconds=20),
        )
        raise StagingViolation(
            f"{unit_id} cannot proceed on-scene: {prerequisite_unit} has not yet arrived"
        )
    repo.transition_assignment(
        conn, assignment_id, "on_scene", ACTOR, f"{unit_id} on scene", at=at + timedelta(seconds=30)
    )
    assignment_arrived[unit_id] = True


def release_staged(
    conn: psycopg.Connection,
    assignment_id: str,
    unit_id: str,
    at: datetime,
    assignment_arrived: dict[str, bool],
) -> None:
    """A unit already `staged` proceeds on_scene once its prerequisite has cleared this module's check."""
    repo.transition_assignment(
        conn,
        assignment_id,
        "on_scene",
        ACTOR,
        f"{unit_id} released from staging, now on scene",
        at=at,
    )
    assignment_arrived[unit_id] = True


def record_handover(
    conn: psycopg.Connection, assignment_id: str, step: HandoverStep, at: datetime
) -> None:
    """Attaches a handover to the *departing* unit's assignment record: `from_agency` had the scene, `to_agency`
    (represented by `to_unit`) takes responsibility for it, acknowledged by name - never a status change on its
    own, since a unit can hand off responsibility before or after it physically clears."""
    repo.transition_assignment(
        conn,
        assignment_id,
        "clear",
        ACTOR,
        step.note,
        at=at,
        handover={
            "from_agency": step.from_agency,
            "to_agency": step.to_agency,
            "handover_at": at.isoformat(),
            "acknowledged_by": step.to_unit,
        },
    )
