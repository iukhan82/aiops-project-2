"""P07.09: host side of the live simulator session (`simulator/control_adapters/
session_server.py`).

Starts the digest-pinned SUMO image once, then drives it request by request
over files in a shared session directory - the same "the field is a separate
process the backend talks to over a file protocol" boundary P07.06 uses, but
with a simulator that stays alive so a real *before* window, the action, a
real *after* window and a physical undo all happen in one and the same
simulated world.

`execute_command_live` is P07.06's `execute_command` against that live
session: the same status transitions (`approved -> executing -> executed/
failed`), the same registered-adapter re-check, and it reports the wall-clock
approval-to-acknowledgement latency (LAT-04) for the call.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.control.policy import KNOWN_ADAPTERS  # noqa: E402
from backend.repositories.commands import get_command, transition_command  # noqa: E402
from backend.roles import require_executor  # noqa: E402

ADAPTER_DIR = SOURCE_ROOT / "simulator" / "control_adapters"
SESSIONS_DIR = ADAPTER_DIR / "output" / "sessions"
IMAGE = "ghcr.io/eclipse-sumo/sumo@sha256:87623396d3501ca8d0ac25154e202bc38baa55a31c724e4dfd6ae60f297c6bf2"
POLL_S = 0.02


class LiveSessionError(Exception):
    pass


def _wsl_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace("D:/", "/mnt/d/").replace("C:/", "/mnt/c/")


class LiveSession:
    def __init__(self, seed: int = 1, vehicles: int = 300, span_s: int = 600, end_s: int = 3600):
        self.session_id = uuid.uuid4().hex[:12]
        self.directory = SESSIONS_DIR / self.session_id
        self.init = {"seed": seed, "vehicles": vehicles, "span_s": span_s, "end_s": end_s}
        self._proc: subprocess.Popen | None = None
        self._n = 0

    @property
    def container_name(self) -> str:
        return f"aiops-session-{self.session_id}"

    def _log_tail(self) -> str:
        log = self.directory / "server.log"
        return log.read_text(encoding="utf-8", errors="replace")[-3000:] if log.is_file() else ""

    def _wait_for(self, path: Path, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        while not path.is_file():
            if self._proc is not None and self._proc.poll() is not None and not path.is_file():
                raise LiveSessionError(
                    f"session process exited ({self._proc.returncode}): {self._log_tail()}"
                )
            if time.monotonic() > deadline:
                raise LiveSessionError(
                    f"timed out after {timeout_s}s waiting for {path.name}: {self._log_tail()}"
                )
            time.sleep(POLL_S)

    def start(self, timeout_s: float = 120.0) -> "LiveSession":
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "init.json").write_text(json.dumps(self.init), encoding="utf-8")
        rel = f"output/sessions/{self.session_id}"
        command = (
            f"docker run --rm --name {self.container_name} -e PYTHONPATH=/usr/share/sumo/tools --volume '{_wsl_path(SOURCE_ROOT)}':/work "
            f"-w /work/simulator/control_adapters {IMAGE} python3 session_server.py {rel}"
        )
        log = open(self.directory / "server.log", "w", encoding="utf-8")  # noqa: SIM115 - lives as long as the process
        self._proc = subprocess.Popen(
            ["wsl.exe", "-e", "bash", "-lc", command], stdout=log, stderr=subprocess.STDOUT
        )
        self._wait_for(self.directory / "ready.json", timeout_s)
        return self

    def call(self, op: str, timeout_s: float = 120.0, **kwargs) -> dict:
        if self._proc is None:
            raise LiveSessionError("session not started")
        n = self._n
        self._n += 1
        req = self.directory / f"req_{n}.json"
        tmp = self.directory / f"req_{n}.json.tmp"
        tmp.write_text(json.dumps({"op": op, **kwargs}), encoding="utf-8")
        os.replace(tmp, req)
        resp = self.directory / f"resp_{n}.json"
        self._wait_for(resp, timeout_s)
        return json.loads(resp.read_text(encoding="utf-8"))

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.poll() is None:
                try:
                    self.call("close", timeout_s=15)
                    self._proc.wait(timeout=20)
                except (LiveSessionError, subprocess.TimeoutExpired):
                    subprocess.run(
                        ["wsl.exe", "-e", "bash", "-lc", f"docker rm -f {self.container_name}"],
                        capture_output=True,
                        timeout=30,
                    )
        finally:
            self._proc = None
            shutil.rmtree(self.directory, ignore_errors=True)

    def __enter__(self) -> "LiveSession":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()


def execute_command_live(
    conn: psycopg.Connection,
    command_id: str,
    session: LiveSession,
    params: dict,
    actor: str,
    now: datetime | None = None,
) -> dict:
    """Like `simulator_adapters.execute_command`, but against an already-running live session. The returned
    dict carries `latency_s`: wall-clock time from this call (i.e. right after policy approval) to the
    simulator adapter's acknowledgement, for LAT-04."""
    require_executor(actor)
    started = time.perf_counter()
    command = get_command(conn, command_id)
    if command is None:
        raise LiveSessionError(f"unknown command {command_id}")
    if command["status"] != "approved":
        raise LiveSessionError(
            f"command {command_id} is {command['status']!r}, not 'approved' - refusing to execute"
        )
    if command["target_adapter"] not in KNOWN_ADAPTERS:
        raise LiveSessionError(f"{command['target_adapter']!r} is not a registered adapter")

    stamp = lambda: now or datetime.now(timezone.utc)  # noqa: E731
    transition_command(
        conn, command_id, "executing", actor, "dispatched to live simulator session", at=stamp()
    )
    resp = session.call(
        "apply",
        idempotency_key=command["idempotency_key"],
        adapter=command["target_adapter"],
        entity_id=command["target_entity_id"],
        params=params,
    )
    if resp["acknowledged"]:
        transition_command(
            conn,
            command_id,
            "executed",
            actor,
            "acknowledged by live simulator session",
            at=stamp(),
            acknowledged=True,
        )
    else:
        message = resp["error"] or "adapter reported failure"
        transition_command(
            conn,
            command_id,
            "failed",
            actor,
            message,
            at=stamp(),
            error_code="invalid_target",
            error_message=message,
            error_retryable=False,
        )
    return {**resp, "latency_s": time.perf_counter() - started}
