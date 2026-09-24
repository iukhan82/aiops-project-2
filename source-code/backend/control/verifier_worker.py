"""P08.08: the outcome verifier - independent of everyone who touched the command.

    python source-code/backend/control/verifier_worker.py [--database aiops_demo] [--window-minutes 10]

`system:outcome-verifier` looks at each executed command once its "after" window has closed, measures the corridor's mean delay
before and after from the platform's own KPIs, and records what it found: effective, ineffective, unsafe (rolled back only when the
undo is physically confirmed) or unknown (escalated). The effectiveness and safety thresholds are not chosen: they come from the
corridor's own noise, the largest change between consecutive windows in the previous two hours, doubled - the rule P07.09 fixed
after measuring the noise on real simulator runs.

One limit is stated, not hidden: the traffic replayed in the demo world is a recorded run that does not react to commands, so
measured against it a command usually comes out "ineffective". The physical cause-and-effect proof (an action that helped, one
that made things worse and was undone) comes from the lock-step simulator runs of P07.09, not from this worker.
"""

from __future__ import annotations

import argparse
import math
import signal
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.control.heartbeat import heartbeat  # noqa: E402
from backend.control.outcome_verification import Metric, mean_corridor_metric, verify_and_rollback  # noqa: E402
from backend.roles import OUTCOME_VERIFIER  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

SERVICE = "outcome-verifier"
METRIC, UNIT = "delay_s", "s"
MIN_THRESHOLD_S = 5.0
POLL_S = 15.0
RECONNECT_S = 5.0


def corridor_of(
    conn: psycopg.Connection, adapter: str, entity: str
) -> tuple[str, str | None] | None:
    with conn.cursor() as cur:
        if adapter == "signal_controller_adapter":
            cur.execute(
                "SELECT corridor_id FROM intersections WHERE intersection_id = %s", (entity,)
            )
        else:
            cur.execute(
                "SELECT corridor_id, direction FROM network_segments WHERE edge_id = %s AND corridor_id IS NOT NULL",
                (entity,),
            )
        row = cur.fetchone()
    if row is None or row[0] is None:
        return None
    return row[0], (row[1] if len(row) > 1 else None)


def noise_threshold(
    conn: psycopg.Connection, corridor: str, direction: str | None, window: timedelta, now: datetime
) -> tuple[float, list[float]]:
    """Twice the largest change between consecutive equal windows over the previous two hours, never below a floor."""
    deltas, previous = [], None
    for step in range(12):
        end = now - window * step - window
        value = mean_corridor_metric(
            conn, corridor, METRIC, UNIT, (end - window, end), direction
        ).value
        if value is not None and previous is not None:
            deltas.append(previous - value)
        previous = value
    band = max((abs(d) for d in deltas), default=0.0)
    return max(MIN_THRESHOLD_S, math.ceil(2 * band)), deltas


def verify_ready(conn: psycopg.Connection, window: timedelta, now: datetime) -> list[str]:
    done = []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.command_id, c.target_adapter, c.target_entity_id, c.acknowledged_at FROM commands c "
            "WHERE c.status = 'executed' AND c.acknowledged_at IS NOT NULL AND NOT EXISTS (SELECT 1 FROM command_outcomes o WHERE o.command_id = c.command_id)"
        )
        candidates = cur.fetchall()
    for command_id, adapter, entity, acknowledged in candidates:
        post_window = (acknowledged, acknowledged + window)
        if post_window[1] > now:
            continue
        pre_window = (acknowledged - window, acknowledged)
        located = corridor_of(conn, adapter, entity)
        if located is None:
            continue
        corridor, direction = located
        pre = mean_corridor_metric(conn, corridor, METRIC, UNIT, pre_window, direction)
        post = mean_corridor_metric(conn, corridor, METRIC, UNIT, post_window, direction)
        threshold, deltas = noise_threshold(
            conn, corridor, direction, window, acknowledged - window
        )
        verify_and_rollback(
            conn,
            str(command_id),
            Metric(pre.name, pre.value, UNIT),
            Metric(post.name, post.value, UNIT),
            pre_window,
            post_window,
            OUTCOME_VERIFIER,
            now,
            safety_regression_threshold=threshold,
            effectiveness_improvement_threshold=threshold,
            higher_is_worse=True,
            undo=None,
            extra_detail={
                "corridor": corridor,
                "direction": direction,
                "basis": "platform corridor KPIs (mean delay per window)",
                "noise": {
                    "consecutive_window_deltas": [round(d, 2) for d in deltas],
                    "threshold_s": threshold,
                    "rule": "2 x the largest change between consecutive windows",
                },
                "limit": "the replayed traffic in the demo world does not react to commands",
            },
        )
        done.append(str(command_id))
    return done


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--database", default="aiops_demo")
    parser.add_argument("--window-minutes", type=float, default=10.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    from backend.demo import world  # noqa: PLC0415

    world.load_platform_env()
    world.use_database(args.database)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    window = timedelta(minutes=args.window_minutes)
    verified = 0
    while not stop.is_set():
        try:
            with psycopg.connect(dsn_from_env(role="svc_outcome_verifier")) as conn:
                while not stop.is_set():
                    heartbeat(conn, SERVICE, {"verified_this_run": verified})
                    for command_id in verify_ready(conn, window, datetime.now(timezone.utc)):
                        verified += 1
                        print(f"verified {command_id}", flush=True)
                    if args.once:
                        return 0
                    stop.wait(POLL_S)
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
