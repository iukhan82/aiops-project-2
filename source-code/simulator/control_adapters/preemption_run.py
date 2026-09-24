"""P07.07 / P07.10: runs one emergency-corridor scenario against the real
pinned SUMO image via TraCI - baseline (no pre-emption), pre-emption, or an
abort mid-corridor - and reports what actually happened.

    python3 preemption_run.py <request.json> <result.json>

request.json: {"mode": "baseline" | "preempt" | "abort" | "preempt_naive" | "snapshot", "seed": int,
"route": [edge_id, ...], "steps": [{"tl_id","entry_edge","exit_edge"}, ...],
"emergency_vehicle_id": str, "emergency_vehicle_depart_s": float,
"background_vehicles": int, "background_span_s": float (departures are spread over [0, span];
default vehicle-departure + 30 s), "abort_after_step": int (mode "abort"),
"snapshot_window_s": float (mode "snapshot": no vehicle is pre-empted; the run stops just before the emergency
vehicle would depart and reports each edge's mean travel time over the last window - the live-KPI feed a
dispatch-time ETA is computed from),
"closed_edges": [edge_id, ...] (an incident: general lanes closed to all
vehicles, background routes that would use them are left out),
"ev_type": {"length","accel","decel","maxSpeed"} overrides,
"cross_routes": [[edge_id, ...], ...] (dedicated cross-street vehicles, so a genuine competing movement
exists to measure), "harm_edges": [edge_id, ...] and "harm_window_s": float (sample cross-street
and network-wide waiting for that long after the vehicle departs, in every mode,
so baseline and pre-emption are compared over the same window),
"initial_phase": {tl_id: phase}, "initial_phase_duration_s": float}

Safety is measured, not assumed: `signal_safety.SignalSafetyMonitor` reads the
raw red/yellow/green state of every controlled intersection at every step and
checks it against the network's own design (conflicting greens, yellow and
pedestrian-clearance timing). Its summary is part of the result. Phase changes
themselves are made by `phase_control.MovementPriority` (movement-aware, and
it never shortens a fixed yellow/all-red/pedestrian phase).
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, "/usr/share/sumo/tools")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "simulator" / "demand"))
import traci  # noqa: E402
from generate_demand import VEHICLE_ROUTES  # noqa: E402
from phase_control import MovementPriority, SafetyViolation  # noqa: E402
from signal_safety import SignalSafetyMonitor, load_design  # noqa: E402

NET_FILE = (
    Path(__file__).resolve().parents[2] / "simulator" / "network" / "output" / "district.net.xml"
)
SIM_END = 900
GENERAL_LANES = (2, 3)


class NaivePhaseZero:
    """NEGATIVE CONTROL ONLY - never used by the platform. The first version of P07.07's mechanism: drive every
    intersection to phase 0 by ending each phase immediately (`setPhaseDuration(0)`), including the yellow and
    all-red/pedestrian phases on the way. It exists so the signal-safety monitor can be shown to catch a real
    unsafe strategy in the real simulator, not only synthetic state strings."""

    def __init__(self, tl_id: str, entry_edge: str, exit_edge: str, now: float):  # noqa: ARG002
        self.tl_id = tl_id
        self.original_program = traci.trafficlight.getProgram(tl_id)
        self.transitions: list[tuple[int, int]] = []
        self.target = 0
        self.holding = False
        self._last_phase = traci.trafficlight.getPhase(tl_id)

    def tick(self, now: float) -> None:  # noqa: ARG002
        current = traci.trafficlight.getPhase(self.tl_id)
        if current != self._last_phase:
            self.transitions.append((self._last_phase, current))
            self._last_phase = current
        if self.holding:
            return
        if current == 0:
            traci.trafficlight.setPhaseDuration(self.tl_id, 30.0)
            self.holding = True
        else:
            traci.trafficlight.setPhaseDuration(self.tl_id, 0.0)

    def release(self) -> None:
        return

    def restore(self) -> None:
        if traci.trafficlight.getProgram(self.tl_id) != self.original_program:
            traci.trafficlight.setProgram(self.tl_id, self.original_program)


def _route_file(
    tmp: Path,
    seed: int,
    route_edges: list[str],
    ev_id: str,
    ev_depart: float,
    background_vehicles: int,
    ev_type: dict,
    closed_edges: set[str],
    cross_routes: list[list[str]],
    span_s: int,
) -> Path:
    rng = random.Random(seed)
    vt = {"length": 6.0, "accel": 2.6, "decel": 4.5, "maxSpeed": 20.0, **ev_type}
    attrs = " ".join(f'{k}="{v}"' for k, v in vt.items())
    lines = [
        "<routes>",
        '<vType id="passenger" vClass="passenger" length="4.5" maxSpeed="16.7"/>',
        f'<vType id="emergency" vClass="emergency" guiShape="emergency" {attrs}/>',
    ]
    usable = [(rid, edges) for rid, edges in VEHICLE_ROUTES if not closed_edges & set(edges)]
    for route_id, edges in usable:
        lines.append(f'<route id="{route_id}" edges="{" ".join(edges)}"/>')
    lines.append(f'<route id="corridor-route" edges="{" ".join(route_edges)}"/>')
    for i, edges in enumerate(cross_routes):
        lines.append(f'<route id="cross-route-{i}" edges="{" ".join(edges)}"/>')
    entities = []
    for i in range(background_vehicles):
        route_id, _ = rng.choice(usable)
        depart = rng.randint(0, max(1, span_s))
        entities.append(
            (depart, f'<vehicle id="v{i}" type="passenger" route="{route_id}" depart="{depart}"/>')
        )
    for i in range(len(cross_routes) * 8):
        depart = rng.randint(0, max(1, int(ev_depart) + 60))
        entities.append(
            (
                depart,
                f'<vehicle id="cv{i}" type="passenger" route="cross-route-{i % len(cross_routes)}" depart="{depart}"/>',
            )
        )
    entities.append(
        (
            ev_depart,
            f'<vehicle id="{ev_id}" type="emergency" route="corridor-route" depart="{ev_depart}"/>',
        )
    )
    entities.sort(key=lambda e: e[0])
    lines += [tag for _, tag in entities]
    lines.append("</routes>")
    path = tmp / "corridor.rou.xml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:  # noqa: PLR0912, PLR0915 - one linear scenario loop
    request_path, result_path = Path(sys.argv[1]), Path(sys.argv[2])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    tmp = result_path.resolve().parent
    ev_id = request["emergency_vehicle_id"]
    ev_depart = float(request.get("emergency_vehicle_depart_s", 45))
    closed_edges = set(request.get("closed_edges", []))
    rou = _route_file(
        tmp,
        request["seed"],
        request["route"],
        ev_id,
        ev_depart,
        int(request.get("background_vehicles", 60)),
        request.get("ev_type", {}),
        closed_edges,
        request.get("cross_routes", []),
        int(request.get("background_span_s", ev_depart + 30)),
    )
    cfg = tmp / "corridor.sumocfg"
    cfg.write_text(
        f'<configuration><input><net-file value="{NET_FILE}"/><route-files value="{rou}"/></input>'
        f'<time><begin value="0"/><end value="{SIM_END}"/></time></configuration>',
        encoding="utf-8",
    )
    steps = [dict(s) for s in request["steps"]]
    mode = request["mode"]
    harm_edges = request.get("harm_edges", [])
    harm_window = float(request.get("harm_window_s", 0.0))
    safety_violations: list[str] = []
    controllers: dict[str, MovementPriority | NaivePhaseZero] = {}
    original_programs: dict[str, str] = {}
    ev_depart_time = ev_arrive_time = None
    aborted_at_step = None
    harm_samples: list[float] = []
    network_samples: list[float] = []
    snapshot_sums: dict[str, float] = {}
    snapshot_steps = 0
    snapshot_window = float(request.get("snapshot_window_s", 30.0))
    monitor = SignalSafetyMonitor(load_design(NET_FILE))

    try:
        traci.start(["sumo", "-c", str(cfg), "--no-step-log", "--start"])
        tl_ids = list(traci.trafficlight.getIDList())
        all_edges = [e for e in traci.edge.getIDList() if not e.startswith(":")]
        for edge in closed_edges:
            for i in GENERAL_LANES:
                lane = f"{edge}_{i}"
                if lane in traci.lane.getIDList():
                    traci.lane.setDisallowed(lane, ["passenger", "emergency"])
        for tl_id, phase in request.get("initial_phase", {}).items():
            traci.trafficlight.setPhase(tl_id, phase)
            traci.trafficlight.setPhaseDuration(
                tl_id, float(request.get("initial_phase_duration_s", 45.0))
            )
        step_index = 0
        while traci.simulation.getTime() < SIM_END:
            traci.simulationStep()
            now = traci.simulation.getTime()
            monitor.observe(
                now, {tl: traci.trafficlight.getRedYellowGreenState(tl) for tl in tl_ids}
            )
            if mode == "snapshot":
                if now >= ev_depart - snapshot_window:
                    for e in all_edges:
                        snapshot_sums[e] = snapshot_sums.get(e, 0.0) + traci.edge.getTraveltime(e)
                    snapshot_steps += 1
                if now >= ev_depart - 1:
                    break
            if ev_depart_time is None and ev_id in traci.simulation.getDepartedIDList():
                ev_depart_time = now
            if ev_depart_time is not None and harm_window and now <= ev_depart_time + harm_window:
                harm_samples.append(sum(traci.edge.getWaitingTime(e) for e in harm_edges))
                network_samples.append(sum(traci.edge.getWaitingTime(e) for e in all_edges))
            arrived_now = ev_id in traci.simulation.getArrivedIDList()
            if arrived_now:
                ev_arrive_time = now
            active = mode in ("preempt", "preempt_naive") or (
                mode == "abort" and step_index < request.get("abort_after_step", 0)
            )
            if (
                active
                and not arrived_now
                and step_index < len(steps)
                and ev_id in traci.vehicle.getIDList()
            ):
                step = steps[step_index]
                tl_id = step["tl_id"]
                if tl_id not in controllers:
                    controller_class = (
                        NaivePhaseZero if mode == "preempt_naive" else MovementPriority
                    )
                    controllers[tl_id] = controller_class(
                        tl_id, step["entry_edge"], step["exit_edge"], now
                    )
                    original_programs[tl_id] = controllers[tl_id].original_program
                controllers[tl_id].tick(now)
                if traci.vehicle.getRoadID(ev_id) == step["exit_edge"]:
                    controllers[tl_id].release()
                    step_index += 1
            if (
                mode == "abort"
                and step_index >= request.get("abort_after_step", 0)
                and aborted_at_step is None
                and controllers
            ):
                aborted_at_step = step_index
                for controller in controllers.values():
                    controller.restore()
                restored = {
                    tl_id: traci.trafficlight.getProgram(tl_id) for tl_id in original_programs
                }
                if restored != original_programs:
                    safety_violations.append(
                        f"restoration mismatch: {restored} != {original_programs}"
                    )
                break
            if ev_arrive_time is not None and not (
                harm_window and ev_depart_time is not None and now < ev_depart_time + harm_window
            ):
                break
        for controller in controllers.values():
            controller.restore()
    except SafetyViolation as exc:
        safety_violations.append(str(exc))
    finally:
        try:
            traci.close()
        except Exception:  # noqa: BLE001
            pass

    summary = monitor.summary()
    safety_violations += [str(v) for v in monitor.violations]
    travel_time = (
        None
        if ev_depart_time is None or ev_arrive_time is None
        else round(ev_arrive_time - ev_depart_time, 2)
    )
    result = {
        "mode": mode,
        "travel_time_s": travel_time,
        "ev_completed": ev_arrive_time is not None,
        "intersections_touched": list(original_programs),
        "original_programs": original_programs,
        "phase_transitions": {tl_id: c.transitions for tl_id, c in controllers.items()},
        "target_phases": {tl_id: c.target for tl_id, c in controllers.items()},
        "safety_violations": safety_violations,
        "signal_safety": summary,
        "aborted_at_step": aborted_at_step,
        "edge_travel_times": {e: round(v / snapshot_steps, 3) for e, v in snapshot_sums.items()}
        if snapshot_steps
        else None,
        "traffic": {
            "window_s": harm_window,
            "samples": len(harm_samples),
            "harm_edges_mean_waiting_s": round(sum(harm_samples) / len(harm_samples), 4)
            if harm_samples
            else None,
            "network_mean_waiting_s": round(sum(network_samples) / len(network_samples), 4)
            if network_samples
            else None,
        },
    }
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {k: result[k] for k in ("mode", "travel_time_s", "ev_completed")}
            | {"violations": summary["violation_count"]}
        )
    )
    return 0 if not safety_violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
