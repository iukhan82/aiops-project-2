"""P05.05 acceptance evidence, run against the real P05.01/P05.04 stack
(Kafka + Postgres). Produces directly onto the real `telemetry.events`
topic (bypassing P05.03's gateway - ingestion's job is to consume that
topic correctly regardless of how events arrived on it) and proves, against
the real database, all three rejection paths plus idempotent replay:

    python source-code/backend/ingestion/verify_ingest.py
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import psycopg
from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient, NewTopic

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.ingestion.ingest import run  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

KAFKA_BOOTSTRAP = "127.0.0.1:19092"
GEOMETRY_VERSION = "2026-09-18.1"
KNOWN_DEVICE = "verify-ingest-known-device"
OUTPUT_DIR = SOURCE_ROOT / "infra" / "platform" / "output"
# A dedicated topic, not the real `telemetry.events` P05.03 produces onto -
# reset on every run so re-running this script stays idempotent (a fresh
# consumer group must see exactly *this* run's messages, not a prior run's).
VERIFY_TOPIC = "telemetry.events.p05_05_verify"


def reset_topic(topic: str) -> None:
    admin = AdminClient({"bootstrap.servers": KAFKA_BOOTSTRAP})
    try:
        for f in admin.delete_topics([topic]).values():
            try:
                f.result(10)
            except Exception:
                pass
    except Exception:
        pass
    time.sleep(1)
    for f in admin.create_topics(
        [NewTopic(topic, num_partitions=1, replication_factor=1)]
    ).values():
        f.result(10)


results: dict[str, bool] = {}
notes: dict[str, str] = {}


def check(name: str, ok: bool, detail: str = "") -> None:
    results[name] = ok
    notes[name] = detail
    print(f"{'PASS' if ok else 'FAIL'}: {name} {detail}")


def make_event(device_id: str, seq: int, event_id: str | None = None, count: int = 1) -> dict:
    return {
        "schema_version": "1.0.0",
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": "traffic.loop_detector.count",
        "device_id": device_id,
        "agency_scope": "city-traffic-ops",
        "observation_time": "2026-09-19T19:00:00Z",
        "ingest_time": "2026-09-19T19:00:00Z",
        "sequence_number": seq,
        "clock_quality": "synced",
        "geometry_version": GEOMETRY_VERSION,
        "location": {
            "coordinate_reference": "EPSG:4326",
            "latitude": 31.5204,
            "longitude": 74.3587,
        },
        "measurements": [
            {
                "name": "vehicle_count",
                "value": count,
                "unit": "count",
                "quality": "valid",
                "confidence": 0.95,
            }
        ],
        "truth_label": "simulated",
        "privacy_classification": "none",
        "retention_class": "standard",
    }


def ensure_known_device(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO devices (
                device_id, device_type, deployment_type, agency_scope, geometry_version,
                location, status, registered_at, privacy_classification, retention_class
            ) VALUES (
                %s, 'traffic_loop', 'simulated', 'city-traffic-ops', %s,
                ST_SetSRID(ST_MakePoint(74.3587, 31.5204), 4326)::geography,
                'active', now(), 'none', 'standard'
            )
            ON CONFLICT (device_id) DO NOTHING
            """,
            (KNOWN_DEVICE, GEOMETRY_VERSION),
        )
    conn.commit()


def produce(events: list[dict]) -> None:
    producer = Producer({"bootstrap.servers": KAFKA_BOOTSTRAP})
    for ev in events:
        producer.produce(VERIFY_TOPIC, key=ev["device_id"].encode(), value=json.dumps(ev).encode())
    producer.flush(10)


def row_count(conn: psycopg.Connection, sql: str, params: tuple = ()) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        (n,) = cur.fetchone()
    return n


