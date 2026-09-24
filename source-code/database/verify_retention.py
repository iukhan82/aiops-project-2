"""P05.10 acceptance evidence, run against the real P05.01/P05.04 Postgres:

    python source-code/database/verify_retention.py

Proves: retention_class-specific purge windows, 'audit' class is never
purged, aggregation happens before deletion (rollup row survives the raw
row), terminal operational records are purged past their window while
active (non-terminal) ones survive regardless of age, and storage-pressure
handling actually shortens the effective window (a real before/after
comparison on the same data, not just a config value).
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOURCE_ROOT))

from database import retention  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

GEOMETRY_VERSION = "2026-09-18.1"
TEST_DEVICE = "verify-retention-device"
OUTPUT_DIR = SOURCE_ROOT / "infra" / "platform" / "output"

results: dict[str, bool] = {}
notes: dict[str, str] = {}


def check(name: str, ok: bool, detail: str = "") -> None:
    results[name] = ok
    notes[name] = detail
    print(f"{'PASS' if ok else 'FAIL'}: {name} {detail}")


def insert_observation(conn: psycopg.Connection, obs_time: datetime, retention_class: str) -> str:
    event_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO observation_events (
                event_id, event_type, device_id, agency_scope, observation_time, ingest_time,
                sequence_number, clock_quality, geometry_version, location,
                measurements, truth_label, privacy_classification, retention_class, content_sha256
            ) VALUES (
                %s, 'traffic.loop_detector.count', %s, 'city-traffic-ops', %s, %s, %s, 'synced', %s,
                ST_SetSRID(ST_MakePoint(74.3587, 31.5204), 4326)::geography,
                %s, 'simulated', 'none', %s, %s
            )
            """,
            (
                event_id,
                TEST_DEVICE,
                obs_time,
                obs_time,
                int(obs_time.timestamp()),
                GEOMETRY_VERSION,
                json.dumps(
                    [
                        {
                            "name": "vehicle_count",
                            "value": 3,
                            "unit": "count",
                            "quality": "valid",
                            "confidence": 0.9,
                        }
                    ]
                ),
                retention_class,
                str(uuid.uuid4()),
            ),
        )
    conn.commit()
    return event_id


def event_exists(conn: psycopg.Connection, event_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM observation_events WHERE event_id = %s", (event_id,))
        return cur.fetchone() is not None


def rollup_exists(conn: psycopg.Connection, device_id: str, bucket_date) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM retention_rollups WHERE device_id = %s AND bucket_date = %s",
            (device_id, bucket_date),
        )
        return cur.fetchone() is not None


