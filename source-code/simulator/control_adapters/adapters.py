"""P07.06: simulator adapters - the real TraCI actions behind an approved
command's `target.adapter`. Each `apply_*` function makes one real,
observable change against the live SUMO instance `run_action.py` connects
to, and returns what TraCI itself reports afterward - never a claimed
success the simulator was not asked to confirm.

`signal_controller_adapter` extends the *current* phase's remaining duration
(`traci.trafficlight.setPhaseDuration`, the standard TraCI technique for a
runtime priority/extension override on a real `actuated` program - every
intersection here already has one, P03.01), bounded by the caller's own
`max_signal_deviation_s` (never trusted blindly from the request; re-checked
here as the last line of defense before an actual signal changes).

`diversion_adapter` closes the segment's general-traffic lanes
(`traci.lane.setDisallowed`) - it does not merely suggest a route, it
actually removes the option from SUMO's own routing, so "traffic no longer
uses this segment" is a fact the simulator itself now enforces, not a hope.

`vms_adapter` has **no real SUMO actuation** and says so: a variable message
sign influences human drivers' voluntary choices, which this simulator does
not model. It is recorded as applied so the command completes honestly, with
that limitation stated in its own result, not hidden.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "/usr/share/sumo/tools")
import traci  # noqa: E402


class AdapterError(Exception):
    pass


GENERAL_LANE_SUFFIXES = (
    "_2",
    "_3",
)  # P03.01: lanes 0/1 are sidewalk/bicycle, 2/3 are general traffic


def apply_signal(tl_id: str, deviation_s: float, max_deviation_s: float) -> dict:
    if tl_id not in traci.trafficlight.getIDList():
        raise AdapterError(f"{tl_id!r} is not a real controlled traffic light in this simulation")
    deviation_s = max(0.0, min(deviation_s, max_deviation_s))
    now = traci.simulation.getTime()
    before_phase, before_switch = (
        traci.trafficlight.getPhase(tl_id),
        traci.trafficlight.getNextSwitch(tl_id),
    )
    remaining = max(0.0, before_switch - now)
    traci.trafficlight.setPhaseDuration(tl_id, remaining + deviation_s)
    after_switch = traci.trafficlight.getNextSwitch(tl_id)
    return {
        "tl_id": tl_id,
        "deviation_applied_s": round(after_switch - before_switch, 3),
        "phase": before_phase,
        "next_switch_before": before_switch,
        "next_switch_after": after_switch,
        "observed_at": now,
    }


def apply_diversion(edge_id: str) -> dict:
    lanes = [
        f"{edge_id}{suffix}"
        for suffix in GENERAL_LANE_SUFFIXES
        if f"{edge_id}{suffix}" in traci.lane.getIDList()
    ]
    if not lanes:
        raise AdapterError(f"{edge_id!r} has no general-traffic lanes in this simulation")
    for lane in lanes:
        traci.lane.setDisallowed(lane, ["passenger"])
    observed = {lane: traci.lane.getDisallowed(lane) for lane in lanes}
    return {
        "edge_id": edge_id,
        "lanes_closed": lanes,
        "observed_disallowed": observed,
        "observed_at": traci.simulation.getTime(),
        "vehicles_still_on_edge": traci.edge.getLastStepVehicleNumber(edge_id),
    }


def apply_vms(edge_id: str, message: str) -> dict:
    if edge_id not in traci.edge.getIDList():
        raise AdapterError(f"{edge_id!r} is not a real edge in this simulation")
    return {
        "edge_id": edge_id,
        "message": message,
        "simulator_actuation": False,
        "limitation": "no native SUMO VMS API; this simulator does not model voluntary driver response to advisory signage",
        "observed_at": traci.simulation.getTime(),
    }
