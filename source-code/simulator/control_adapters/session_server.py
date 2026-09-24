"""P07.09: one long-lived, lock-step simulator session (real SUMO via TraCI).

    python3 session_server.py <session_dir>

P07.06 runs each action in a fresh container, so an action's effect dies with
the process and nothing can be measured *around* it or physically undone
afterwards. Independent outcome verification needs one simulator that stays
alive across "measure before -> apply -> measure after -> undo". This server
is that simulator, driven request by request over files in `session_dir`
(`init.json` in; `ready.json`, `req_<n>.json` in, `resp_<n>.json` out). It
advances simulated time only when asked (lock-step), so the host controls
exactly which window is "pre" and which is "post".

Operations (`op`): `advance` (step N seconds, sample total edge waiting time),
`apply` (the same `adapters.apply_*` functions P07.06 uses, plus a recorded
undo state), `undo` (restore that state and read it back from TraCI),
`state` (read-only view), `close`.

Fail-safe: a session that hears nothing for `IDLE_TIMEOUT_S` closes itself.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, "/usr/share/sumo/tools")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "simulator" / "demand"))
import traci  # noqa: E402
from adapters import AdapterError, apply_diversion, apply_signal, apply_vms  # noqa: E402
from generate_demand import VEHICLE_ROUTES  # noqa: E402

NET_FILE = (
    Path(__file__).resolve().parents[2] / "simulator" / "network" / "output" / "district.net.xml"
)
POLL_S = 0.02
IDLE_TIMEOUT_S = 300


def _write(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def _route_file(directory: Path, seed: int, vehicles: int, span_s: int) -> Path:
    """Evenly spaced departures cycling through a seeded shuffle of the routes: the demand is the same in
    every window, so a before/after difference is the action's, not random departure clumping."""
    rng = random.Random(seed)
    lines = ["<routes>", '<vType id="passenger" vClass="passenger" length="4.5" maxSpeed="16.7"/>']
    for route_id, edges in VEHICLE_ROUTES:
        lines.append(f'<route id="{route_id}" edges="{" ".join(edges)}"/>')
    order = [route_id for route_id, _ in VEHICLE_ROUTES]
    rng.shuffle(order)
    for i in range(vehicles):
        lines.append(
            f'<vehicle id="v{i}" type="passenger" route="{order[i % len(order)]}" depart="{int(i * span_s / vehicles)}"/>'
        )
    lines.append("</routes>")
    path = directory / "session.rou.xml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _op_advance(req: dict) -> dict:
    edges = req.get("sample_edges") or [e for e in traci.edge.getIDList() if not e.startswith(":")]
    total, samples = 0.0, 0
    for _ in range(int(req["seconds"])):
        traci.simulationStep()
        total += sum(traci.edge.getWaitingTime(e) for e in edges)
        samples += 1
    return {
        "sim_time": traci.simulation.getTime(),
        "samples": samples,
        "sampled_edges": len(edges),
        "mean_total_waiting_s": round(total / samples, 4) if samples else None,
        "vehicles_in_network": traci.vehicle.getIDCount(),
    }


def _capture_undo_state(adapter: str, entity: str) -> dict:
    if adapter == "signal_controller_adapter" and entity in traci.trafficlight.getIDList():
        return {
            "kind": "signal",
            "tl_id": entity,
            "program": traci.trafficlight.getProgram(entity),
            "phase": traci.trafficlight.getPhase(entity),
            "next_switch": traci.trafficlight.getNextSwitch(entity),
        }
    if adapter == "diversion_adapter":
        lanes = [f"{entity}_{i}" for i in (2, 3) if f"{entity}_{i}" in traci.lane.getIDList()]
        return {
            "kind": "diversion",
            "lanes": {lane: list(traci.lane.getDisallowed(lane)) for lane in lanes},
        }
    return {"kind": "none"}


def _op_apply(req: dict, applied: dict) -> dict:
    key = req["idempotency_key"]
    if key in applied:
        return {**applied[key]["result"], "idempotent_replay": True}
    adapter, entity, params = req["adapter"], req["entity_id"], req.get("params", {})
    undo_state = _capture_undo_state(adapter, entity)
    try:
        if adapter == "signal_controller_adapter":
            observation = apply_signal(
                entity,
                float(params.get("deviation_s", 0.0)),
                float(params.get("max_deviation_s", 20.0)),
            )
        elif adapter == "diversion_adapter":
            observation = apply_diversion(entity)
        elif adapter == "vms_adapter":
            observation = apply_vms(entity, params.get("message", ""))
        else:
            raise AdapterError(f"{adapter!r} is not a live-session adapter")
        result = {
            "acknowledged": True,
            "error": None,
            "observation": observation,
            "sim_time": traci.simulation.getTime(),
            "idempotent_replay": False,
        }
    except (AdapterError, traci.TraCIException) as exc:
        return {
            "acknowledged": False,
            "error": str(exc),
            "observation": {},
            "sim_time": traci.simulation.getTime(),
            "idempotent_replay": False,
        }
    applied[key] = {
        "undo_state": undo_state,
        "result": {k: v for k, v in result.items() if k != "idempotent_replay"},
        "undone": False,
    }
    return result


