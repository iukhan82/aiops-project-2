"""P05.06 acceptance evidence, run against the real P05.01/P05.04 Postgres.
Inserts real observation_events rows directly (state computation is a pure
read-side concern, independent of how rows got there - P05.05 already
proves the write path) and proves compute_state against them:

    python source-code/backend/state/verify_network_state.py
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jsonschema
import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.state.network_state import compute_state  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

GEOMETRY_VERSION = "2026-09-18.1"
TEST_LANE = "verify-network-state-lane"
SCHEMA = json.loads(
    (SOURCE_ROOT / "contracts" / "network-state" / "v1" / "schema.json").read_text(encoding="utf-8")
)
OUTPUT_DIR = SOURCE_ROOT / "infra" / "platform" / "output"

results: dict[str, bool] = {}
notes: dict[str, str] = {}


def check(name: str, ok: bool, detail: str = "") -> None:
    results[name] = ok
    notes[name] = detail
    print(f"{'PASS' if ok else 'FAIL'}: {name} {detail}")


def insert_event(
    conn: psycopg.Connection, obs_time: datetime, value: float, quality: str, confidence: float
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO observation_events (
                event_id, event_type, device_id, agency_scope, observation_time, ingest_time,
                sequence_number, clock_quality, geometry_version, location, lane_id,
                measurements, truth_label, privacy_classification, retention_class, content_sha256
            ) VALUES (
                %s, 'traffic.loop_detector.count', 'verify-network-state-device', 'city-traffic-ops',
                %s, %s, %s, 'synced', %s,
                ST_SetSRID(ST_MakePoint(74.3587, 31.5204), 4326)::geography, %s,
                %s, 'simulated', 'none', 'standard', %s
            )
            """,
            (
                str(uuid.uuid4()),
                obs_time,
                obs_time,
                int(obs_time.timestamp()),
                GEOMETRY_VERSION,
                TEST_LANE,
                json.dumps(
                    [
                        {
                            "name": "vehicle_count",
                            "value": value,
                            "unit": "count",
                            "quality": quality,
                            "confidence": confidence,
                        }
                    ]
                ),
                str(uuid.uuid4()),
            ),
        )
    conn.commit()


def main() -> int:
    with psycopg.connect(dsn_from_env()) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM observation_events WHERE lane_id = %s", (TEST_LANE,))
        conn.commit()

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO devices (device_id, device_type, deployment_type, agency_scope, geometry_version, "
                "location, lane_id, status, registered_at, privacy_classification, retention_class) VALUES "
                "('verify-network-state-device', 'traffic_loop', 'simulated', 'city-traffic-ops', %s, "
                "ST_SetSRID(ST_MakePoint(74.3587, 31.5204), 4326)::geography, %s, 'active', now(), 'none', 'standard') "
                "ON CONFLICT (device_id) DO NOTHING",
                (GEOMETRY_VERSION, TEST_LANE),
            )
        conn.commit()

        as_of = datetime(2026, 9, 19, 20, 0, 0, tzinfo=timezone.utc)

        # ---- never observed: must return None, not a contract-invalid record ----
        never = compute_state(
            conn,
            "lane",
            "no-such-lane-ever",
            window_seconds=60,
            max_staleness_seconds=60,
            as_of=as_of,
        )
        check("never_observed_returns_none", never is None)

        # ---- fresh, in-window samples ----
        insert_event(conn, as_of - timedelta(seconds=10), value=5, quality="valid", confidence=0.9)
        insert_event(conn, as_of - timedelta(seconds=5), value=7, quality="valid", confidence=0.9)
        state = compute_state(
            conn, "lane", TEST_LANE, window_seconds=60, max_staleness_seconds=30, as_of=as_of
        )
        check("fresh_window_returns_record", state is not None)
        m = next(m for m in state["measurements"] if m["name"] == "vehicle_count")
        check("aggregation_averages_in_window_values", m["value"] == 6.0, detail=str(m))
        check("sample_count_reflects_contributing_rows", m["sample_count"] == 2, detail=str(m))
        check("freshness_is_fresh_within_staleness_budget", state["freshness_status"] == "fresh")
        check(
            "observation_time_is_window_end_not_now",
            state["observation_time"].startswith("2026-09-19T20:00:00"),
        )
        check("geometry_version_preserved", state["geometry_version"] == GEOMETRY_VERSION)

        try:
            jsonschema.validate(instance=state, schema=SCHEMA)
            schema_ok = True
        except jsonschema.ValidationError as exc:
            schema_ok = False
            print("schema error:", exc.message)
        check("record_validates_against_real_contract_schema", schema_ok)

        # ---- stale: window has no samples, but an earlier one exists -> carried forward ----
        later = as_of + timedelta(seconds=120)
        stale_state = compute_state(
            conn, "lane", TEST_LANE, window_seconds=30, max_staleness_seconds=30, as_of=later
        )
        check("carried_forward_when_window_empty", stale_state is not None)
        cm = next(m for m in stale_state["measurements"] if m["name"] == "vehicle_count")
        check("carried_forward_sample_count_is_zero", cm["sample_count"] == 0, detail=str(cm))
        check("carried_forward_value_is_last_known", cm["value"] == 7, detail=str(cm))
        check("freshness_is_stale_beyond_budget", stale_state["freshness_status"] == "stale")

        # ---- quality/confidence aggregation: one suspect sample downgrades the window ----
        insert_event(
            conn, as_of - timedelta(seconds=2), value=10, quality="suspect", confidence=0.4
        )
        mixed = compute_state(
            conn, "lane", TEST_LANE, window_seconds=60, max_staleness_seconds=30, as_of=as_of
        )
        mm = next(m for m in mixed["measurements"] if m["name"] == "vehicle_count")
        check("worst_quality_wins_over_valid_samples", mm["quality"] == "suspect", detail=str(mm))

        # ---- segment: the lane's own edge; an unsupported type still refuses ----
        seg = compute_state(
            conn,
            "segment",
            "verify-network-state-lane".rsplit("_", 1)[0] or "x",
            window_seconds=60,
            max_staleness_seconds=30,
            as_of=as_of,
        )
        check("segment_with_no_matching_lane_ids_returns_none", seg is None)
        try:
            compute_state(
                conn, "zone", "x", window_seconds=60, max_staleness_seconds=30, as_of=as_of
            )
            not_impl = False
        except NotImplementedError:
            not_impl = True
        check("unsupported_element_type_raises_rather_than_fabricating", not_impl)

    all_passed = all(results.values())
    evidence = {
        "task": "P05.06",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": results,
        "notes": notes,
        "all_passed": all_passed,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "p05_06_evidence.json"
    out_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f">> Evidence written to {out_path}")
    print("P05.06: ALL CHECKS PASSED" if all_passed else "P05.06: ONE OR MORE CHECKS FAILED")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
