"""P05.05: validated central ingestion and deduplication.

Consumes the Kafka-compatible backbone's `telemetry.events` topic (P05.03's
gateway is the only producer) and writes to `observation_events` (P05.04),
exactly-once-by-meaning: a Kafka offset is committed only after its event's
database write has committed, so a crash between the two simply reprocesses
that event on restart - safe only because every write path here is
idempotent by construction, not because reprocessing is rare.

Three ways an event is rejected outright (never written, recorded in
`ingestion_rejections` for audit):
- schema conflict: fails `contracts/observation-envelope/v1` (defense in
  depth - P05.03's gateway already checked this, but ingestion is a
  different trust boundary and must not assume upstream validation held).
- identity conflict: `device_id` does not reference a known `devices` row.
- content conflict: `event_id` already exists in `observation_events` with
  a *different* `content_sha256` - never silently overwritten.

A true replay (same `event_id`, same `content_sha256`) is accepted as an
idempotent no-op: this is what "identical replay is idempotent" means in
practice, not merely "does not crash."
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import jsonschema
import psycopg
from confluent_kafka import Consumer

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.kpis import parse_time  # noqa: E402
from backend.observability import BoundedCounter, BoundedHistogram, configure, traced  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

OBSERVATION_SCHEMA_PATH = SOURCE_ROOT / "contracts" / "observation-envelope" / "v1" / "schema.json"
KAFKA_TOPIC = "telemetry.events"

log = logging.getLogger("ingestion")
_metric_outcomes: dict[str, BoundedCounter] = {}
_metric_ingest_latency: BoundedHistogram | None = None


def _outcome_counter(outcome: str) -> BoundedCounter:
    counter = _metric_outcomes.get(outcome)
    if counter is None:
        counter = BoundedCounter(
            f"ingestion_events_{outcome}", f"Ingested events with outcome={outcome}"
        )
        _metric_outcomes[outcome] = counter
    return counter


def _ingest_latency_histogram() -> BoundedHistogram:
    """LAT-02: observation_time to durable central persistence. Recorded
    only for a genuinely new row (INSERTED) - a duplicate replay's
    observation_time says nothing about this run's own pipeline latency."""
    global _metric_ingest_latency
    if _metric_ingest_latency is None:
        _metric_ingest_latency = BoundedHistogram(
            "ingestion_ingest_latency",
            "Observation time to durable persistence (LAT-02)",
            unit="ms",
        )
    return _metric_ingest_latency


class Outcome(str, Enum):
    INSERTED = "inserted"
    DUPLICATE_OK = "duplicate_ok"
    REJECTED_SCHEMA = "rejected_schema"
    REJECTED_UNKNOWN_DEVICE = "rejected_unknown_device"
    REJECTED_CONTENT_CONFLICT = "rejected_content_conflict"


@dataclass(frozen=True)
class IngestResult:
    outcome: Outcome
    event_id: str | None
    detail: str = ""


def load_schema() -> dict:
    return json.loads(OBSERVATION_SCHEMA_PATH.read_text(encoding="utf-8"))