def _op_undo(req: dict, applied: dict) -> dict:
    entry = applied.get(req["idempotency_key"])
    if entry is None:
        return {
            "restored": False,
            "error": "no such applied action in this session",
            "sim_time": traci.simulation.getTime(),
        }
    if entry["undone"]:
        return {"restored": True, "mode": "already_undone", "sim_time": traci.simulation.getTime()}
    state = entry["undo_state"]
    now = traci.simulation.getTime()
    if state["kind"] == "signal":
        tl = state["tl_id"]
        same_phase = traci.trafficlight.getPhase(tl) == state["phase"]
        if (
            same_phase
            and state["next_switch"] > now
            and traci.trafficlight.getNextSwitch(tl) > state["next_switch"] + 0.01
        ):
            traci.trafficlight.setPhaseDuration(tl, state["next_switch"] - now)
            mode = "cancelled_remaining_extension"
        else:
            mode = "extension_already_expired"
        restored = traci.trafficlight.getProgram(tl) == state["program"] and (
            not same_phase
            or traci.trafficlight.getNextSwitch(tl) <= max(state["next_switch"], now) + 0.5
        )
        evidence = {
            "tl_id": tl,
            "program": traci.trafficlight.getProgram(tl),
            "phase": traci.trafficlight.getPhase(tl),
            "next_switch": traci.trafficlight.getNextSwitch(tl),
            "original_next_switch": state["next_switch"],
        }
    elif state["kind"] == "diversion":
        for lane, original in state["lanes"].items():
            traci.lane.setDisallowed(lane, original)
        observed = {lane: list(traci.lane.getDisallowed(lane)) for lane in state["lanes"]}
        restored = observed == state["lanes"]
        mode, evidence = (
            "reopened_lanes",
            {"observed_disallowed": observed, "original_disallowed": state["lanes"]},
        )
    else:
        mode, restored, evidence = "nothing_to_undo", True, {}
    entry["undone"] = restored
    return {"restored": restored, "mode": mode, "evidence": evidence, "sim_time": now}


def _op_state(req: dict) -> dict:
    out: dict = {"sim_time": traci.simulation.getTime()}
    if req.get("tl_id"):
        tl = req["tl_id"]
        out["signal"] = {
            "program": traci.trafficlight.getProgram(tl),
            "phase": traci.trafficlight.getPhase(tl),
            "next_switch": traci.trafficlight.getNextSwitch(tl),
        }
    if req.get("edge_id"):
        out["lanes"] = {
            f"{req['edge_id']}_{i}": list(traci.lane.getDisallowed(f"{req['edge_id']}_{i}"))
            for i in (2, 3)
            if f"{req['edge_id']}_{i}" in traci.lane.getIDList()
        }
    return out


def main() -> int:
    directory = Path(sys.argv[1]).resolve()
    init = json.loads((directory / "init.json").read_text(encoding="utf-8"))
    rou = _route_file(directory, int(init["seed"]), int(init["vehicles"]), int(init["span_s"]))
    cfg = directory / "session.sumocfg"
    cfg.write_text(
        f'<configuration><input><net-file value="{NET_FILE}"/><route-files value="{rou}"/></input>'
        f'<time><begin value="0"/><end value="{int(init.get("end_s", 3600))}"/></time></configuration>',
        encoding="utf-8",
    )
    applied: dict = {}
    traci.start(
        [
            "sumo",
            "-c",
            str(cfg),
            "--no-step-log",
            "--start",
            "--seed",
            str(init["seed"]),
            "--ignore-route-errors",
        ]
    )
    try:
        _write(directory / "ready.json", {"ready": True, "sim_time": traci.simulation.getTime()})
        n, last_activity = 0, time.monotonic()
        while time.monotonic() - last_activity < IDLE_TIMEOUT_S:
            req_path = directory / f"req_{n}.json"
            if not req_path.is_file():
                time.sleep(POLL_S)
                continue
            last_activity = time.monotonic()
            req = json.loads(req_path.read_text(encoding="utf-8"))
            op = req["op"]
            if op == "close":
                _write(directory / f"resp_{n}.json", {"closed": True})
                return 0
            handler = {
                "advance": lambda: _op_advance(req),
                "apply": lambda: _op_apply(req, applied),
                "undo": lambda: _op_undo(req, applied),
                "state": lambda: _op_state(req),
            }.get(op)
            resp = handler() if handler else {"error": f"unknown op {op!r}"}
            _write(directory / f"resp_{n}.json", resp)
            n += 1
        return 2
    finally:
        try:
            traci.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
