"""P07.06: the container entrypoint. Reads one action request, applies it
against a real running SUMO instance via TraCI, and writes one result -
called once per approved command by `backend/control/simulator_adapters.py`
(each invocation is a fresh, isolated container: this platform's boundary
between the backend and "the field," matching how every other SUMO-touching
stage in this project runs the pinned image, never SUMO on the host).

    python3 run_action.py <action.json> <result.json> <ledger.jsonl>

Idempotency is enforced here, not just at the database layer P07.05 already
proved: if `idempotency_key` is already in `ledger.jsonl`, the stored result
is returned unchanged and SUMO is never even started - a second "execution"
of the same command cannot double-apply a real-world action.
"""

from __future__ import annotations

import fcntl
import json
import sys
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
SIM_SECONDS_BEFORE_ACTION = 20
SIM_SECONDS_AFTER_ACTION = 10


def _read_ledger(path: Path) -> dict:
    if not path.is_file():
        return {}
    return {
        json.loads(line)["idempotency_key"]: json.loads(line)["result"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    }


def _append_ledger(path: Path, idempotency_key: str, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps({"idempotency_key": idempotency_key, "result": result}) + "\n")
        fcntl.flock(stream, fcntl.LOCK_UN)


def _route_file(tmp: Path) -> Path:
    lines = ["<routes>", '<vType id="passenger" vClass="passenger" length="4.5" maxSpeed="16.7"/>']
    for route_id, edges in VEHICLE_ROUTES:
        lines.append(f'<route id="{route_id}" edges="{" ".join(edges)}"/>')
    vehicles = sorted(range(40), key=lambda i: i % 15)
    for i in vehicles:
        route_id, _ = VEHICLE_ROUTES[i % len(VEHICLE_ROUTES)]
        lines.append(f'<vehicle id="v{i}" type="passenger" route="{route_id}" depart="{i % 15}"/>')
    lines.append("</routes>")
    path = tmp / "action.rou.xml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    action_path, result_path, ledger_path = (Path(p) for p in sys.argv[1:4])
    action = json.loads(action_path.read_text(encoding="utf-8"))
    key = action["idempotency_key"]

    ledger = _read_ledger(ledger_path)
    if key in ledger:
        result = {**ledger[key], "idempotent_replay": True}
        result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print("idempotent replay, SUMO not started")
        return 0

    tmp = result_path.resolve().parent
    rou = _route_file(tmp)
    cfg = tmp / "action.sumocfg"
    cfg.write_text(
        f'<configuration><input><net-file value="{NET_FILE}"/><route-files value="{rou}"/></input>'
        f'<time><begin value="0"/><end value="120"/></time></configuration>',
        encoding="utf-8",
    )
    ok, error, observation = True, None, {}
    try:
        traci.start(["sumo", "-c", str(cfg), "--no-step-log", "--start"])
        for _ in range(SIM_SECONDS_BEFORE_ACTION):
            traci.simulationStep()
        adapter = action["target"]["adapter"]
        entity = action["target"]["entity_id"]
        params = action.get("params", {})
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
            raise AdapterError(f"{adapter!r} is not a registered simulator adapter")
        for _ in range(SIM_SECONDS_AFTER_ACTION):
            traci.simulationStep()
        observation["confirmed_at"] = traci.simulation.getTime()
    except AdapterError as exc:
        ok, error = False, str(exc)
    finally:
        try:
            traci.close()
        except Exception:  # noqa: BLE001 - closing a connection that never opened must not mask the real error
            pass

    result = {
        "idempotency_key": key,
        "acknowledged": ok,
        "error": error,
        "observation": observation,
        "idempotent_replay": False,
    }
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    _append_ledger(ledger_path, key, {k: v for k, v in result.items() if k != "idempotent_replay"})
    print("acknowledged" if ok else f"failed: {error}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
