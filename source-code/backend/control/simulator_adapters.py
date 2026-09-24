"""P07.06: executes an *approved* command against the real simulator (P07.05
is the gate that got it here; this module only ever touches a command whose
`status` is already `approved`).

Each execution is one fresh, isolated `run_container.sh` invocation of the
pinned SUMO image (`simulator/control_adapters/`), the same execution
boundary every other SUMO-touching stage of this project uses - "the field"
is a real, separate process the backend talks to over a file-based action/
result protocol, not an in-process call. The action's concrete parameters
(a signal deviation in seconds, a VMS message) are **not** reconstructed from
the command record: `contracts/command/v1` deliberately carries only
`target.adapter`/`target.entity_id`, not a magnitude, so whatever selected
the alternative (an operator, or P07.09's orchestration) must pass `params`
explicitly to `execute_command` - never invented here.

Only a command whose `target` names a **registered** adapter+kind is ever
sent to the container (`KNOWN_ADAPTERS`, the same registry P07.05's policy
already checked before approval - re-checked here as the last gate before a
real action is dispatched, matching this project's repeated "check again at
the boundary that actually acts" pattern).
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

from backend.control.policy import KNOWN_ADAPTERS  # noqa: E402
from backend.repositories.commands import get_command, transition_command  # noqa: E402
from backend.roles import require_executor  # noqa: E402

ADAPTER_DIR = SOURCE_ROOT / "simulator" / "control_adapters"
OUTPUT_DIR = ADAPTER_DIR / "output"
RUN_SCRIPT = ADAPTER_DIR / "run_container.sh"
EXECUTION_TIMEOUT_S = 120


class AdapterExecutionError(Exception):
    pass


def _run_in_wsl(command: str, timeout_s: float) -> subprocess.CompletedProcess:
    """Every other SUMO-touching stage of this project runs the pinned image the same way: from this
    Windows host, through WSL's Docker. See docs/evidence for the digest; this is a demo-environment
    detail, not a claim about how a production field-adapter connection would be wired."""
    return subprocess.run(
        ["wsl.exe", "-e", "bash", "-lc", command], capture_output=True, text=True, timeout=timeout_s
    )


def execute_command(
    conn: psycopg.Connection, command_id: str, params: dict, actor: str, now: datetime | None = None
) -> dict:
    """Runs the command's target action against the real simulator and drives the command through
    executing -> executed/failed based on what the adapter actually observed. Returns the raw result."""
    require_executor(actor)
    now = now or datetime.now(timezone.utc)
    command = get_command(conn, command_id)
    if command is None:
        raise AdapterExecutionError(f"unknown command {command_id}")
    if command["status"] != "approved":
        raise AdapterExecutionError(
            f"command {command_id} is {command['status']!r}, not 'approved' - refusing to execute"
        )
    if command["target_adapter"] not in KNOWN_ADAPTERS:
        raise AdapterExecutionError(f"{command['target_adapter']!r} is not a registered adapter")

    transition_command(
        conn, command_id, "executing", actor, "dispatched to simulator adapter", at=now
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    action = {
        "idempotency_key": command["idempotency_key"],
        "action_type": command["action_type"],
        "target": {"adapter": command["target_adapter"], "entity_id": command["target_entity_id"]},
        "params": params,
    }
    (OUTPUT_DIR / "action.json").write_text(json.dumps(action, indent=2), encoding="utf-8")
    result_path = OUTPUT_DIR / "result.json"
    if result_path.is_file():
        result_path.unlink()

    posix_script = (
        str(RUN_SCRIPT).replace("\\", "/").replace("D:/", "/mnt/d/").replace("C:/", "/mnt/c/")
    )
    try:
        proc = _run_in_wsl(f"bash '{posix_script}'", EXECUTION_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        error = {
            "error_code": "adapter_unreachable",
            "message": f"simulator adapter did not respond within {EXECUTION_TIMEOUT_S}s",
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
        return {"acknowledged": False, "error": error["message"]}

    if not result_path.is_file():
        error = {
            "error_code": "adapter_unreachable",
            "message": f"no result from adapter container: {proc.stderr[-500:]}",
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
        return {"acknowledged": False, "error": error["message"]}

    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result["acknowledged"]:
        transition_command(
            conn,
            command_id,
            "executed",
            actor,
            "acknowledged by simulator adapter",
            at=now,
            acknowledged=True,
        )
    else:
        error = {
            "error_code": "invalid_target",
            "message": result["error"] or "adapter reported failure",
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
