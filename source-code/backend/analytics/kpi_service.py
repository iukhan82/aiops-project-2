"""P06.01: corridor KPI service over the platform's persisted telemetry.

Reads loop events back out of `observation_events` (P05.05's write path),
runs the same pure `compute_corridor_kpis` the datasets use, and upserts the
result into `corridor_kpis`. Upserting is idempotent by primary key
(corridor, direction, window, geometry), so recomputing a window - after late
events, a backfill or a restart - replaces it rather than duplicating it.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.kpis import (  # noqa: E402
    INTERVAL_SECONDS,
    LOOP_EVENT_TYPE,
    RELIABILITY_WINDOWS,
    WINDOW_SECONDS,
    compute_corridor_kpis,
    iso_z,
    window_floor,
)
from backend.analytics.topology import Segment  # noqa: E402


def load_segments(conn: psycopg.Connection, geometry_version: str) -> list[Segment]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT edge_id, from_node, to_node, corridor_id, direction, order_index, length_m, "
            "free_flow_speed_m_s, lane_share FROM network_segments WHERE geometry_version = %s ORDER BY edge_id",
            (geometry_version,),
        )
        return [Segment(*row) for row in cur.fetchall()]


def fetch_loop_events(conn: psycopg.Connection, start: datetime, end: datetime) -> list[dict]:
    """Minimal envelope dicts (what kpis.samples_from_events reads) for loop
    events whose observation_time is in (start, end]."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT event_type, observation_time, lane_id, measurements
            FROM observation_events
            WHERE event_type = %s AND observation_time > %s AND observation_time <= %s
            ORDER BY observation_time, device_id
            """,
            (LOOP_EVENT_TYPE, start, end),
        )
        return [
            {
                "event_type": t,
                "observation_time": iso_z(ts),
                "location": {"lane_id": lane},
                "measurements": m,
            }
            for t, ts, lane, m in cur.fetchall()
        ]


def compute_and_store(
    conn: psycopg.Connection,
    start: datetime,
    end: datetime,
    geometry_version: str,
    window_s: int = WINDOW_SECONDS,
) -> list[dict]:
    """KPIs for windows in [start, end); reads an extra reliability lookback
    so trailing-hour indices exist from the first requested window."""
    lookback = timedelta(seconds=window_s * RELIABILITY_WINDOWS)
    segments = load_segments(conn, geometry_version)
    events = fetch_loop_events(conn, window_floor(start, window_s) - lookback, end)
    kpis = [
        k
        for k in compute_corridor_kpis(
            events, segments, geometry_version, window_s, INTERVAL_SECONDS
        )
        if k["window_start"] >= iso_z(window_floor(start, window_s))
    ]
    store_kpis(conn, kpis)
    return kpis


def store_kpis(conn: psycopg.Connection, kpis: list[dict]) -> None:
    with conn.cursor() as cur:
        for k in kpis:
            cur.execute(
                """
                INSERT INTO corridor_kpis (corridor_id, direction, window_start, window_seconds, geometry_version,
                    kpis, quality, coverage, sample_count, segments_reporting, segments_expected)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (corridor_id, direction, window_start, window_seconds, geometry_version) DO UPDATE SET
                    kpis = EXCLUDED.kpis, quality = EXCLUDED.quality, coverage = EXCLUDED.coverage,
                    sample_count = EXCLUDED.sample_count, segments_reporting = EXCLUDED.segments_reporting,
                    segments_expected = EXCLUDED.segments_expected, computed_at = now()
                """,
                (
                    k["corridor_id"],
                    k["direction"],
                    k["window_start"],
                    k["window_seconds"],
                    k["geometry_version"],
                    Jsonb(k["kpis"]),
                    k["quality"],
                    k["coverage"],
                    k["sample_count"],
                    k["segments_reporting"],
                    k["segments_expected"],
                ),
            )
    conn.commit()


def stored_kpis(
    conn: psycopg.Connection, geometry_version: str, window_s: int = WINDOW_SECONDS
) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT corridor_id, direction, window_start, window_seconds, geometry_version, kpis, quality,
                   coverage, sample_count, segments_reporting, segments_expected
            FROM corridor_kpis WHERE geometry_version = %s AND window_seconds = %s
            ORDER BY window_start, corridor_id, direction
            """,
            (geometry_version, window_s),
        )
        return [
            {
                "corridor_id": c,
                "direction": d,
                "window_start": iso_z(w),
                "window_seconds": ws,
                "geometry_version": g,
                "kpis": k,
                "quality": q,
                "coverage": cov,
                "sample_count": sc,
                "segments_reporting": sr,
                "segments_expected": se,
            }
            for c, d, w, ws, g, k, q, cov, sc, sr, se in cur.fetchall()
        ]
