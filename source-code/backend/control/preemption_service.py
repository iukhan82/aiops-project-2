"""P07.07: host-side wiring - request an `emergency_preemption` command for
a real corridor route (P07.02's router), then, once P07.05 approves it, run
the actual corridor scenario against the real pinned SUMO image
(`simulator/control_adapters/preemption_run.py`), the same fresh-isolated-
container boundary P07.06's other adapters use.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.kpi_service import load_segments  # noqa: E402
from backend.analytics.topology import controlled_intersections  # noqa: E402
from backend.control.command_service import request_command  # noqa: E402
from backend.control.preemption import corridor_from_route, pick_corridor_id  # noqa: E402
from backend.repositories.commands import get_command, transition_command  # noqa: E402
from backend.roles import require_executor  # noqa: E402

NET_FILE = SOURCE_ROOT / "simulator" / "network" / "output" / "district.net.xml"
ADAPTER_DIR = SOURCE_ROOT / "simulator" / "control_adapters"
OUTPUT_DIR = ADAPTER_DIR / "output"
EXECUTION_TIMEOUT_S = 180


class PreemptionError(Exception):
    pass


def request_preemption(
    conn: psycopg.Connection,
    route_edges: tuple[str, ...],
    geometry: str,
    requested_by: str,
    now: datetime,
    idempotency_key: str,
    requester_role: str = "dispatcher",
) -> tuple[str, list]:
    """The emergency call that motivates this request is the caller's context (its own real
    lifecycle lives in `backend/repositories/emergency.py`); `commands/v1` has no emergency-call
    linkage column yet, so it is not threaded through here as a stored field."""
    segments = {s.edge_id: s for s in load_segments(conn, geometry)}
    tls = controlled_intersections(NET_FILE)
    steps = corridor_from_route(route_edges, segments, tls)
    if not steps:
        raise PreemptionError("this route passes no controlled intersection - nothing to pre-empt")
    # target.entity_id names the corridor the pre-emption spans (policy's "corridor" freshness check
    # applies to it as a whole), not any one intersection along it.
    corridor_id = pick_corridor_id(route_edges, segments, steps[0].tl_id)
    command_id, _ = request_command(
        conn,
        idempotency_key,
        "emergency_preemption",
        "emergency_preemption_adapter",
        corridor_id,
        requested_by,
        now,
        requester_role,
        ttl_s=120.0,
        params={"route_edges": list(route_edges)},
        geometry=geometry,
    )
    return command_id, steps


def _run_in_wsl(command: str, timeout_s: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["wsl.exe", "-e", "bash", "-lc", command], capture_output=True, text=True, timeout=timeout_s
    )


def _run_corridor_scenario(request: dict) -> dict | None:
    """Runs preemption_run.py in the container with `request`. Returns the parsed result, or None if
    the adapter never produced one (timeout or crash) - the caller decides how that fails the command."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "request.json").write_text(json.dumps(request, indent=2), encoding="utf-8")
    result_path = OUTPUT_DIR / "result.json"
    if result_path.is_file():
        result_path.unlink()
    work = str(SOURCE_ROOT).replace("\\", "/").replace("D:/", "/mnt/d/").replace("C:/", "/mnt/c/")
    docker_cmd = (
        f"docker run --rm -e PYTHONPATH=/usr/share/sumo/tools --volume '{work}':/work "
        f"-w /work/simulator/control_adapters ghcr.io/eclipse-sumo/sumo@sha256:"
        f"87623396d3501ca8d0ac25154e202bc38baa55a31c724e4dfd6ae60f297c6bf2 "
        f"python3 preemption_run.py output/request.json output/result.json"
    )
    try:
        _run_in_wsl(docker_cmd, EXECUTION_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None
    return json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else None


def _step_records(steps: list) -> list[dict]:
    return [{"tl_id": s.tl_id, "entry_edge": s.entry_edge, "exit_edge": s.exit_edge} for s in steps]


def execute_preemption(
    conn: psycopg.Connection,
    command_id: str,
    steps: list,
    emergency_vehicle_id: str,
    route_edges: tuple[str, ...],
    actor: str,
    now: datetime | None = None,
    seed: int = 1,
    extra_request: dict | None = None,
) -> dict:
    require_executor(actor)
    now = now or datetime.now(timezone.utc)
    command = get_command(conn, command_id)
    if command is None:
        raise PreemptionError(f"unknown command {command_id}")
    if command["status"] != "approved":
        raise PreemptionError(
            f"command {command_id} is {command['status']!r}, not 'approved' - refusing to execute"
        )

    transition_command(
        conn, command_id, "executing", actor, "dispatched to pre-emption adapter", at=now
    )
    request = {
        "mode": "preempt",
        "seed": seed,
        "route": list(route_edges),
        "emergency_vehicle_id": emergency_vehicle_id,
        "steps": _step_records(steps),
        **(extra_request or {}),
    }
    result = _run_corridor_scenario(request)
    if result is None:
        error = {
            "error_code": "adapter_unreachable",
            "message": "pre-emption adapter produced no result",
            "retryable": True,
        }
        transition_command(
            conn,
            command_id,
            "failed",
            actor,
            error["message"],
            at=now,
            error_code=error["error_code"],
            error_message=error["message"],
            error_retryable=error["retryable"],
        )
        return {"ev_completed": False, "safety_violations": [error["message"]]}

    if result["ev_completed"] and not result["safety_violations"]:
        transition_command(
            conn,
            command_id,
            "executed",
            actor,
            "emergency vehicle cleared the corridor",
            at=now,
            acknowledged=True,
        )
    else:
        error = {
            "error_code": "internal" if result["safety_violations"] else "adapter_unreachable",
            "message": "; ".join(result["safety_violations"])
            or "emergency vehicle did not complete the corridor",
            "retryable": False,
        }
        transition_command(
            conn,
            command_id,
            "failed",
            actor,
            error["message"],
            at=now,
            error_code=error["error_code"],
            error_message=error["message"],
            error_retryable=error["retryable"],
        )
    return result


def abort_preemption(
    conn: psycopg.Connection,
    command_id: str,
    steps: list,
    emergency_vehicle_id: str,
    route_edges: tuple[str, ...],
    abort_after_step: int,
    actor: str,
    now: datetime | None = None,
    seed: int = 1,
    extra_request: dict | None = None,
) -> dict:
    """Same execution path, `mode=abort` - restores every touched intersection's real original
    program mid-corridor and reports the restoration, then marks the command `failed` (the corridor
    was not completed - an abort is never reported as a success)."""
    require_executor(actor)
    now = now or datetime.now(timezone.utc)
    command = get_command(conn, command_id)
    if command is None or command["status"] != "approved":
        raise PreemptionError(f"command {command_id} is not approved")
    transition_command(
        conn,
        command_id,
        "executing",
        actor,
        "dispatched to pre-emption adapter (abort scenario)",
        at=now,
    )
    request = {
        "mode": "abort",
        "seed": seed,
        "route": list(route_edges),
        "emergency_vehicle_id": emergency_vehicle_id,
        "abort_after_step": abort_after_step,
        "steps": _step_records(steps),
        **(extra_request or {}),
    }
    result = _run_corridor_scenario(request)
    if result is None:
        raise PreemptionError("pre-emption adapter produced no result during abort")
    error = {
        "error_code": "internal",
        "message": "aborted: caller requested rollback of the pre-emption corridor",
        "retryable": False,
    }
    transition_command(
        conn,
        command_id,
        "failed",
        actor,
        error["message"],
        at=now,
        error_code=error["error_code"],
        error_message=error["message"],
        error_retryable=error["retryable"],
    )
    return result
