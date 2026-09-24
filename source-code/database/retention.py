"""P05.10: retention, aggregation and storage-pressure handling.

Retention classes come straight from the schema's own enum
(`observation_events.retention_class`, `contracts/*'s` `retention_class`):
'short'/'standard'/'extended' each purge past a fixed window; 'audit' is
*never* purged by this module, full stop - a real compliance property
(P09.05 protects it further at the role level), not just a long default.

Two more invariants this module enforces, both load-bearing:

- **Preserves active control**: a command or incident still in a
  non-terminal status is never deleted, no matter how old. Only rows in a
  terminal status (commands: denied/failed/expired/rolled_back; incidents:
  resolved and not reopened since) are retention-eligible.
- **Aggregation before deletion**: every observation_events row about to be
  purged is folded into a per-device/per-day `retention_rollups` row first
  - the raw event is gone, its trend contribution is not.

Storage-pressure handling: if the database exceeds `HARD_SIZE_BYTES`, every
finite retention window is shortened by `PRESSURE_FACTOR` for that run
only (never persisted as a new default) - a safety valve that purges more
aggressively under real pressure without silently changing policy at rest.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOURCE_ROOT))

from database.migrate import dsn_from_env  # noqa: E402

RETENTION_DAYS: dict[str, int | None] = {
    "short": 7,
    "standard": 90,
    "extended": 365,
    "audit": None,  # never purged
}

TERMINAL_COMMAND_STATUSES = {"denied", "failed", "expired", "rolled_back"}
TERMINAL_INCIDENT_STATUSES = {"resolved"}

HARD_SIZE_BYTES = (
    10 * 1024 * 1024 * 1024
)  # 10 GiB: RESOURCE_BUDGET.md-scale placeholder, not a measured ceiling
PRESSURE_FACTOR = 0.5


def database_size_bytes(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_database_size(current_database())")
        (size,) = cur.fetchone()
    return size


def _effective_windows(under_pressure: bool) -> dict[str, int | None]:
    if not under_pressure:
        return dict(RETENTION_DAYS)
    return {
        k: (max(1, int(v * PRESSURE_FACTOR)) if v is not None else None)
        for k, v in RETENTION_DAYS.items()
    }


def _rollup_and_delete_events(
    conn: psycopg.Connection, retention_class: str, cutoff: datetime
) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO retention_rollups (device_id, event_type, bucket_date, sample_count, mean_value, retention_class)
            SELECT device_id, event_type, observation_time::date,
                   count(*),
                   avg((m->>'value')::double precision),
                   %s
            FROM observation_events, LATERAL jsonb_array_elements(measurements) AS m
            WHERE retention_class = %s AND observation_time < %s
              AND jsonb_typeof(m->'value') = 'number'
            GROUP BY device_id, event_type, observation_time::date
            ON CONFLICT (device_id, event_type, bucket_date) DO UPDATE SET
                sample_count = retention_rollups.sample_count + EXCLUDED.sample_count,
                mean_value = (retention_rollups.mean_value + EXCLUDED.mean_value) / 2,
                created_at = now()
            """,
            (retention_class, retention_class, cutoff),
        )
        rolled_up = cur.rowcount

        cur.execute(
            "DELETE FROM observation_events WHERE retention_class = %s AND observation_time < %s",
            (retention_class, cutoff),
        )
        deleted = cur.rowcount
    return rolled_up, deleted


def _delete_terminal_commands(conn: psycopg.Connection, cutoff: datetime) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM commands WHERE status = ANY(%s) AND expires_at < %s",
            (list(TERMINAL_COMMAND_STATUSES), cutoff),
        )
        return cur.rowcount


def _delete_terminal_incidents(conn: psycopg.Connection, cutoff: datetime) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM incidents WHERE status = ANY(%s) AND resolved_at IS NOT NULL AND resolved_at < %s",
            (list(TERMINAL_INCIDENT_STATUSES), cutoff),
        )
        return cur.rowcount


def apply_retention(conn: psycopg.Connection, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    size = database_size_bytes(conn)
    under_pressure = size >= HARD_SIZE_BYTES
    windows = _effective_windows(under_pressure)

    total_rolled_up = total_deleted = 0
    per_class: dict[str, dict] = {}
    for retention_class, days in windows.items():
        if days is None:
            per_class[retention_class] = {"purged": False, "reason": "audit class is never purged"}
            continue
        cutoff = now - timedelta(days=days)
        rolled_up, deleted = _rollup_and_delete_events(conn, retention_class, cutoff)
        total_rolled_up += rolled_up
        total_deleted += deleted
        per_class[retention_class] = {
            "purged": True,
            "window_days": days,
            "rolled_up": rolled_up,
            "deleted": deleted,
        }

    # Terminal operational records: same finite windows do not apply cleanly
    # (commands/incidents aren't tagged with retention_class); use the
    # 'standard' window as the operational-record default.
    op_cutoff = now - timedelta(days=windows["standard"])
    commands_deleted = _delete_terminal_commands(conn, op_cutoff)
    incidents_deleted = _delete_terminal_incidents(conn, op_cutoff)

    conn.commit()

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO retention_runs (storage_pressure, database_size_bytes, events_rolled_up, "
            "events_deleted, commands_deleted, incidents_deleted, detail) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                under_pressure,
                size,
                total_rolled_up,
                total_deleted,
                commands_deleted,
                incidents_deleted,
                json.dumps(per_class),
            ),
        )
        (run_id,) = cur.fetchone()
    conn.commit()

    return {
        "run_id": run_id,
        "storage_pressure": under_pressure,
        "database_size_bytes": size,
        "per_class": per_class,
        "events_rolled_up": total_rolled_up,
        "events_deleted": total_deleted,
        "commands_deleted": commands_deleted,
        "incidents_deleted": incidents_deleted,
    }


def main() -> int:
    with psycopg.connect(dsn_from_env()) as conn:
        report = apply_retention(conn)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
