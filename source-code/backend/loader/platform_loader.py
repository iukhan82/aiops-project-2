"""Loads simulator output into the platform through its real write path.

Devices are registered into `devices` (identity is what ingestion checks), and
events go through P05.05's `ingest_one` - the same schema/identity/content-
conflict/idempotency checks live traffic gets - not a bulk INSERT that would
bypass them. It skips only the MQTT/Kafka transport, already proven in P05.03.
"""

from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.ingestion.ingest import ingest_one, load_schema  # noqa: E402


def register_devices(conn: psycopg.Connection, devices: Iterable[dict]) -> int:
    n = 0
    with conn.cursor() as cur:
        for d in devices:
            loc = d["location"]
            cur.execute(
                """
                INSERT INTO devices (device_id, device_type, deployment_type, agency_scope, geometry_version,
                    location, intersection_id, corridor_id, lane_id, capabilities, status, registered_at,
                    privacy_classification, retention_class)
                VALUES (%s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (device_id) DO NOTHING
                """,
                (
                    d["device_id"],
                    d["device_type"],
                    d["deployment_type"],
                    d["agency_scope"],
                    loc["geometry_version"],
                    loc["longitude"],
                    loc["latitude"],
                    loc.get("intersection_id"),
                    loc.get("corridor_id"),
                    loc.get("lane_id"),
                    d.get("capabilities", []),
                    d["status"],
                    d["registered_at"],
                    d["privacy_classification"],
                    d["retention_class"],
                ),
            )
            n += cur.rowcount
    conn.commit()
    return n


def reset_simulated_loops(conn: psycopg.Connection) -> int:
    """Removes previously loaded simulated loop events (and what was derived
    from them). Two simulated runs share one clock and the same loop devices,
    so loading a second run on top of the first would interleave two different
    traffic histories at identical timestamps - a test-fixture hazard, not
    something the platform's single-timeline data model supports."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM observation_events WHERE device_id LIKE 'loop-int-%'")
        deleted = cur.rowcount
        cur.execute("DELETE FROM detection_candidates")
    conn.commit()
    return deleted


def load_events(conn: psycopg.Connection, events: Iterable[dict]) -> Counter:
    schema = load_schema()
    outcomes: Counter = Counter()
    for event in events:
        outcomes[ingest_one(conn, event, schema).outcome.value] += 1
    return outcomes
