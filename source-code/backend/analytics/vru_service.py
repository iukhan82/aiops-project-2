"""P06.06: the platform path for pedestrian/cyclist conflict indicators.

    tracker frames -> deployed edge indicator (edge/vru_conflict.py) ->
    privacy-passed window aggregates (`vru.conflict.aggregate` events) ->
    P05.05 ingestion -> `pedestrian_conflict` detection candidates.

Only aggregates cross the edge boundary. A candidate carries the site, the
window, counts and a minimum predicted PET - no track, position or individual
speed exists anywhere downstream. Candidates are low-confidence by design (see
the evaluation: the indicator's event-level precision is modest) and are never
incidents; P06.07's correlation decides what they are worth.

Cyclist candidates are off unless `emit_cyclist_conflicts` is set: SUMO's
separated bike lanes give no real cyclist-vehicle conflict to validate against,
so the mode is not validated (docs in the evaluation report).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.congestion import episode_id  # noqa: E402
from backend.analytics.congestion_service import store_candidates  # noqa: E402
from backend.analytics.kpis import parse_time  # noqa: E402
from backend.analytics.vru_data import Run, Tracker, tracker_frames  # noqa: E402
from edge.vision_privacy import PrivacyGate, PrivacyZone  # noqa: E402
from edge.vru_conflict import (  # noqa: E402
    AGGREGATE_EVENT_TYPE,
    DEFAULT_MIN_COHORT,
    DEFAULT_WINDOW_S,
    ConflictAggregator,
    ConflictParams,
    PairScorer,
    SiteConflictDetector,
    SuppressedConflictWindow,
    suppression_event,
    to_observation_event,
)

ARTIFACT = SOURCE_ROOT / "backend" / "analytics" / "artifacts" / "vru_conflict.json"
SOURCE = "edge:vru_conflict/1"
PIPELINE_VERSION = "edge-vru-conflict/1"
ANCHOR = parse_time("2026-09-18T09:00:00Z")
CANDIDATE_KIND = {"pedestrian": "pedestrian_conflict", "cyclist": "cyclist_conflict"}


def load_artifact() -> dict:
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def deployed_detector() -> tuple[ConflictParams, PairScorer | None]:
    artifact = load_artifact()
    return ConflictParams(**artifact["params"]), (
        PairScorer(artifact["scorer"]) if artifact["scorer"] else None
    )


def run_edge(
    run: Run,
    tracker: Tracker,
    devices: dict[str, dict],
    run_id: str,
    zones: list[PrivacyZone] | None = None,
    salt: bytes | None = None,
    window_s: float = DEFAULT_WINDOW_S,
    min_cohort: int = DEFAULT_MIN_COHORT,
    emit_cyclist_conflicts: bool = False,
) -> dict:
    """Runs the deployed indicator over one run as an edge camera per site would, and
    returns the events it would publish plus counters for what the privacy layer withheld."""
    params, scorer = deployed_detector()
    gate = PrivacyGate(zones or [], window_s, salt)
    aggregator = ConflictAggregator(gate, ANCHOR, min_cohort)
    raw_events = 0
    for site, frames in sorted(tracker_frames(run, tracker).items()):
        detector = SiteConflictDetector(site, params, scorer)
        for t in sorted(frames):
            admitted = [s for s in frames[t] if aggregator.admit_sample(s)]
            for s in admitted:
                if s.object_class in ("pedestrian", "cyclist"):
                    aggregator.note_exposure(site, s.object_class, s.track_id, t)
            detector.update(t, admitted)
        for event in detector.events:
            raw_events += 1
            if event.mode == "pedestrian" or emit_cyclist_conflicts:
                aggregator.note_conflict(event)
    published: list[dict] = []
    suppressed = 0
    sequence: dict[tuple[str, str], int] = {}
    for result in aggregator.close_all():
        if result.mode == "cyclist" and not emit_cyclist_conflicts:
            continue
        if isinstance(result, SuppressedConflictWindow):
            suppressed += 1
            builder = suppression_event
        else:
            builder = to_observation_event
        device = devices[f"camera-{result.site}"]
        key = (device["device_id"], result.mode)
        sequence[key] = sequence.get(key, 0) + 1
        published.append(
            builder(result, device, run_id, sequence[key], "2026-09-18.1", PIPELINE_VERSION)
        )
    published.sort(key=lambda e: (e["observation_time"], e["device_id"], e["sequence_number"]))
    return {
        "events": published,
        "raw_events": raw_events,
        "zone_suppressed_samples": aggregator.zone_suppressed_samples,
        "suppressed_windows": suppressed,
    }


def fetch_aggregates(conn: psycopg.Connection, start: datetime, end: datetime) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT event_id, device_id, intersection_id, observation_time, measurements
            FROM observation_events WHERE event_type = %s AND observation_time > %s AND observation_time <= %s
            ORDER BY observation_time, device_id
            """,
            (AGGREGATE_EVENT_TYPE, start, end),
        )
        return [
            {
                "event_id": str(r[0]),
                "device_id": r[1],
                "site": r[2],
                "window_end": r[3],
                "measurements": {m["name"]: m["value"] for m in r[4]},
            }
            for r in cur.fetchall()
        ]


def to_candidates(
    aggregates: list[dict],
    geometry: str,
    artifact: dict,
    window_s: float = DEFAULT_WINDOW_S,
    emit_cyclist: bool = False,
) -> list[dict]:
    precision = artifact["window_precision"]
    out = []
    for a in aggregates:
        for mode, kind in CANDIDATE_KIND.items():
            if mode == "cyclist" and not emit_cyclist:
                continue
            serious = int(a["measurements"].get(f"conflicts_serious_{mode}", 0))
            severe = int(a["measurements"].get(f"conflicts_severe_{mode}", 0))
            n = serious + severe
            if n == 0:
                continue
            onset = a["window_end"] - timedelta(seconds=window_s)
            confidence = min(0.9, precision)
            out.append(
                {
                    "candidate_id": episode_id(kind, a["site"], onset),
                    "kind": kind,
                    "network_element_type": "intersection",
                    "network_element_id": a["site"],
                    "geometry_version": geometry,
                    "onset_time": onset,
                    "clear_time": a["window_end"],
                    "detected_at": a["window_end"],
                    "severity": "high" if severe else "medium",
                    "confidence": round(confidence, 4),
                    "evidence_event_ids": [a["event_id"]],
                    "source": SOURCE,
                    "attributes": {
                        "conflicts_serious": serious,
                        "conflicts_severe": severe,
                        "exposure": int(a["measurements"].get(f"exposure_{mode}", 0)),
                        "min_predicted_pet_s": a["measurements"].get(f"min_predicted_pet_{mode}"),
                        "window_s": window_s,
                        "k_anonymity_floor": DEFAULT_MIN_COHORT,
                        "window_precision": precision,
                        "event_precision": artifact["event_precision"],
                        "confidence_basis": "validation precision of flagged site x window cells (the unit this candidate is); capped at 0.9",
                        "granularity": "site x window aggregate; no track, position or individual speed exists",
                    },
                }
            )
    return out


def detect_and_store(
    conn: psycopg.Connection, start: datetime, end: datetime, geometry: str, store: bool = True
) -> list[dict]:
    candidates = to_candidates(fetch_aggregates(conn, start, end), geometry, load_artifact())
    if store and candidates:
        store_candidates(conn, candidates)
    return candidates
