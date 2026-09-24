"""P05.04 acceptance evidence, run against the real P05.01 Postgres/PostGIS
container. Not a pytest suite: proving checksum-tamper detection means
temporarily rewriting a migration file on disk, which has no place in the
always-green unit test run. Run directly:

    python source-code/database/verify_migrate.py --reset

`--reset` drops and recreates the public schema first, so the proof starts
from nothing (safe here: this is the project's own dev/test Postgres
fixture, not a shared database anything else depends on yet). Without
--reset, only a plain (safe, non-destructive) migrate+seed run happens.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from database.migrate import ChecksumMismatch, dsn_from_env, migrate  # noqa: E402
from database.seeds import seed_topology  # noqa: E402

OUTPUT_DIR = SOURCE_ROOT / "infra" / "platform" / "output"

results: dict[str, bool] = {}
notes: dict[str, str] = {}


def check(name: str, ok: bool, detail: str = "") -> None:
    results[name] = ok
    notes[name] = detail
    print(f"{'PASS' if ok else 'FAIL'}: {name} {detail}")


def reset_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    conn.commit()


def table_names(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        return {r[0] for r in cur.fetchall()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    with psycopg.connect(dsn_from_env(), autocommit=False) as conn:
        if args.reset:
            reset_schema(conn)

        report1 = migrate(conn)
        expected_tables = {
            "schema_migrations",
            "geometry_versions",
            "intersections",
            "lanes",
            "devices",
            "observation_events",
            "commands",
            "command_outcomes",
            "incidents",
            "recommendations",
            "emergency_calls",
            "emergency_unit_assignments",
            # the operator UI's tables (0020) and the scenario-control trail (0009, made append-only by 0022)
            "incident_notes",
            "operator_audit",
            "shift_handovers",
            "service_heartbeats",
            "scenario_runs",
            "scenario_control_audit",
            "policy_decisions",  # every policy-engine decision about a command (0024, P09.03)
        }
        actual_tables = table_names(conn)
        check(
            "all_migrations_create_expected_tables",
            expected_tables.issubset(actual_tables),
            detail=f"missing={expected_tables - actual_tables}",
        )

        report2 = migrate(conn)
        check(
            "rerun_is_idempotent_no_new_applies",
            report2["applied"] == []
            and len(report2["already_applied"])
            == len(report1["applied"]) + len(report1["already_applied"]),
            detail=f"second run applied={report2['applied']}",
        )

        # ---- constraints: bad enum value and dangling FK must be rejected ----
        bad_enum_rejected = False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO commands (command_id, idempotency_key, action_type, target_adapter, "
                    "target_entity_id, requested_by, requested_at, expires_at, policy_decision, status) "
                    "VALUES (gen_random_uuid(), 'x', 'not_a_real_action_type', 'a', 'b', 'c', now(), now() + interval '1 hour', 'pending', 'requested')"
                )
            conn.commit()
        except psycopg.errors.CheckViolation:
            bad_enum_rejected = True
            conn.rollback()
        check("bad_enum_value_rejected_by_check_constraint", bad_enum_rejected)

        dangling_fk_rejected = False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO command_outcomes (outcome_id, command_id, pre_window_start, pre_window_end, "
                    "post_window_start, post_window_end, classification, verified_at, verifier, rollback_triggered) "
                    "VALUES (gen_random_uuid(), gen_random_uuid(), now(), now(), now(), now(), 'unknown', now(), 'x', false)"
                )
            conn.commit()
        except psycopg.errors.ForeignKeyViolation:
            dangling_fk_rejected = True
            conn.rollback()
        check("dangling_foreign_key_rejected", dangling_fk_rejected)

        expired_before_requested_rejected = False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO commands (command_id, idempotency_key, action_type, target_adapter, "
                    "target_entity_id, requested_by, requested_at, expires_at, policy_decision, status) "
                    "VALUES (gen_random_uuid(), 'y', 'other', 'a', 'b', 'c', now(), now() - interval '1 hour', 'pending', 'requested')"
                )
            conn.commit()
        except psycopg.errors.CheckViolation:
            expired_before_requested_rejected = True
            conn.rollback()
        check("expires_before_requested_rejected", expired_before_requested_rejected)

    # ---- checksum tamper detection (its own connection/transaction) ----
    migrations_dir = SOURCE_ROOT / "database" / "migrations"
    target = migrations_dir / "0001_topology.sql"
    original = target.read_text(encoding="utf-8")
    tamper_detected = False
    try:
        target.write_text(original + "\n-- tampered for P05.04 verification\n", encoding="utf-8")
        with psycopg.connect(dsn_from_env()) as conn2:
            try:
                migrate(conn2)
            except ChecksumMismatch:
                tamper_detected = True
    finally:
        target.write_text(original, encoding="utf-8")
    check("hand_edited_applied_migration_is_detected", tamper_detected)

    # ---- seed: real topology data, idempotent re-run ----
    with psycopg.connect(dsn_from_env()) as conn3:
        seed_report_1 = seed_topology.seed(conn3)
    with psycopg.connect(dsn_from_env()) as conn4:
        seed_report_2 = seed_topology.seed(conn4)
    check(
        "seed_runs_against_real_sumo_network",
        not seed_report_1.get("skipped", True)
        and seed_report_1.get("intersections_seeded", 0) == 12,
        detail=str(seed_report_1),
    )
    with psycopg.connect(dsn_from_env()) as conn5, conn5.cursor() as cur:
        cur.execute("SELECT count(*) FROM intersections")
        (count_after,) = cur.fetchone()
    check(
        "seed_rerun_is_idempotent",
        seed_report_2.get("intersections_seeded") == 12 and count_after == 12,
        detail=f"count_after_two_runs={count_after}",
    )

    all_passed = all(results.values())
    evidence = {
        "task": "P05.04",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": results,
        "notes": notes,
        "all_passed": all_passed,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "p05_04_evidence.json"
    out_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f">> Evidence written to {out_path}")
    print("P05.04: ALL CHECKS PASSED" if all_passed else "P05.04: ONE OR MORE CHECKS FAILED")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