def main() -> int:
    with psycopg.connect(dsn_from_env()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO devices (device_id, device_type, deployment_type, agency_scope, geometry_version, "
                "location, status, registered_at, privacy_classification, retention_class) VALUES "
                "(%s, 'traffic_loop', 'simulated', 'city-traffic-ops', %s, "
                "ST_SetSRID(ST_MakePoint(74.3587, 31.5204), 4326)::geography, 'active', now(), 'none', 'standard') "
                "ON CONFLICT (device_id) DO NOTHING",
                (TEST_DEVICE, GEOMETRY_VERSION),
            )
        conn.commit()

        now = datetime.now(timezone.utc)
        future = now + timedelta(days=30)  # > 'short' window (7d), < 'standard' (90d)

        old_short = insert_observation(conn, now - timedelta(days=1), "short")
        old_audit = insert_observation(conn, now - timedelta(days=1), "audit")
        bucket_date = (now - timedelta(days=1)).date()

        report1 = retention.apply_retention(conn, now=future)
        check(
            "short_class_event_purged_past_its_window",
            not event_exists(conn, old_short),
            detail=str(report1["per_class"].get("short")),
        )
        check("audit_class_never_purged", event_exists(conn, old_audit))
        check(
            "aggregation_rollup_created_before_deletion",
            rollup_exists(conn, TEST_DEVICE, bucket_date),
        )

        # ---- terminal vs. active operational records ----
        from backend.repositories import commands, incidents  # noqa: E402

        terminal_cmd_id, _ = commands.create_command(
            conn,
            f"verify-retention-terminal-{time.time()}",
            "other",
            "x",
            "y",
            "verify-script",
            expires_in_seconds=1,
            policy_decision="denied",
        )
        commands.transition_command(
            conn,
            terminal_cmd_id,
            "denied",
            "verify-script",
            error_code="policy_denied",
            error_message="test",
            error_retryable=False,
        )
        active_cmd_id, _ = commands.create_command(
            conn,
            f"verify-retention-active-{time.time()}",
            "other",
            "x",
            "y",
            "verify-script",
            expires_in_seconds=1,
            policy_decision="pending",
        )

        resolved_incident_id = incidents.create_incident(
            conn,
            "hazard",
            "low",
            "lane",
            "verify-retention-lane",
            GEOMETRY_VERSION,
            ["33333333-3333-3333-3333-333333333333"],
            "OPS",
            0.5,
            "verify-script",
        )
        incidents.transition_incident(conn, resolved_incident_id, "resolved", "verify-script")
        active_incident_id = incidents.create_incident(
            conn,
            "hazard",
            "low",
            "lane",
            "verify-retention-lane",
            GEOMETRY_VERSION,
            ["44444444-4444-4444-4444-444444444444"],
            "OPS",
            0.5,
            "verify-script",
        )

        far_future = now + timedelta(days=200)  # past the 90-day operational-record window
        retention.apply_retention(conn, now=far_future)

        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM commands WHERE command_id = %s", (terminal_cmd_id,))
            terminal_cmd_gone = cur.fetchone() is None
            cur.execute("SELECT 1 FROM commands WHERE command_id = %s", (active_cmd_id,))
            active_cmd_survives = cur.fetchone() is not None
            cur.execute("SELECT 1 FROM incidents WHERE incident_id = %s", (resolved_incident_id,))
            resolved_incident_gone = cur.fetchone() is None
            cur.execute("SELECT 1 FROM incidents WHERE incident_id = %s", (active_incident_id,))
            active_incident_survives = cur.fetchone() is not None

        check("terminal_command_purged_past_window", terminal_cmd_gone)
        check("active_command_preserved_regardless_of_projected_age", active_cmd_survives)
        check("resolved_incident_purged_past_window", resolved_incident_gone)
        check("active_incident_preserved_regardless_of_projected_age", active_incident_survives)

        # ---- storage-pressure handling: same-age data, different outcome under pressure ----
        pressure_probe = insert_observation(conn, now - timedelta(days=50), "standard")
        # 50 days: survives the normal 90-day 'standard' window...
        report_normal = retention.apply_retention(conn, now=now)
        survived_normal = event_exists(conn, pressure_probe)

        original_threshold = retention.HARD_SIZE_BYTES
        retention.HARD_SIZE_BYTES = (
            0  # force the real (small) dev DB to register as "under pressure"
        )
        try:
            report_pressure = retention.apply_retention(conn, now=now)
        finally:
            retention.HARD_SIZE_BYTES = original_threshold
        purged_under_pressure = not event_exists(conn, pressure_probe)

        check(
            "storage_pressure_shortens_effective_window_and_purges_more",
            survived_normal
            and purged_under_pressure
            and report_pressure["storage_pressure"] is True
            and report_normal["storage_pressure"] is False,
            detail=f"normal_pressure={report_normal['storage_pressure']} forced_pressure={report_pressure['storage_pressure']}",
        )

    all_passed = all(results.values())
    evidence = {
        "task": "P05.10",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": results,
        "notes": notes,
        "all_passed": all_passed,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "p05_10_evidence.json"
    out_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f">> Evidence written to {out_path}")
    print("P05.10: ALL CHECKS PASSED" if all_passed else "P05.10: ONE OR MORE CHECKS FAILED")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
