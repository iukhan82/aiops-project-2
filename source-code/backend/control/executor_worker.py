"""P08.08: the command executor - the only thing that executes a command.

    python source-code/backend/control/executor_worker.py [--database aiops_demo]

No person executes anything: a requester asks, a second person approves, and this service - `system:command-executor`, the only
identity `backend/roles.py` gives EXECUTE authority - picks up an approved command and drives the adapter. It runs the real
simulator adapter (`simulator_adapters.execute_command`: TraCI in the pinned SUMO container) with the parameters stored when the
command was requested, and records what the adapter actually observed: `executed`, or `failed` with the adapter's own error.

It also expires commands nobody acted on in time, so an approval that sat too long is never executed, and records a heartbeat
so the platform-status screen can tell a running executor from a stopped one.

P09.03: an approval is not taken on trust. Immediately before the adapter is driven the executor asks the policy engine (Open Policy
Agent) to decide again, against fresh facts, whether THIS command may be executed by THIS identity now: the recorded approver must be a
different person in a role that could approve the class, the command must not have expired, the target must still exist with fresh
evidence. A refusal moves the command to `denied` (with the engine's reason) and nothing is executed. If the engine cannot answer, the
command stays `approved` and is not executed; it is asked again after `GATE_RETRY_S`, and expires by its own time limit if the engine
stays down - a policy outage stops actions, it never lets one through.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
import traceback
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.control.heartbeat import heartbeat  # noqa: E402
from backend.control.policy import current_geometry, execution_gate  # noqa: E402
from backend.control.simulator_adapters import AdapterExecutionError, execute_command  # noqa: E402
from backend.repositories import commands as command_repo  # noqa: E402
from backend.roles import EXECUTOR  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

SERVICE = "command-executor"
POLL_S = 2.0
RECONNECT_S = 5.0
GATE_RETRY_S = 15.0
_gate_retry_after: dict[str, float] = {}


def next_approved(conn: psycopg.Connection) -> tuple[str, dict] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.command_id, p.params FROM commands c LEFT JOIN command_params p USING (command_id) "
            "WHERE c.status = 'approved' ORDER BY c.approved_at, c.command_id LIMIT 1"
        )
        row = cur.fetchone()
    return (str(row[0]), row[1] or {}) if row else None


def run_once(conn: psycopg.Connection) -> str | None:
    """Expire what is stale, then act on at most one approved command: execute it, or refuse it if the policy engine says no. Returns its
    id, or None if there was nothing to do or the engine could not be asked (the command stays approved and is asked again later)."""
    command_repo.expire_stale(conn)
    conn.commit()
    job = next_approved(conn)
    if job is None:
        return None
    command_id, params = job
    if time.monotonic() < _gate_retry_after.get(command_id, 0.0):
        return None
    command = command_repo.get_command(conn, command_id)
    verdict = execution_gate(conn, command, None, current_geometry(conn) or "")
    if verdict.decision == "policy_unavailable":
        conn.commit()
        _gate_retry_after[command_id] = time.monotonic() + GATE_RETRY_S
        print(f"holding {command_id}: {verdict.message}", flush=True)
        return None
    if verdict.decision != "approved":
        note = f"refused at execution by policy {verdict.policy_version}: {verdict.message}"
        if verdict.decision == "expired":
            command_repo.transition_command(conn, command_id, "expired", EXECUTOR, note)
        else:
            command_repo.transition_command(
                conn,
                command_id,
                "denied",
                EXECUTOR,
                note,
                error_code=verdict.error_code,
                error_message=verdict.message,
                error_retryable=verdict.error_code == "stale_evidence",
                policy_decision="denied",
            )
        return command_id
    try:
        execute_command(conn, command_id, params, EXECUTOR)
    except (AdapterExecutionError, psycopg.Error, OSError):
        conn.rollback()
        traceback.print_exc()
        command = command_repo.get_command(conn, command_id)
        if command and command["status"] == "executing":
            command_repo.transition_command(
                conn,
                command_id,
                "failed",
                EXECUTOR,
                "the executor failed while running the adapter",
                error_code="internal",
                error_message="the executor could not complete the adapter run",
                error_retryable=True,
            )
    return command_id


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--database", default="aiops_demo")
    parser.add_argument(
        "--once", action="store_true", help="execute what is approved right now, then exit"
    )
    args = parser.parse_args()
    from backend.demo import world  # noqa: PLC0415

    world.load_platform_env()
    world.use_database(args.database)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    executed = 0
    while not stop.is_set():
        try:
            with psycopg.connect(dsn_from_env(role="svc_command_executor")) as conn:
                while not stop.is_set():
                    heartbeat(conn, SERVICE, {"executed_this_run": executed})
                    done = run_once(conn)
                    if done:
                        executed += 1
                        print(f"processed {done}", flush=True)
                    elif args.once:
                        return 0
                    stop.wait(POLL_S if not done else 0.2)
        except psycopg.OperationalError as exc:
            # The database restarted or went away. The heartbeat stops meanwhile, so platform status says so; reconnect rather than die.
            print(
                f"database unavailable ({exc.__class__.__name__}); reconnecting in {RECONNECT_S:.0f} s",
                flush=True,
            )
            stop.wait(RECONNECT_S)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
