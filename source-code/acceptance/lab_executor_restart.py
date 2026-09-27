#!/usr/bin/env python3
"""P12.02 (REC-02): the command executor is killed while a command is executing - on restart the orphan is reconciled BEFORE anything new is executed.

    python source-code/acceptance/lab_executor_restart.py        # needs the platform stack (PostgreSQL, the policy engine) up; about a minute

The acceptance target reads: "central service restart: observed state reconciled before new commands are accepted". For the executor - the only thing that drives an adapter - that means a
command it was running when it died. A command moves to `executing` before its adapter is driven and to `executed` or `failed` after; a process killed in between leaves it `executing`,
and the state machine offers no other way out (found by this lab: nothing reconciled it, and the executor went on to the next command with the orphan still `executing`).

What runs: a real executor process (`executor_worker.run_once`) whose adapter blocks, killed with no warning while the command is `executing`; then a NEW executor's start-up. The adapter is
a stand-in (the SUMO runs are P07.06-P07.10); the database, the policy engine, the state machine and the executor code are the platform's own.
"""

from __future__ import annotations

import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from acceptance import lab_protected_actions as lab  # noqa: E402
from backend.control import executor_worker, simulator_adapters  # noqa: E402
from backend.control.command_service import request_command, review_command  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.repositories import commands as command_repo  # noqa: E402
from backend.roles import EXECUTOR  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

GEOMETRY = "2026-09-18.1"
STAMP = uuid.uuid4().hex[:8]
ev = Evidence("P12.02", "p12_02_rec02_executor_restart", docs_name="p12_02_rec02_executor_restart")

BLOCKED_EXECUTOR = """
import sys, time
sys.path.insert(0, {root!r})
import psycopg
from unittest.mock import patch
from backend.control import executor_worker, simulator_adapters
from database.migrate import dsn_from_env
with patch.object(simulator_adapters, "_run_in_wsl", lambda command, timeout: time.sleep(600)), psycopg.connect(dsn_from_env()) as conn:
    executor_worker.run_once(conn)
"""


def approved(conn: psycopg.Connection, tag: str, now: datetime) -> str:
    spec = lab.SPECS[2]  # the sign: SC-0, needs one approver
    command_id, _ = request_command(
        conn,
        f"p12-rec02-{STAMP}-{tag}",
        spec[1],
        spec[0],
        spec[2],
        lab.REQUESTER,
        now,
        "operator",
        ttl_s=lab.LONG,
    )
    status = review_command(
        conn, command_id, lab.APPROVER, "supervisor", now + timedelta(seconds=2), GEOMETRY
    )
    if status != "approved":
        raise RuntimeError(f"{tag} was not approved: {status}")
    return command_id


def transition_times(conn: psycopg.Connection, command_id: str) -> dict[str, datetime]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT to_status, min(changed_at) FROM command_transitions WHERE command_id = %s GROUP BY to_status",
            (command_id,),
        )
        return {status: at for status, at in cur.fetchall()}


