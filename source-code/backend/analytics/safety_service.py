"""P06.05: safety-candidate detection over the platform's persisted telemetry.

Stalled-vehicle candidates come from the real EdgeRuntime replayed over loop
events read back out of `observation_events` (rebuilt into full envelopes) and
fused with P06.04's congestion episodes; overlay candidates (collision,
wrong-way, flooding, low visibility) come from their event-driven rules. All
are written to `detection_candidates` (never straight to incidents).
"""

from __future__ import annotations

import sys
import json
from datetime import datetime, timezone
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.congestion import Params, detect_congestion  # noqa: E402
from backend.analytics.congestion_service import load_artifact as load_congestion_artifact  # noqa: E402
from backend.analytics.congestion_service import store_candidates  # noqa: E402
from backend.analytics.kpi_service import load_segments  # noqa: E402
from backend.analytics.overlay_candidates import detect_overlay_candidates  # noqa: E402
from backend.analytics.stall_candidates import FusionParams, alarm_spans, fuse, run_edge_runtime  # noqa: E402

EDGE_MODEL = SOURCE_ROOT / "models" / "registry" / "traffic-safety-blockage" / "1.0.0"
EDGE_BASELINE = SOURCE_ROOT / "models" / "registry" / "baseline" / "baseline_v1.json"
FUSION_ARTIFACT = SOURCE_ROOT / "backend" / "analytics" / "artifacts" / "stall_fusion.json"
EVENT_TYPES = (
    "traffic.loop_detector.count",
    "road.condition_sensor.reading",
    "weather.station.reading",
)


def _ms_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def fetch_envelopes(
    conn: psycopg.Connection,
    start: datetime,
    end: datetime,
    event_types: tuple[str, ...] = EVENT_TYPES,
) -> list[dict]:
    """Full observation-envelope events (what schema validation and the edge
    runtime expect) rebuilt from `observation_events` rows."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT event_id, event_type, device_id, agency_scope, observation_time, ingest_time, sequence_number, clock_quality,
                   geometry_version, ST_Y(location::geometry), ST_X(location::geometry), intersection_id, corridor_id, lane_id,
                   measurements, truth_label, privacy_classification, retention_class, correlation_id, provenance
            FROM observation_events WHERE event_type = ANY(%s) AND observation_time > %s AND observation_time <= %s
            ORDER BY observation_time, device_id
            """,
            (list(event_types), start, end),
        )
        rows = cur.fetchall()
    out = []
    for (
        eid,
        et,
        dev,
        scope,
        obs,
        ing,
        seq,
        clk,
        geom,
        lat,
        lon,
        inter,
        corr,
        lane,
        meas,
        truth,
        priv,
        ret,
        cid,
        prov,
    ) in rows:
        loc = {"coordinate_reference": "EPSG:4326", "latitude": lat, "longitude": lon}
        loc.update(
            {
                k: v
                for k, v in (("intersection_id", inter), ("corridor_id", corr), ("lane_id", lane))
                if v
            }
        )
        e = {
            "schema_version": "1.0.0",
            "event_id": str(eid),
            "event_type": et,
            "device_id": dev,
            "agency_scope": scope,
            "observation_time": _ms_z(obs),
            "ingest_time": _ms_z(ing),
            "sequence_number": seq,
            "clock_quality": clk,
            "geometry_version": geom,
            "location": loc,
            "measurements": meas,
            "truth_label": truth,
            "privacy_classification": priv,
            "retention_class": ret,
        }
        if cid:
            e["correlation_id"] = cid
        if prov:
            e["provenance"] = prov
        out.append(e)
    return out


def load_fusion_params() -> FusionParams:
    return FusionParams(**json.loads(FUSION_ARTIFACT.read_text(encoding="utf-8"))["params"])


def detect_stall(
    conn: psycopg.Connection,
    start: datetime,
    end: datetime,
    geometry: str,
    registry_path: Path,
    store: bool = True,
) -> list[dict]:
    events = fetch_envelopes(conn, start, end, ("traffic.loop_detector.count",))
    segments = load_segments(conn, geometry)
    congestion = detect_congestion(events, segments, Params(**load_congestion_artifact()["params"]))
    derived = run_edge_runtime(events, registry_path, EDGE_BASELINE, EDGE_MODEL, "p06")
    candidates = fuse(alarm_spans(derived), congestion, load_fusion_params(), geometry)
    if store and candidates:
        store_candidates(conn, candidates)
    return candidates


def detect_overlays(
    conn: psycopg.Connection, start: datetime, end: datetime, geometry: str, store: bool = True
) -> list[dict]:
    events = fetch_envelopes(conn, start, end)
    segments = load_segments(conn, geometry)
    congestion = detect_congestion(events, segments, Params(**load_congestion_artifact()["params"]))
    candidates = detect_overlay_candidates(events, congestion, geometry)
    if store and candidates:
        store_candidates(conn, candidates)
    return candidates
