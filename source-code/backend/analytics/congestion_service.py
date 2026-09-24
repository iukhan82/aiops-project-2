"""P06.04: congestion/spillback detection over the platform's persisted
telemetry, emitting evidence-backed `detection_candidates` (not incidents).

Re-running over the same data replaces a candidate rather than duplicating it
(`candidate_id` is derived from kind, element and onset), which is also how an
episode that was still open on one run gets its `clear_time` on the next.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.congestion import (  # noqa: E402
    SOURCE,
    Episode,
    Params,
    Spillback,
    SpillParams,
    detect_congestion,
    detect_spillback,
    episode_id,
)
from backend.analytics.kpis import LOOP_EVENT_TYPE, iso_z  # noqa: E402
from backend.analytics.kpi_service import load_segments  # noqa: E402
from backend.observability import traced  # noqa: E402

ARTIFACT = SOURCE_ROOT / "backend" / "analytics" / "artifacts" / "congestion_params.json"


def load_artifact() -> dict:
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def fetch_loop_events_with_ids(
    conn: psycopg.Connection, start: datetime, end: datetime
) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT event_id, event_type, observation_time, lane_id, measurements FROM observation_events
            WHERE event_type = %s AND observation_time > %s AND observation_time <= %s
            ORDER BY observation_time, device_id
            """,
            (LOOP_EVENT_TYPE, start, end),
        )
        return [
            {
                "event_id": str(i),
                "event_type": t,
                "observation_time": iso_z(ts),
                "location": {"lane_id": lane},
                "measurements": m,
            }
            for i, t, ts, lane, m in cur.fetchall()
        ]


def to_candidates(
    episodes: list[Episode], spillbacks: list[Spillback], geometry: str, artifact: dict
) -> list[dict]:
    precision = artifact["severity_precision"]
    out = []
    for e in episodes:
        out.append(
            {
                "candidate_id": episode_id("congestion", e.segment, e.onset),
                "kind": "congestion",
                "network_element_type": "segment",
                "network_element_id": e.segment,
                "geometry_version": geometry,
                "onset_time": e.onset,
                "clear_time": e.clear,
                "detected_at": e.detected_at,
                "severity": e.severity,
                "confidence": precision[e.severity],
                "evidence_event_ids": e.evidence,
                "source": SOURCE,
                "attributes": {
                    "corridor_id": e.corridor_id,
                    "direction": e.direction,
                    "order": e.order,
                    "peak_occupancy": round(e.peak_occupancy, 3),
                    "min_speed_m_s": e.min_speed,
                    "duration_s": e.duration_s,
                    "detector_params": artifact["params"],
                },
            }
        )
    for s in spillbacks:
        out.append(
            {
                "candidate_id": episode_id("spillback", s.origin_segment, s.onset),
                "kind": "spillback",
                "network_element_type": "segment",
                "network_element_id": s.origin_segment,
                "geometry_version": geometry,
                "onset_time": s.onset,
                "clear_time": s.clear,
                "detected_at": s.detected_at,
                "severity": s.severity,
                "confidence": precision[s.severity],
                "evidence_event_ids": s.evidence,
                "source": SOURCE,
                "attributes": {
                    "corridor_id": s.corridor_id,
                    "direction": s.direction,
                    "upstream_segment": s.upstream_segment,
                    "inferred": s.inferred,
                    "spillback_params": artifact.get("spillback_params"),
                },
            }
        )
    return out


def store_candidates(conn: psycopg.Connection, candidates: list[dict]) -> None:
    with conn.cursor() as cur:
        for c in candidates:
            cur.execute(
                """
                INSERT INTO detection_candidates (candidate_id, kind, network_element_type, network_element_id, geometry_version,
                    onset_time, clear_time, detected_at, severity, confidence, evidence_event_ids, source, attributes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::uuid[], %s, %s)
                ON CONFLICT (candidate_id) DO UPDATE SET clear_time = EXCLUDED.clear_time, severity = EXCLUDED.severity,
                    confidence = EXCLUDED.confidence, evidence_event_ids = EXCLUDED.evidence_event_ids,
                    detected_at = LEAST(detection_candidates.detected_at, EXCLUDED.detected_at), attributes = EXCLUDED.attributes
                """,
                (
                    c["candidate_id"],
                    c["kind"],
                    c["network_element_type"],
                    c["network_element_id"],
                    c["geometry_version"],
                    c["onset_time"],
                    c["clear_time"],
                    c["detected_at"],
                    c["severity"],
                    c["confidence"],
                    c["evidence_event_ids"],
                    c["source"],
                    Jsonb(c["attributes"]),
                ),
            )
    conn.commit()


def detect_and_store(
    conn: psycopg.Connection, start: datetime, end: datetime, geometry: str, store: bool = True
) -> list[dict]:
    with traced(
        "analytics.congestion.detect_and_store",
        correlation_id=f"{geometry}:{iso_z(start)}:{iso_z(end)}",
    ) as span:
        artifact = load_artifact()
        segments = load_segments(conn, geometry)
        episodes = detect_congestion(
            fetch_loop_events_with_ids(conn, start, end), segments, Params(**artifact["params"])
        )
        spillbacks = detect_spillback(
            episodes, segments, SpillParams(**artifact["spillback_params"])
        )
        candidates = to_candidates(episodes, spillbacks, geometry, artifact)
        if store and candidates:
            store_candidates(conn, candidates)
        span.set_attribute("candidates_produced", len(candidates))
        return candidates
