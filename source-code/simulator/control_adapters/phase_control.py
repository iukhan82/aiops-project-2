"""P07.10: clearance-preserving, movement-aware phase control for pre-emption
and transit priority (real TraCI; runs inside the SUMO container).

Two defects in the first version of this mechanism (P07.07/P07.08) were found
by P07.10's independent signal-safety monitor and fixed here:

1. **It drove every intersection to phase 0, whatever the vehicle's movement.**
   Phase 0 is the corridor-through phase in this network's programs, so a
   vehicle that *turns* (onto a cross street, or off one) was held at a red the
   pre-emption itself kept in place - the prototype measured pre-emption
   making such routes 12-20 s slower than doing nothing. The target is now the
   phase that gives the vehicle's actual movement (entry edge -> exit edge) its
   protected green, found from TraCI's own controlled-link table.
2. **It shortened every phase on the way to the target to zero, including the
   fixed yellow and all-red/pedestrian-clearance phases.** The phase *order*
   was respected, but a 3 s yellow became one simulation step. Only an
   actuated service phase (`minDur < maxDur`) is shortened now, and only down
   to its own `minDur`; every fixed phase (yellow, all-red, pedestrian green,
   green tail) runs its full designed duration.

`MovementPriority` is a per-step controller (`tick` once per simulation step)
rather than a blocking loop, so the caller's own per-step work - the safety
monitor, traffic sampling, arrival detection - keeps running while the
intersection is being advanced.

Neither the old nor the new version is trusted on its own word: the caller
runs `signal_safety.SignalSafetyMonitor` on the raw signal state of every
controlled intersection at every step.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "/usr/share/sumo/tools")
import traci  # noqa: E402

HOLD_DURATION_S = 30.0
MAX_WAIT_PER_PHASE_S = 90.0


class SafetyViolation(Exception):
    pass


def active_logic(tl_id: str):  # noqa: ANN201 - a traci Logic object
    program = traci.trafficlight.getProgram(tl_id)
    return next(
        logic
        for logic in traci.trafficlight.getAllProgramLogics(tl_id)
        if logic.programID == program
    )


def movement_links(tl_id: str, entry_edge: str, exit_edge: str) -> list[int]:
    """Signal link indices that carry entry_edge -> exit_edge, from TraCI's own controlled-link table."""
    links = []
    for index, group in enumerate(traci.trafficlight.getControlledLinks(tl_id)):
        if any(
            in_lane.rsplit("_", 1)[0] == entry_edge and out_lane.rsplit("_", 1)[0] == exit_edge
            for in_lane, out_lane, _via in group
        ):
            links.append(index)
    return links


def is_service_phase(phase) -> bool:  # noqa: ANN001
    return phase.minDur < phase.maxDur and "y" not in phase.state


def service_phase(tl_id: str, links: list[int]) -> int:
    """The phase that gives this movement the most protected green (then the most permissive green); an
    actuated service phase is preferred over a fixed green tail, then the earliest phase."""
    best, best_score = None, None
    for index, phase in enumerate(active_logic(tl_id).phases):
        protected = sum(1 for link in links if phase.state[link] == "G")
        permissive = sum(1 for link in links if phase.state[link] == "g")
        if protected + permissive == 0:
            continue
        score = (protected, permissive, is_service_phase(phase), -index)
        if best_score is None or score > best_score:
            best, best_score = index, score
    if best is None:
        raise SafetyViolation(f"no phase of {tl_id} gives green to links {links}")
    return best


class MovementPriority:
    """Gives one intersection's green to one vehicle movement, then lets go."""

    def __init__(self, tl_id: str, entry_edge: str, exit_edge: str, now: float):
        self.tl_id = tl_id
        self.links = movement_links(tl_id, entry_edge, exit_edge)
        if not self.links:
            raise SafetyViolation(f"{tl_id} has no controlled link for {entry_edge} -> {exit_edge}")
        self.target = service_phase(tl_id, self.links)
        self.original_program = traci.trafficlight.getProgram(tl_id)
        self.transitions: list[tuple[int, int]] = []
        self.holding = False
        self._last_phase = traci.trafficlight.getPhase(tl_id)
        self._deadline = now + len(active_logic(tl_id).phases) * MAX_WAIT_PER_PHASE_S

    def release(self) -> None:
        """The vehicle has passed: end the held service phase as soon as its own minimum green allows, so the
        corridor does not keep a green nobody needs. Fixed phases are never touched."""
        if not self.holding:
            return
        phase = active_logic(self.tl_id).phases[traci.trafficlight.getPhase(self.tl_id)]
        if is_service_phase(phase):
            spent = traci.trafficlight.getSpentDuration(self.tl_id)
            traci.trafficlight.setPhaseDuration(self.tl_id, max(0.0, phase.minDur - spent))

    def tick(self, now: float) -> None:
        tl_id = self.tl_id
        current = traci.trafficlight.getPhase(tl_id)
        if current != self._last_phase:
            self.transitions.append((self._last_phase, current))
            self._last_phase = current
        if self.holding:
            return
        phase = active_logic(tl_id).phases[current]
        if current == self.target or all(phase.state[link] == "G" for link in self.links):
            if is_service_phase(phase):
                traci.trafficlight.setPhaseDuration(tl_id, HOLD_DURATION_S)
            self.holding = True
            return
        if now > self._deadline:
            raise SafetyViolation(
                f"{tl_id} did not reach a phase serving {self.links} before the deadline"
            )
        if is_service_phase(phase):
            spent = traci.trafficlight.getSpentDuration(tl_id)
            traci.trafficlight.setPhaseDuration(tl_id, max(0.0, phase.minDur - spent))

    def restore(self) -> None:
        if traci.trafficlight.getProgram(self.tl_id) != self.original_program:
            traci.trafficlight.setProgram(self.tl_id, self.original_program)
