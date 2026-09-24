"""P07.08 / P07.10: transit priority - the same real, clearance-preserving,
movement-aware TraCI mechanism as emergency pre-emption (`phase_control.
MovementPriority`), applied to a real transit bus, with **balanced**
measurement across stakeholders: transit benefit and cross-street
general-traffic harm - never only the transit number.

    python3 transit_priority_run.py <request.json> <result.json>

request.json: {"mode": "baseline" | "priority", "seed": int, "route":
[edge_id,...], "steps": [...], "transit_vehicle_id": str, "harm_edges":
[edge_id, ...] (real cross-street edges feeding the corridor - traffic that
does NOT get the extended green), "harm_window_s": float (sample harm for that
long after the bus departs, in both modes, so the comparison covers the same
window; 0 = until the bus arrives), "cross_routes": [[edge, ...], ...],
"initial_phase": {...}, ...}

`harm_edges` must name edges with a *real* competing movement at one of the
corridor's own intersections (this network's minor phases at a plain
through-intersection are pedestrian-only, not vehicular - see
backend/control/README.md's P07.08 section for which intersection and why).
The raw signal state of every controlled intersection is checked at every
step by `signal_safety.SignalSafetyMonitor`; its summary is in the result.
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


def _route_file(
    tmp: Path,
    seed: int,
    route_edges: list[str],
    transit_id: str,
    transit_depart: float,
    background_vehicles: int,
    cross_routes: list[list[str]],
) -> Path:
    rng = random.Random(seed)
    lines = [
        "<routes>",
        '<vType id="passenger" vClass="passenger" length="4.5" maxSpeed="16.7"/>',
        '<vType id="transit-bus" vClass="bus" length="12.0" maxSpeed="14.0"/>',
    ]
    for route_id, edges in VEHICLE_ROUTES:
        lines.append(f'<route id="{route_id}" edges="{" ".join(edges)}"/>')
    lines.append(f'<route id="transit-route" edges="{" ".join(route_edges)}"/>')
    for i, edges in enumerate(cross_routes):
        lines.append(f'<route id="cross-route-{i}" edges="{" ".join(edges)}"/>')
    entities = []
    for i in range(background_vehicles):
        route_id, _ = rng.choice(VEHICLE_ROUTES)
        depart = rng.randint(0, max(1, int(transit_depart) + 30))
        entities.append(
            (depart, f'<vehicle id="v{i}" type="passenger" route="{route_id}" depart="{depart}"/>')
        )
    # dedicated cross-street traffic: real vehicles on `harm_edges`' own route, so a genuine competing
    # movement exists to measure - VEHICLE_ROUTES alone does not cover every real edge in the network
    for i in range(len(cross_routes) * 8):
        depart = rng.randint(0, max(1, int(transit_depart) + 60))
        entities.append(
            (
                depart,
                f'<vehicle id="cv{i}" type="passenger" route="cross-route-{i % max(1, len(cross_routes))}" depart="{depart}"/>',
            )
        )
    entities.append(
        (
            transit_depart,
            f'<vehicle id="{transit_id}" type="transit-bus" route="transit-route" depart="{transit_depart}"/>',
        )
    )
    entities.sort(key=lambda e: e[0])
    lines += [tag for _, tag in entities]
    lines.append("</routes>")
    path = tmp / "transit.rou.xml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:  # noqa: PLR0912, PLR0915 - one linear scenario loop
    request_path, result_path = Path(sys.argv[1]), Path(sys.argv[2])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    tmp = result_path.resolve().parent
    transit_id = request["transit_vehicle_id"]
    transit_depart = float(request.get("transit_vehicle_depart_s", 20))
    harm_edges = request.get("harm_edges", [])
    harm_window = float(request.get("harm_window_s", 0.0))
    rou = _route_file(
        tmp,
        request["seed"],
        request["route"],
        transit_id,
        transit_depart,
        int(request.get("background_vehicles", 40)),
        request.get("cross_routes", []),
    )
    cfg = tmp / "transit.sumocfg"
    cfg.write_text(
        f'<configuration><input><net-file value="{NET_FILE}"/><route-files value="{rou}"/></input>'
        f'<time><begin value="0"/><end value="{SIM_END}"/></time></configuration>',
        encoding="utf-8",
    )
    steps = [dict(s) for s in request["steps"]]
    controllers: dict[str, MovementPriority] = {}
    safety_violations: list[str] = []
    transit_depart_time = transit_arrive_time = None
    cross_traffic_wait_samples: list[float] = []
    monitor = SignalSafetyMonitor(load_design(NET_FILE))

    try:
        traci.start(["sumo", "-c", str(cfg), "--no-step-log", "--start"])
        tl_ids = list(traci.trafficlight.getIDList())
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
            if transit_depart_time is None and transit_id in traci.simulation.getDepartedIDList():
                transit_depart_time = now
            sampling = (
                harm_window == 0.0
                and transit_arrive_time is None
                and transit_depart_time is not None
                or (
                    harm_window > 0.0
                    and transit_depart_time is not None
                    and now <= transit_depart_time + harm_window
                )
            )
            if sampling:
                cross_traffic_wait_samples.append(
                    sum(traci.edge.getWaitingTime(e) for e in harm_edges)
                )
            arrived_now = transit_id in traci.simulation.getArrivedIDList()
            if arrived_now:
                transit_arrive_time = now
            if (
                request["mode"] == "priority"
                and not arrived_now
                and step_index < len(steps)
                and transit_id in traci.vehicle.getIDList()
            ):
                step = steps[step_index]
                tl_id = step["tl_id"]
                if tl_id not in controllers:
                    controllers[tl_id] = MovementPriority(
                        tl_id, step["entry_edge"], step["exit_edge"], now
                    )
                controllers[tl_id].tick(now)
                if traci.vehicle.getRoadID(transit_id) == step["exit_edge"]:
                    controllers[tl_id].release()
                    step_index += 1
            if transit_arrive_time is not None and not (
                harm_window
                and transit_depart_time is not None
                and now < transit_depart_time + harm_window
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
        if transit_depart_time is None or transit_arrive_time is None
        else round(transit_arrive_time - transit_depart_time, 2)
    )
    result = {
        "mode": request["mode"],
        "transit_travel_time_s": travel_time,
        "transit_completed": transit_arrive_time is not None,
        "intersections_touched": list(controllers),
        "phase_transitions": {tl_id: c.transitions for tl_id, c in controllers.items()},
        "target_phases": {tl_id: c.target for tl_id, c in controllers.items()},
        "safety_violations": safety_violations,
        "signal_safety": summary,
        "cross_traffic_mean_waiting_time_s": round(
            sum(cross_traffic_wait_samples) / len(cross_traffic_wait_samples), 3
        )
        if cross_traffic_wait_samples
        else None,
        "cross_traffic_samples": len(cross_traffic_wait_samples),
        "harm_edges": harm_edges,
        "harm_window_s": harm_window,
    }
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                k: result[k]
                for k in ("mode", "transit_travel_time_s", "cross_traffic_mean_waiting_time_s")
            }
            | {"violations": summary["violation_count"]}
        )
    )
    return 0 if not safety_violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