def content_hash(event: dict) -> str:
    canonical = json.dumps(event, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ingest_one(conn: psycopg.Connection, event: dict, schema: dict) -> IngestResult:
    """One event, one DB transaction, one of the five Outcome values.
    Caller controls commit (so it can be tied to a Kafka offset commit)."""
    precheck_event_id = event.get("event_id") if isinstance(event, dict) else None
    with traced(
        "ingestion.ingest_one",
        correlation_id=precheck_event_id,
        device_id=str(event.get("device_id")) if isinstance(event, dict) else "unknown",
    ):
        result = _ingest_one(conn, event, schema)
        _outcome_counter(result.outcome.value).add()
        return result


def _ingest_one(conn: psycopg.Connection, event: dict, schema: dict) -> IngestResult:
    try:
        jsonschema.validate(instance=event, schema=schema)
    except jsonschema.ValidationError as exc:
        event_id = event.get("event_id") if isinstance(event, dict) else None
        _record_rejection(conn, event_id, "schema_invalid", exc.message)
        return IngestResult(Outcome.REJECTED_SCHEMA, event_id, exc.message)

    event_id = event["event_id"]
    device_id = event["device_id"]

    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM devices WHERE device_id = %s", (device_id,))
        if cur.fetchone() is None:
            _record_rejection(conn, event_id, "unknown_device", device_id)
            return IngestResult(Outcome.REJECTED_UNKNOWN_DEVICE, event_id, device_id)

    chash = content_hash(event)
    location = event["location"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO observation_events (
                event_id, event_type, device_id, agency_scope, observation_time,
                ingest_time, sequence_number, clock_quality, geometry_version,
                location, intersection_id, corridor_id, lane_id, measurements,
                truth_label, privacy_classification, retention_class,
                correlation_id, provenance, content_sha256
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
            """,
            (
                event_id,
                event["event_type"],
                device_id,
                event["agency_scope"],
                event["observation_time"],
                event["ingest_time"],
                event["sequence_number"],
                event["clock_quality"],
                event["geometry_version"],
                location["longitude"],
                location["latitude"],
                location.get("intersection_id"),
                location.get("corridor_id"),
                location.get("lane_id"),
                json.dumps(event["measurements"]),
                event["truth_label"],
                event["privacy_classification"],
                event["retention_class"],
                event.get("correlation_id"),
                json.dumps(event["provenance"]) if event.get("provenance") else None,
                chash,
            ),
        )
        inserted = cur.fetchone() is not None

    if inserted:
        conn.commit()
        latency_ms = (
            datetime.now(timezone.utc) - parse_time(event["observation_time"])
        ).total_seconds() * 1000.0
        _ingest_latency_histogram().record(max(0.0, latency_ms))
        return IngestResult(Outcome.INSERTED, event_id)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT content_sha256 FROM observation_events WHERE event_id = %s", (event_id,)
        )
        (stored_hash,) = cur.fetchone()

    if stored_hash == chash:
        conn.commit()
        return IngestResult(Outcome.DUPLICATE_OK, event_id)

    _record_rejection(conn, event_id, "content_conflict", f"stored={stored_hash} incoming={chash}")
    return IngestResult(
        Outcome.REJECTED_CONTENT_CONFLICT, event_id, f"stored={stored_hash} incoming={chash}"
    )


def _record_rejection(
    conn: psycopg.Connection, event_id: str | None, reason: str, detail: str
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ingestion_rejections (event_id, reason, detail) VALUES (%s, %s, %s)",
            (event_id, reason, detail),
        )
    conn.commit()


def run(
    kafka_bootstrap: str,
    group_id: str = "ingestion",
    topic: str = KAFKA_TOPIC,
    max_messages: int | None = None,
) -> dict:
    """Consume-validate-write-commit loop. Returns outcome counts. Stops
    after `max_messages` when given (verification/tests); runs forever
    otherwise."""
    configure("ingestion")
    schema = load_schema()
    consumer = Consumer(
        {
            "bootstrap.servers": kafka_bootstrap,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([topic])
    counts: dict[str, int] = {}
    processed = 0
    try:
        with psycopg.connect(dsn_from_env()) as conn:
            while max_messages is None or processed < max_messages:
                msg = consumer.poll(1.0)
                if msg is None:
                    if max_messages is not None:
                        break
                    continue
                if msg.error():
                    log.warning("kafka error: %s", msg.error())
                    continue
                event = json.loads(msg.value())
                result = ingest_one(conn, event, schema)
                counts[result.outcome.value] = counts.get(result.outcome.value, 0) + 1
                consumer.commit(msg, asynchronous=False)
                processed += 1
    finally:
        consumer.close()
    return counts


def main() -> int:
    import os

    counts = run(kafka_bootstrap=os.environ.get("KAFKA_BOOTSTRAP", "127.0.0.1:19092"))
    print(counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
