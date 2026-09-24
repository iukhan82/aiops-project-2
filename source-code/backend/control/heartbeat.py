"""One row per platform service saying when it last reported it was alive (`service_heartbeats`, migration 0020)."""

from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb


def heartbeat(conn: psycopg.Connection, service: str, detail: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO service_heartbeats (service, last_seen, detail) VALUES (%s, now(), %s) "
            "ON CONFLICT (service) DO UPDATE SET last_seen = now(), detail = EXCLUDED.detail",
            (service, Jsonb(detail)),
        )
    conn.commit()