def main() -> int:
    reset_topic(VERIFY_TOPIC)
    with psycopg.connect(dsn_from_env()) as conn:
        ensure_known_device(conn)

    valid_event = make_event(KNOWN_DEVICE, seq=1)
    valid_event_id = valid_event["event_id"]
    duplicate_of_valid = dict(valid_event)  # identical content, same event_id

    conflicting = make_event(
        KNOWN_DEVICE, seq=1, event_id=valid_event_id, count=999
    )  # same id, different content

    schema_invalid = make_event(KNOWN_DEVICE, seq=2)
    del schema_invalid["measurements"]

    unknown_device = make_event("this-device-was-never-registered", seq=3)

    group = f"verify-ingest-{uuid.uuid4().hex}"
    produce([valid_event, duplicate_of_valid, conflicting, schema_invalid, unknown_device])
    time.sleep(1)
    counts = run(KAFKA_BOOTSTRAP, group_id=group, topic=VERIFY_TOPIC, max_messages=5)
    print("first pass counts:", counts)

    check("valid_event_inserted", counts.get("inserted") == 1, detail=str(counts))
    check("identical_replay_is_idempotent", counts.get("duplicate_ok") == 1, detail=str(counts))
    check(
        "content_conflict_rejected",
        counts.get("rejected_content_conflict") == 1,
        detail=str(counts),
    )
    check("schema_conflict_rejected", counts.get("rejected_schema") == 1, detail=str(counts))
    check(
        "identity_conflict_rejected", counts.get("rejected_unknown_device") == 1, detail=str(counts)
    )

    with psycopg.connect(dsn_from_env()) as conn:
        stored_rows = row_count(
            conn, "SELECT count(*) FROM observation_events WHERE event_id = %s", (valid_event_id,)
        )
        check(
            "exactly_one_row_survives_conflict_attempt",
            stored_rows == 1,
            detail=f"rows={stored_rows}",
        )

        stored_count = row_count(
            conn,
            "SELECT (measurements->0->>'value')::int FROM observation_events WHERE event_id = %s",
            (valid_event_id,),
        )
        check(
            "original_content_not_overwritten_by_conflict",
            stored_count == 1,
            detail=f"stored value={stored_count}",
        )

        rejections = row_count(
            conn,
            "SELECT count(*) FROM ingestion_rejections WHERE event_id = %s OR detail LIKE %s",
            (valid_event_id, f"%{valid_event_id}%"),
        )
        check(
            "content_conflict_recorded_in_rejections",
            rejections >= 1,
            detail=f"rejection rows={rejections}",
        )

    # ---- full topic replay: a brand-new consumer group re-reads every message from the start ----
    fresh_group_replay = run(
        KAFKA_BOOTSTRAP, group_id=f"{group}-replay", topic=VERIFY_TOPIC, max_messages=5
    )
    print("fresh-group full replay counts:", fresh_group_replay)
    # Both `valid_event` and its explicit `duplicate_of_valid` copy share the
    # same event_id and content, so a fresh consumer group correctly resolves
    # *each* of those two messages as duplicate_ok (2), not 1 - nothing was
    # ever re-inserted (`inserted` stays 0).
    check(
        "full_topic_replay_from_new_consumer_group_is_idempotent",
        fresh_group_replay.get("inserted", 0) == 0
        and fresh_group_replay.get("duplicate_ok", 0) == 2
        and fresh_group_replay.get("rejected_content_conflict", 0) == 1,
        detail=str(fresh_group_replay),
    )

    with psycopg.connect(dsn_from_env()) as conn:
        final_rows = row_count(
            conn, "SELECT count(*) FROM observation_events WHERE event_id = %s", (valid_event_id,)
        )
        check("replay_never_duplicates_the_row", final_rows == 1, detail=f"rows={final_rows}")

    all_passed = all(results.values())
    evidence = {
        "task": "P05.05",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": results,
        "notes": notes,
        "all_passed": all_passed,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "p05_05_evidence.json"
    out_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f">> Evidence written to {out_path}")
    print("P05.05: ALL CHECKS PASSED" if all_passed else "P05.05: ONE OR MORE CHECKS FAILED")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