def main() -> int:
    with (
        patch.object(simulator_adapters, "_run_in_wsl", lab.stand_in),
        psycopg.connect(dsn_from_env()) as conn,
    ):
        now = datetime.now(UTC)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM commands WHERE status IN ('approved', 'executing')")
            waiting = cur.fetchone()[0]
        ev.check(
            "precondition_no_command_is_waiting_for_the_executor",
            waiting == 0,
            f"{waiting} approved or executing",
        )
        if waiting:
            return ev.finish()
        lab.seed_evidence(conn, now)
        first = approved(
            conn, "first", now
        )  # the executor will be running this one when it is killed
        second = approved(
            conn, "second", now + timedelta(seconds=1)
        )  # and this one is waiting behind it

        victim = subprocess.Popen(
            [sys.executable, "-c", BLOCKED_EXECUTOR.format(root=str(SOURCE_ROOT))],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 60
        status = ""
        while time.time() < deadline:
            status = command_repo.get_command(conn, first)["status"]
            conn.commit()
            if status == "executing":
                break
            time.sleep(0.5)
        victim.kill()  # no warning: the process is gone mid-run
        victim.wait(timeout=15)
        conn.commit()
        after_kill = command_repo.get_command(conn, first)
        ev.check(
            "the_executor_process_was_killed_while_the_command_was_executing_and_left_it_executing",
            status == "executing"
            and after_kill["status"] == "executing"
            and victim.returncode != 0,
            f"status after the kill: {after_kill['status']}; process exit code {victim.returncode}",
        )

        # ---- what the executor did before this change: nothing reconciled it (probe: the reconciliation switched off)
        lab.calls.clear()
        with patch.object(executor_worker, "reconcile", lambda *_a, **_k: []):
            lab.drain(conn)
        stuck = command_repo.get_command(conn, first)
        ev.check(
            "without_reconciliation_the_orphan_stays_executing_for_ever_while_the_executor_carries_on_with_the_next_command",
            stuck["status"] == "executing"
            and command_repo.get_command(conn, second)["status"] == "executed",
            f"orphan {stuck['status']}, next command {command_repo.get_command(conn, second)['status']}",
        )
        third = approved(
            conn, "third", datetime.now(UTC)
        )  # arrives while the orphan is still there

        # ---- a NEW executor starts: it reconciles first
        lab.calls.clear()
        reconciled = executor_worker.reconcile(conn, at_start=True)
        lab.drain(conn)
        orphan = command_repo.get_command(conn, first)
        times_first, times_third = transition_times(conn, first), transition_times(conn, third)
        first_key = orphan["idempotency_key"]
        ev.check(
            "on_start_the_new_executor_reconciles_the_orphan_it_is_failed_not_executed_and_not_retried",
            first in reconciled
            and orphan["status"] == "failed"
            and orphan["error_retryable"] is False
            and orphan["error_code"] == "internal",
            f"reconciled {len(reconciled)}; status {orphan['status']}, retryable {orphan['error_retryable']}, error {orphan['error_code']}",
        )
        ev.check(
            "the_orphan_says_the_outcome_is_unknown_and_that_it_was_not_repeated",
            "unknown" in (orphan["error_message"] or "")
            and "not repeated" in (orphan["error_message"] or ""),
            orphan["error_message"] or "",
        )
        ev.check(
            "the_orphan_was_reconciled_before_the_next_command_was_executed",
            times_first["failed"] <= times_third["executing"],
            f"orphan failed at {times_first['failed']:%H:%M:%S.%f}, next command executing at {times_third['executing']:%H:%M:%S.%f}",
        )
        ev.check(
            "the_orphan_was_never_driven_again_the_adapter_was_not_called_for_it",
            lab.calls.get(first_key, 0) == 0,
            f"adapter calls for the orphan after the restart: {lab.calls.get(first_key, 0)}",
        )
        ev.check(
            "the_command_that_arrived_meanwhile_is_executed_exactly_once",
            command_repo.get_command(conn, third)["status"] == "executed"
            and lab.calls.get(command_repo.get_command(conn, third)["idempotency_key"], 0) == 1,
            command_repo.get_command(conn, third)["status"],
        )

        # ---- a command that is executing NOW, inside the adapter's time limit, is not touched by the running executor
        live, _ = request_command(
            conn,
            f"p12-rec02-{STAMP}-live",
            lab.SPECS[2][1],
            lab.SPECS[2][0],
            lab.SPECS[2][2],
            lab.REQUESTER,
            datetime.now(UTC),
            "operator",
            ttl_s=lab.LONG,
        )
        review_command(conn, live, lab.APPROVER, "supervisor", datetime.now(UTC), GEOMETRY)
        command_repo.transition_command(conn, live, "executing", EXECUTOR, "in flight")
        untouched = executor_worker.reconcile(conn)
        ev.check(
            "a_command_executing_within_the_adapters_time_limit_is_not_reconciled_while_the_executor_runs",
            live not in untouched and command_repo.get_command(conn, live)["status"] == "executing",
            f"orphans found: {untouched}",
        )
        command_repo.transition_command(
            conn, live, "executed", EXECUTOR, "completed", acknowledged=True
        )  # leave nothing behind
    ev.notes["scope"] = (
        "one executor process killed with no warning; the adapter is a stand-in, the database, policy engine, state machine and executor code are real; the REC-02 acceptance also names 'observed state reconciled' for the other central services, which the target restarts (P11.05) cover by their end-to-end checks"
    )
    ev.notes["not_proven"] = (
        "the independent outcome verifier looking at the network for a command that ended failed-unknown: an operator sees the failed command and its message; the verifier verifies only commands that ended executed"
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
