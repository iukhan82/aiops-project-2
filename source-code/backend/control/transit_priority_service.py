"""P07.08: host-side wiring - request a `transit_priority` command for a
real transit route (reusing P07.02's router / P07.07's corridor-derivation
logic), then, once P07.05 approves it, run the real corridor scenario
against the real pinned SUMO image (`simulator/control_adapters/
transit_priority_run.py`) - same execution boundary as every other adapter.
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


class TransitPriorityError(Exception):
    pass


def request_transit_priority(
    conn: psycopg.Connection,
    route_edges: tuple[str, ...],
    geometry: str,
    requested_by: str,
    now: datetime,
    idempotency_key: str,
    requester_role: str = "operator",
) -> tuple[str, list]:
    segments = {s.edge_id: s for s in load_segments(conn, geometry)}
    tls = controlled_intersections(NET_FILE)
    steps = corridor_from_route(route_edges, segments, tls)
    if not steps:
        raise TransitPriorityError(
            "this route passes no controlled intersection - nothing to prioritize"
        )
    corridor_id = pick_corridor_id(route_edges, segments, steps[0].tl_id)
    command_id, _ = request_command(
        conn,
        idempotency_key,
        "transit_priority",
        "transit_priority_adapter",
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


def _run_scenario(request: dict) -> dict | None:
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
        f"python3 transit_priority_run.py output/request.json output/result.json"
    )
    try:
        _run_in_wsl(docker_cmd, EXECUTION_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None
    return json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else None


def execute_transit_priority(
    conn: psycopg.Connection,
    command_id: str,
    steps: list,
    transit_vehicle_id: str,
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
        raise TransitPriorityError(f"unknown command {command_id}")
    if command["status"] != "approved":
        raise TransitPriorityError(
            f"command {command_id} is {command['status']!r}, not 'approved' - refusing to execute"
        )

    transition_command(
        conn, command_id, "executing", actor, "dispatched to transit-priority adapter", at=now
    )
    request = {
        "mode": "priority",
        "seed": seed,
        "route": list(route_edges),
        "transit_vehicle_id": transit_vehicle_id,
        "steps": [
            {"tl_id": s.tl_id, "entry_edge": s.entry_edge, "exit_edge": s.exit_edge} for s in steps
        ],
        **(extra_request or {}),
    }
    result = _run_scenario(request)
    if result is None:
        error = {
            "error_code": "adapter_unreachable",
            "message": "transit-priority adapter produced no result",
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
        return {"transit_completed": False, "safety_violations": [error["message"]]}
    if result["transit_completed"] and not result["safety_violations"]:
        transition_command(
            conn,
            command_id,
            "executed",
            actor,
            "transit vehicle cleared the corridor",
            at=now,
            acknowledged=True,
        )
    else:
        error = {
            "error_code": "internal" if result["safety_violations"] else "adapter_unreachable",
            "message": "; ".join(result["safety_violations"])
            or "transit vehicle did not complete the corridor",
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
