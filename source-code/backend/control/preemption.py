"""P07.07 / P07.10: emergency green-corridor pre-emption planning (pure logic;
the real TraCI actuation is `simulator/control_adapters/phase_control.py`,
run inside the pinned SUMO container like P07.06's other adapters).

**What is planned here:** which controlled intersections a route passes
through and the entry/exit edge at each (`corridor_from_route`), and the
session bookkeeping for one vehicle's corridor request (`PreemptionSession`).

**What is deliberately not decided here:** which signal phase serves the
vehicle. That comes from the simulator's own controlled-link table for the
vehicle's actual movement (entry edge -> exit edge), because a fixed
"corridor phase" cannot serve a vehicle that turns - the first version of this
module assumed phase 0 and P07.10's prototype measured pre-emption making
turning routes 12-20 s *slower* than doing nothing.

**Safety is measured, not assumed.** Advancing a program only ever shortens an
actuated service phase down to its own `minDur`; every fixed yellow, all-red
and pedestrian phase runs its designed duration. Whether that held is checked
by the independent runtime monitor (`simulator/control_adapters/
signal_safety.py`) on the raw signal state at every simulation step - the first
version of this mechanism shortened yellows to one step and passed a
phase-*order* check while failing that timing check.

**Session state** tracks exactly one intersection "active" (held green) at a
time, following the vehicle's real position along its route - pre-emption
never forces every downstream light green simultaneously, only the one the
vehicle is actually approaching. `abort` at any point returns every touched
intersection to its **original program** (`setProgram`, not a remembered
phase/timer) - the safe fallback is "exactly what was there before," not a
best-effort reconstruction.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.topology import Segment  # noqa: E402


@dataclass(frozen=True)
class CorridorStep:
    tl_id: str
    entry_edge: str
    exit_edge: str


def corridor_from_route(
    edges: tuple[str, ...], segments: dict[str, Segment], controlled_tls: set[str]
) -> list[CorridorStep]:
    """Every controlled intersection the route actually passes through, entry/exit edge on each -
    derived from the route's own edge sequence and the real segment topology (a junction is between
    edge[i] and edge[i+1] exactly when edge[i]'s to_node is edge[i+1]'s from_node), not a separately
    maintained map that could drift from what the vehicle is really doing."""
    steps = []
    for entry, exit_ in zip(edges, edges[1:], strict=False):
        entry_seg, exit_seg = segments.get(entry), segments.get(exit_)
        if entry_seg is None or exit_seg is None or entry_seg.to_node != exit_seg.from_node:
            continue
        junction = entry_seg.to_node
        if junction in controlled_tls:
            steps.append(CorridorStep(junction, entry, exit_))
    return steps


def pick_corridor_id(
    route_edges: tuple[str, ...], segments: dict[str, Segment], fallback: str
) -> str:
    """The route's first edge may be a cross-street connector with no corridor_id (a vehicle can enter via a
    cross edge before joining the corridor it actually needs priority on) - use the first route edge that has
    one, since that is the corridor whose live KPI freshness genuinely governs this request; `fallback`
    (normally the first corridor step's own intersection id) covers the all-cross-street edge case."""
    return next((segments[e].corridor_id for e in route_edges if segments[e].corridor_id), fallback)


@dataclass
class PreemptionSession:
    """One emergency vehicle's corridor request. `advance()` is called as the vehicle reaches each
    intersection in turn; `abort()` can be called at any point and always restores every touched
    intersection, active or already-cleared-forward, to its original program."""

    call_id: str
    steps: list[CorridorStep]
    original_programs: dict[str, str] = field(default_factory=dict)
    touched: list[str] = field(default_factory=list)
    active_index: int | None = None
    status: str = "pending"  # pending | active | completed | aborted

    def next_step(self) -> CorridorStep | None:
        i = 0 if self.active_index is None else self.active_index + 1
        return self.steps[i] if i < len(self.steps) else None

    def begin(self, tl_id: str, program_id: str) -> None:
        if tl_id not in self.original_programs:
            self.original_programs[tl_id] = program_id
            self.touched.append(tl_id)
        self.active_index = (self.active_index if self.active_index is not None else -1) + 1
        self.status = "active"

    def complete_current(self) -> str:
        """The intersection the vehicle just cleared is left on its normal program going forward -
        pre-emption releases each light as soon as it is no longer needed, not only at the end."""
        tl_id = self.steps[self.active_index].tl_id
        self.status = "active" if self.next_step() is not None else "completed"
        return tl_id

    def restore_list(self) -> list[tuple[str, str]]:
        """Every touched intersection with its real original program id - what `abort()` (or normal
        completion) must set each one back to."""
        return [(tl_id, self.original_programs[tl_id]) for tl_id in self.touched]

    def abort(self) -> list[tuple[str, str]]:
        self.status = "aborted"
        return self.restore_list()
