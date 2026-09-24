"""P03.04: deterministic (seed, run_id) emergency call/unit/assignment
generator. Pure Python except for the actual travel time, which comes from a
real SUMO trip (see build_and_verify.py) - this module only decides *what*
happens (calls, units, dispatch order, timestamps up to depart), not vehicle
physics.

Scope (documented limitation, see README.md):
- One station per agency; two units per agency (6 total).
- A unit's route is a single, real, SUMO-router-computed path (no fabricated
  route alternatives, no route-constraint modeling: vehicle-height/hazmat/
  bridge-weight constraints are not represented in this network yet).
- No cross-agency handover scenario here; that belongs to P03.05's
  multi-emergency scenario work.
- Deliberately patient-free: no field here ever carries patient identity or
  medical data (AGENTS.md); call_subtype is an operational category only.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

NAMESPACE = uuid.UUID("6f6f6f6f-0304-4a4a-8a8a-202609180004")

AGENCIES: dict[str, dict[str, object]] = {
    "fire": {
        "agency_scope": "fire-dispatch",
        "station_junction": "int-a1",
        "units": ["fire-engine-1", "fire-engine-2"],
        "capability": ["engine", "rescue"],
        "subtypes": ["structure_fire", "vehicle_fire", "hazmat"],
        "scene_duration_s": 600,
    },
    "ambulance": {
        "agency_scope": "ems-dispatch",
        "station_junction": "int-b1",
        "units": ["ambulance-1", "ambulance-2"],
        "capability": ["als", "transport"],
        "subtypes": ["medical_emergency", "traffic_collision"],
        "scene_duration_s": 300,
    },
    "police": {
        "agency_scope": "police-dispatch",
        "station_junction": "int-c1",
        "units": ["police-1", "police-2"],
        "capability": ["patrol", "traffic_control"],
        "scene_duration_s": 240,
        "subtypes": ["traffic_collision", "public_safety"],
    },
}

CALLS_PER_AGENCY = 3
DISPATCH_LATENCY_S = 8.0
ACK_LATENCY_S = 5.0
ETA_UNCERTAINTY_FRACTION = 0.12


@dataclass(frozen=True)
class Call:
    call_id: str
    call_type: str
    call_subtype: str
    priority: str
    junction_id: str
    reported_at_s: float
    source_reliability: str


@dataclass(frozen=True)
class Dispatch:
    call: Call
    assignment_id: str
    unit_id: str
    agency_scope: str
    capability: list[str]
    assigned_at_s: float
    acknowledged_at_s: float


def _event_uuid(run_id: str, kind: str, index: int) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{run_id}:{kind}:{index}"))


def generate_calls(seed: int, run_id: str, sim_end: int) -> list[Call]:
    rng = random.Random(f"{seed}:{run_id}:calls")
    junctions = [f"int-{letter}{n}" for letter in "abc" for n in range(1, 5)]
    priorities = ["low", "medium", "high", "critical"]
    calls: list[Call] = []
    index = 0
    for call_type, info in AGENCIES.items():
        for _ in range(CALLS_PER_AGENCY):
            reported_at_s = round(rng.uniform(0, sim_end * 0.55), 1)
            call = Call(
                call_id=_event_uuid(run_id, "call", index),
                call_type=call_type,
                call_subtype=rng.choice(info["subtypes"]),  # type: ignore[arg-type]
                priority=rng.choice(priorities),
                junction_id=rng.choice(junctions),
                reported_at_s=reported_at_s,
                source_reliability=rng.choice(
                    ["verified_dispatch", "unverified_report", "automated_detection"]
                ),
            )
            calls.append(call)
            index += 1
    calls.sort(key=lambda c: (c.reported_at_s, c.call_id))
    return calls


def dispatch_calls(calls: list[Call], run_id: str) -> list[Dispatch]:
    busy_until: dict[str, float] = {}
    for info in AGENCIES.values():
        for unit_id in info["units"]:  # type: ignore[union-attr]
            busy_until[unit_id] = 0.0

    dispatches: list[Dispatch] = []
    for index, call in enumerate(calls):
        info = AGENCIES[call.call_type]
        unit_ids: list[str] = info["units"]  # type: ignore[assignment]
        unit_id = min(unit_ids, key=lambda u: (busy_until[u], u))
        assigned_at_s = max(call.reported_at_s, busy_until[unit_id]) + DISPATCH_LATENCY_S
        acknowledged_at_s = assigned_at_s + ACK_LATENCY_S
        dispatches.append(
            Dispatch(
                call=call,
                assignment_id=_event_uuid(run_id, "assignment", index),
                unit_id=unit_id,
                agency_scope=info["agency_scope"],  # type: ignore[arg-type]
                capability=list(info["capability"]),  # type: ignore[arg-type]
                assigned_at_s=assigned_at_s,
                acknowledged_at_s=acknowledged_at_s,
            )
        )
        # Provisional busy-until; build_and_verify.py overwrites this once the
        # real SUMO travel time + scene duration are known, but dispatch order
        # for later calls must not depend on that not-yet-simulated value, so
        # a conservative fixed estimate is used only to break unit-choice ties
        # for calls still in this same generation pass.
        busy_until[unit_id] = acknowledged_at_s + 300.0
    return dispatches


def iso(anchor: datetime, offset_s: float) -> str:
    dt = anchor + timedelta(seconds=offset_s)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
