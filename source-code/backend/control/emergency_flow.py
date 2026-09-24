"""P07.10: one emergency response through the whole platform, end to end.

    call (P07.01) -> live-state-aware fastest-safe route and ETA (P07.02)
      -> assignment with route alternatives (P07.01) -> pre-emption command
      request (P07.07) -> policy approval (P07.05: role, four-eyes, SAFE-03
      fresh evidence) -> execution against the real simulator (P07.06/07)
      -> independent outcome verification (P07.09)

and, for every dispatch, a **paired same-seed baseline run** with no
pre-emption - so the platform's own benefit, its ETA error against what
actually happened, and its effect on general traffic are all measured, never
assumed. The unit's *actual* travel time is whatever it really experienced:
the pre-emption run when the command was approved and executed, the baseline
run when it was not (denied, expired, policy outage, adapter failure).

Every failure branch is fail-closed: a command that is not approved and
executed never touches a signal, and the unit simply drives on unassisted.

The emergency call and assignment are written after the simulation with a
timeline that ends now and is back-dated by the simulated travel time, so no
row is future-dated and the recorded timeline matches what the simulator
measured. Commands and outcomes keep real wall-clock times (they are
control-plane events).
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.kpi_service import load_segments  # noqa: E402
from backend.control.command_service import review_command  # noqa: E402
from backend.control.outcome_verification import Metric, verify_and_rollback  # noqa: E402
from backend.control.preemption_service import (  # noqa: E402
    PreemptionError,
    _run_corridor_scenario,
    _step_records,
    execute_preemption,
    request_preemption,
)  # noqa: E402
from backend.repositories import commands as cmd_repo  # noqa: E402
from backend.repositories import emergency as em_repo  # noqa: E402
from backend.roles import EXECUTOR, OUTCOME_VERIFIER  # noqa: E402
from backend.routing.route_service import route  # noqa: E402

REQUESTER, APPROVER, VERIFIER = "dispatcher:alice", "incident_commander:bob", OUTCOME_VERIFIER
HARM_WINDOW_S = 150
OUTCOME_SAFETY_REGRESSION_S = (
    5.0  # pre-emption slower than the paired baseline by more than this is `unsafe` (rolled back)
)
OUTCOME_EFFECTIVENESS_S = 1.0


@dataclass(frozen=True)
class Scenario:
    name: str
    call_type: str
    subtype: str
    priority: str
    unit_id: str
    agency: str
    capability: tuple[str, ...]
    origin: str
    destination: str
    ev_type: dict = field(default_factory=dict)


@dataclass
class DispatchResult:
    scenario: str
    status: str  # completed | no_route | did_not_complete
    tag: str
    call_id: str | None = None
    assignment_id: str | None = None
    command_id: str | None = None
    command_status: str | None = None
    route_edges: tuple[str, ...] = ()
    eta_s: float | None = None
    eta_uncertainty_s: float | None = None
    alternatives: int = 0
    baseline_s: float | None = None
    preempt_s: float | None = None
    actual_s: float | None = None
    preemption_applied: bool = False
    safety_observations: int = 0
    safety_violations: int = 0
    baseline_traffic: dict = field(default_factory=dict)
    preempt_traffic: dict = field(default_factory=dict)
    outcome_classification: str | None = None
    outcome_id: str | None = None
    wall_s: float = 0.0
    notes: list[str] = field(default_factory=list)


def publish_live_kpis(
    conn: psycopg.Connection, geometry: str, segments: dict, edge_travel_times: dict, at: datetime
) -> list[tuple[str, str, datetime]]:
    """Turns the simulator's measured per-edge travel times (the last 30 s before dispatch) into the corridor KPI
    rows the platform's own KPI pipeline (P06.01) would have produced, so the router's ETA reads real live
    congestion through `corridor_kpis` exactly as it would in operation. Returns the row keys for cleanup."""
    groups: dict[tuple[str, str], list] = {}
    for seg in segments.values():
        if seg.corridor_id is not None:
            groups.setdefault((seg.corridor_id, seg.direction), []).append(seg)
    keys = []
    with conn.cursor() as cur:
        for (corridor, direction), segs in sorted(groups.items()):
            free_flow = sum(s.length_m / s.free_flow_speed_m_s for s in segs)
            measured = sum(
                max(edge_travel_times.get(s.edge_id, 0.0), s.length_m / s.free_flow_speed_m_s)
                for s in segs
            )
            cur.execute(
                "INSERT INTO corridor_kpis (corridor_id, direction, window_start, window_seconds, geometry_version, kpis, quality, coverage, "
                "sample_count, segments_reporting, segments_expected) VALUES (%s, %s, %s, 30, %s, %s::jsonb, 'valid', 1.0, 30, %s, %s) ON CONFLICT DO NOTHING",
                (
                    corridor,
                    direction,
                    at,
                    geometry,
                    Jsonb({"travel_time_s": measured, "free_flow_travel_time_s": free_flow}),
                    len(segs),
                    len(segs),
                ),
            )
            keys.append((corridor, direction, at))
    conn.commit()
    return keys


def _cross_street_harm_edges(
    segments: dict, steps: list, route_edges: tuple[str, ...]
) -> list[str]:
    """Cross-street approaches to the pre-empted intersections: the traffic that loses green when the corridor is held."""
    on_route = set(route_edges)
    return sorted(
        {
            s.edge_id
            for s in segments.values()
            if s.corridor_id is None
            and s.edge_id not in on_route
            and s.to_node in {st.tl_id for st in steps}
        }
    )


def run_dispatch(
    conn: psycopg.Connection,
    scenario: Scenario,
    geometry: str,
    tag: str,
    seed: int,
    depart_s: float,
    *,
    background_rate_per_s: float = 0.6,
    closed_edges: list[str] | None = None,
    verify_outcome: bool = True,
    live_kpi: bool = True,
    kpi_keys: list | None = None,
) -> DispatchResult:
    """`live_kpi=True` runs a no-vehicle snapshot of the simulated world up to dispatch time and publishes what it
    measured as corridor KPIs before routing (the platform's operating mode); `False` leaves the router on posted
    free-flow speeds, and leaves the policy's SAFE-03 freshness gate with no live evidence."""
    started = time.perf_counter()
    result = DispatchResult(scenario.name, "completed", tag)
    now = datetime.now(timezone.utc)
    span = int(depart_s + 300)
    world = {
        "seed": seed,
        "emergency_vehicle_depart_s": depart_s,
        "background_vehicles": int(background_rate_per_s * span),
        "background_span_s": span,
        "ev_type": scenario.ev_type,
        "closed_edges": closed_edges or [],
    }
    segments = {s.edge_id: s for s in load_segments(conn, geometry)}
    if live_kpi:
        snapshot = _run_corridor_scenario(
            {
                **world,
                "mode": "snapshot",
                "route": [next(iter(segments))],
                "steps": [],
                "emergency_vehicle_id": f"ev-snap-{tag}",
                "snapshot_window_s": 30,
            }
        )
        if snapshot is None or not snapshot.get("edge_travel_times"):
            result.status = "did_not_complete"
            result.notes.append("the live-state snapshot produced no measurements")
            return result
        keys = publish_live_kpis(
            conn, geometry, segments, snapshot["edge_travel_times"], now - timedelta(seconds=30)
        )
        if kpi_keys is not None:
            kpi_keys.extend(keys)
        result.safety_observations += snapshot["signal_safety"]["observations"]
        result.safety_violations += snapshot["signal_safety"]["violation_count"]

    alternatives = route(
        conn, scenario.origin, scenario.destination, geometry, now, vehicle_class=scenario.call_type
    )
    result.alternatives = len(alternatives)
    if not alternatives:
        result.status = "no_route"
        result.notes.append(
            "no open route between origin and destination: dispatch needs a human decision, nothing is pre-empted"
        )
        result.call_id = em_repo.create_call(
            conn,
            scenario.call_type,
            scenario.subtype,
            scenario.priority,
            {"latitude": 31.52, "longitude": 74.36},
            geometry,
            "verified_dispatch",
            "simulated",
            REQUESTER,
            at=now,
        )
        em_repo.transition_call(
            conn,
            result.call_id,
            "dispatched",
            REQUESTER,
            "no route available: awaiting a human dispatch decision",
            at=now,
        )
        result.wall_s = time.perf_counter() - started
        return result
    best = alternatives[0]
    result.route_edges, result.eta_s, result.eta_uncertainty_s = (
        best.edges,
        best.eta_seconds,
        best.eta_uncertainty_seconds,
    )

    try:
        command_id, steps = request_preemption(
            conn, best.edges, geometry, REQUESTER, now, f"verify-p0710-{tag}"
        )
    except PreemptionError as exc:
        result.notes.append(f"no pre-emption request: {exc}")
        command_id, steps = None, []
    result.command_id = command_id

    sim_request = {
        **world,
        "route": list(best.edges),
        "steps": _step_records(steps),
        "harm_edges": _cross_street_harm_edges(segments, steps, best.edges),
        "harm_window_s": HARM_WINDOW_S,
    }

    # ---- the unit's paired baseline: same seed, same departure, no pre-emption ----
    base_start = datetime.now(timezone.utc)
    baseline = _run_corridor_scenario(
        {**sim_request, "mode": "baseline", "emergency_vehicle_id": f"ev-base-{tag}"}
    )
    base_end = datetime.now(timezone.utc)
    if baseline is None or not baseline["ev_completed"]:
        result.status = "did_not_complete"
        result.notes.append("the baseline run produced no arrival within the simulation horizon")
        result.safety_observations += baseline["signal_safety"]["observations"] if baseline else 0
        result.safety_violations += baseline["signal_safety"]["violation_count"] if baseline else 0
        result.wall_s = time.perf_counter() - started
        return result
    result.baseline_s, result.baseline_traffic = baseline["travel_time_s"], baseline["traffic"]
    result.safety_observations += baseline["signal_safety"]["observations"]
    result.safety_violations += baseline["signal_safety"]["violation_count"]

    # ---- approval, then execution only if approved ----
    preempt = None
    if command_id is not None:
        status = review_command(
            conn, command_id, APPROVER, "incident_commander", datetime.now(timezone.utc), geometry
        )
        result.command_status = status
        if status == "approved":
            pre_start = datetime.now(timezone.utc)
            preempt = execute_preemption(
                conn,
                command_id,
                steps,
                f"ev-{tag}",
                best.edges,
                EXECUTOR,
                extra_request=sim_request,
            )
            pre_end = datetime.now(timezone.utc)
            result.command_status = cmd_repo.get_command(conn, command_id)["status"]
            if preempt.get("signal_safety"):
                result.safety_observations += preempt["signal_safety"]["observations"]
                result.safety_violations += preempt["signal_safety"]["violation_count"]
            if result.command_status == "executed" and preempt.get("ev_completed"):
                result.preempt_s, result.preempt_traffic, result.preemption_applied = (
                    preempt["travel_time_s"],
                    preempt["traffic"],
                    True,
                )
                if verify_outcome:
                    outcome_id, verdict = verify_and_rollback(
                        conn,
                        command_id,
                        Metric("emergency_travel_time", baseline["travel_time_s"], "s"),
                        Metric("emergency_travel_time", preempt["travel_time_s"], "s"),
                        (base_start, base_end),
                        (pre_start, pre_end),
                        VERIFIER,
                        datetime.now(timezone.utc),
                        OUTCOME_SAFETY_REGRESSION_S,
                        OUTCOME_EFFECTIVENESS_S,
                        extra_detail={
                            "design": "paired same-seed counterfactual: pre = baseline run (no pre-emption), post = pre-emption run"
                        },
                    )
                    result.outcome_id, result.outcome_classification = (
                        outcome_id,
                        verdict.classification,
                    )
                    result.command_status = cmd_repo.get_command(conn, command_id)["status"]
            else:
                result.notes.append(
                    f"pre-emption did not complete: command {result.command_status}"
                )
        else:
            result.notes.append(f"pre-emption not applied: command {status}")
    result.actual_s = result.preempt_s if result.preemption_applied else result.baseline_s

    # ---- the call and assignment, on a timeline that ends now and is back-dated by the simulated travel ----
    end = datetime.now(timezone.utc)
    depart = end - timedelta(seconds=result.actual_s)
    call_id = em_repo.create_call(
        conn,
        scenario.call_type,
        scenario.subtype,
        scenario.priority,
        {"latitude": 31.52, "longitude": 74.36},
        geometry,
        "verified_dispatch",
        "simulated",
        REQUESTER,
        at=depart - timedelta(seconds=40),
    )
    em_repo.transition_call(
        conn, call_id, "dispatched", REQUESTER, at=depart - timedelta(seconds=35)
    )
    assignment_id = em_repo.create_assignment(
        conn,
        call_id,
        scenario.unit_id,
        scenario.agency,
        list(scenario.capability),
        [a.as_record() for a in alternatives],
        "simulated",
        REQUESTER,
        at=depart - timedelta(seconds=30),
    )
    em_repo.transition_call(
        conn, call_id, "unit_assigned", REQUESTER, at=depart - timedelta(seconds=30)
    )
    em_repo.transition_assignment(
        conn, assignment_id, "acknowledged", scenario.unit_id, at=depart - timedelta(seconds=15)
    )
    em_repo.transition_assignment(conn, assignment_id, "en_route", scenario.unit_id, at=depart)
    em_repo.transition_call(conn, call_id, "en_route", REQUESTER, at=depart)
    em_repo.transition_assignment(conn, assignment_id, "on_scene", scenario.unit_id, at=end)
    em_repo.transition_call(conn, call_id, "on_scene", REQUESTER, at=end)
    em_repo.transition_assignment(conn, assignment_id, "clear", scenario.unit_id, at=end)
    em_repo.transition_call(conn, call_id, "cleared", REQUESTER, at=end)
    result.call_id, result.assignment_id = call_id, assignment_id
    result.wall_s = time.perf_counter() - started
    return result


def cleanup_dispatches(conn: psycopg.Connection, tag_prefix: str, call_ids: list[str]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM commands WHERE idempotency_key LIKE %s", (f"verify-p0710-{tag_prefix}%",)
        )
        cur.execute(
            "DELETE FROM emergency_unit_assignments WHERE call_id = ANY(%s::uuid[])", (call_ids,)
        )
        cur.execute("DELETE FROM emergency_calls WHERE call_id = ANY(%s::uuid[])", (call_ids,))
    conn.commit()
