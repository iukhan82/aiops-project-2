"""P06.05: stalled-vehicle (road-blockage) candidates from multiple sources.

Sources, each a different kind of evidence about the same segment:
1. `edge_model` - P04's real ONNX road-blockage model run by the real
   `EdgeRuntime` (validation -> features -> inference -> alarm/abstain);
2. `loop_congestion` - P06.04's central congestion episode on that segment.

A candidate needs `k_min` consecutive edge alarms. Corroboration by a
congestion episode overlapping the alarm span raises confidence (noisy-OR);
`require_corroboration` makes it a precondition unless the model itself is
very sure (`solo_probability`). An edge *abstain* never raises a candidate.
Confidence is a fused score and is reported with the per-source parts so the
uncertainty stays inspectable rather than collapsed into one number.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from backend.analytics.congestion import Episode, episode_id
from backend.analytics.kpis import INTERVAL_SECONDS, parse_time
from backend.analytics.topology import edge_of_lane
from edge.replay import INGEST_LAG_S
from edge.runtime import EdgeConfig, EdgeRuntime, ListSink
from edge.timeutil import parse_ts

SOURCE = "fusion:stall/1"
MAX_ALARM_GAP_S = 60.0
CORROBORATION_SLACK_S = 120.0
CONGESTION_SOURCE_CONFIDENCE = 0.75  # P06.04 validation precision


@dataclass(frozen=True)
class FusionParams:
    k_min: int = 2
    require_corroboration: bool = False
    solo_probability: float = 0.9


@dataclass
class AlarmSpan:
    segment: str
    onset: datetime  # interval start of the first alarmed interval
    last: datetime  # end of the last alarmed interval
    probabilities: list[float] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


def run_edge_runtime(
    events: Iterable[dict],
    registry_path: Path,
    baseline_path: Path,
    model_dir: Path,
    run_label: str = "p06",
) -> list[dict]:
    """The real edge runtime over recorded telemetry (same loop as `edge.main replay`)."""
    config = EdgeConfig(
        site_id=run_label,
        runtime_device_id=f"edge-runtime-{run_label}",
        geometry_version="2026-09-18.1",
        registry_path=registry_path,
        baseline_path=baseline_path,
        model_dir=model_dir,
        boot_id=run_label,
    )
    sink = ListSink()
    runtime = EdgeRuntime.build(config, sink)
    ordered = sorted(
        events, key=lambda e: (e["observation_time"], e["device_id"], e["sequence_number"])
    )
    for _, group in itertools.groupby(ordered, key=lambda e: e["observation_time"]):
        tick = list(group)
        for e in tick:
            runtime.ingest(e, parse_ts(e["ingest_time"]) + timedelta(seconds=INGEST_LAG_S))
        runtime.evaluate_due(
            parse_ts(tick[0]["observation_time"]) + timedelta(seconds=config.eval_delay_s + 0.1)
        )
    return sink.events


def alarm_spans(derived: Iterable[dict]) -> list[AlarmSpan]:
    by_edge: dict[str, list[tuple]] = {}
    for e in derived:
        m = {x["name"]: x["value"] for x in e["measurements"]}
        if m.get("decision") != "alarm" or m.get("inference_mode") != "model":
            continue
        end = parse_time(e["observation_time"])
        by_edge.setdefault(edge_of_lane(e["location"]["lane_id"]), []).append(
            (
                end,
                float(m["blockage_probability"]),
                list(e.get("provenance", {}).get("source_event_ids", [])),
            )
        )
    spans: list[AlarmSpan] = []
    for edge, rows in by_edge.items():
        rows.sort(key=lambda r: r[0])
        current: AlarmSpan | None = None
        for end, p, ids in rows:
            if current is not None and (end - current.last).total_seconds() <= MAX_ALARM_GAP_S:
                current.last = end
                current.probabilities.append(p)
                current.evidence.extend(i for i in ids if i not in current.evidence)
            else:
                if current is not None:
                    spans.append(current)
                current = AlarmSpan(
                    edge, end - timedelta(seconds=INTERVAL_SECONDS), end, [p], list(ids)
                )
        if current is not None:
            spans.append(current)
    return sorted(spans, key=lambda s: (s.onset, s.segment))


def _corroborating(span: AlarmSpan, episodes: list[Episode]) -> Episode | None:
    slack = timedelta(seconds=CORROBORATION_SLACK_S)
    for ep in episodes:
        if ep.segment != span.segment:
            continue
        ep_end = ep.clear or (ep.last_seen or ep.detected_at)
        if ep.onset - slack <= span.last and span.onset - slack <= ep_end:
            return ep
    return None


def fuse(
    spans: list[AlarmSpan],
    episodes: list[Episode],
    params: FusionParams,
    geometry: str = "2026-09-18.1",
) -> list[dict]:
    out = []
    for s in spans:
        if len(s.probabilities) < params.k_min:
            continue
        p_edge = sum(s.probabilities) / len(s.probabilities)
        ep = _corroborating(s, episodes)
        if params.require_corroboration and ep is None and p_edge < params.solo_probability:
            continue
        sources = [
            {
                "source": "edge_model",
                "model": "traffic-safety-blockage/1.0.0",
                "alarms": len(s.probabilities),
                "mean_probability": round(p_edge, 4),
            }
        ]
        confidence = p_edge
        evidence = list(s.evidence)
        if ep is not None:
            sources.append(
                {
                    "source": "loop_congestion",
                    "candidate": episode_id("congestion", ep.segment, ep.onset),
                    "peak_occupancy": round(ep.peak_occupancy, 3),
                    "confidence": CONGESTION_SOURCE_CONFIDENCE,
                }
            )
            confidence = 1.0 - (1.0 - p_edge) * (1.0 - CONGESTION_SOURCE_CONFIDENCE)
            evidence += [i for i in ep.evidence if i not in evidence]
        confirm = s.onset + timedelta(seconds=INTERVAL_SECONDS * params.k_min)
        out.append(
            {
                "candidate_id": episode_id("stalled_vehicle", s.segment, s.onset),
                "kind": "stalled_vehicle",
                "network_element_type": "segment",
                "network_element_id": s.segment,
                "geometry_version": geometry,
                "onset_time": s.onset,
                "clear_time": None,
                "detected_at": max(confirm, s.onset),
                "severity": "high" if confidence >= 0.9 else "medium",
                "confidence": round(confidence, 4),
                "evidence_event_ids": evidence,
                "source": SOURCE,
                "attributes": {
                    "sources": sources,
                    "fusion_params": params.__dict__,
                    "corroborated": ep is not None,
                    "last_alarm_at": s.last.isoformat(),
                },
            }
        )
    return out
