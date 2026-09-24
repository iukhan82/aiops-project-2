"""P06.08 (part 1): incident-level evaluation of the whole traffic-intelligence path.

    python source-code/backend/analytics/evaluate_intelligence.py [--dry-run]

detectors (congestion, spillback, edge-model stall fusion) -> candidates ->
correlation/opening policy -> incidents, scored against SUMO's own measured
truth: physical blockages (stop-output) and standing-queue episodes
(edge-wide waiting vehicles). An incident is a true positive when one of its
member candidates relates (same event, duplicate source, or consequence on
the graph) to a truth event; FA-01 is the share of raised incidents that are
not. Reported per scenario class - never blended (ACC-03) - with Wilson 95%
intervals. The opening policy was fixed before any TEST scoring; TEST is
scored once (ledgered non-selecting `incident_report`).
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics import evaluate_stall as es  # noqa: E402
from backend.analytics.congestion import SpillParams, detect_spillback, truth_episodes, wilson  # noqa: E402
from backend.analytics.congestion_service import load_artifact as load_congestion  # noqa: E402
from backend.analytics.congestion_service import to_candidates  # noqa: E402
from backend.analytics.correlation import (  # noqa: E402
    Cand,
    Policy,
    Topology,
    link,
    load_policy,
    simulate_openings,
)  # noqa: E402
from backend.analytics.evaluate_congestion import ANCHOR, DATASET  # noqa: E402
from backend.analytics.kpis import parse_time  # noqa: E402
from backend.analytics.stall_candidates import FusionParams, fuse  # noqa: E402
from models.evaluation.test_gate import record_opening  # noqa: E402

GEOMETRY = "2026-09-18.1"
REGISTRY = SOURCE_ROOT / "models" / "registry" / "intelligence-eval"
FEATURE_VERSION = "incident-eval/1"
STALL_CLEAR_AFTER_S = 120.0
RUN_END = ANCHOR + timedelta(seconds=10800)


def to_cand(d: dict, time_of: dict[str, datetime]) -> Cand:
    attrs = d.get("attributes", {})
    end = d["clear_time"]
    if d["kind"] == "stalled_vehicle" and end is None and attrs.get("last_alarm_at"):
        end = es.parse_last(d) + timedelta(seconds=STALL_CLEAR_AFTER_S)
    seen = max([time_of[e] for e in d["evidence_event_ids"] if e in time_of] + [d["detected_at"]])
    if d["kind"] == "stalled_vehicle" and attrs.get("last_alarm_at"):
        seen = max(seen, es.parse_last(d))
    return Cand(
        d["candidate_id"],
        d["kind"],
        d["network_element_type"],
        d["network_element_id"],
        d["onset_time"],
        end,
        d["detected_at"],
        d["severity"],
        d["confidence"],
        d["source"],
        "loop_detector",
        bool(attrs.get("corroborated", False)),
        tuple(d["evidence_event_ids"]),
        attrs,
        seen,
    )


def truth_cands(run: dict) -> list[Cand]:
    out = []
    for i, (edge, on, end, _dist) in enumerate(run["incidents"]):
        out.append(
            Cand(
                f"truth-blockage-{i}",
                "stalled_vehicle",
                "segment",
                edge,
                on,
                end,
                on,
                "high",
                1.0,
                "truth",
                "truth",
                False,
                (),
                {},
                end,
            )
        )
    for i, t in enumerate(run["truth_queues"]):
        out.append(
            Cand(
                f"truth-queue-{i}",
                "congestion",
                "segment",
                t.segment,
                t.onset,
                t.end,
                t.onset,
                t.severity,
                1.0,
                "truth",
                "truth",
                False,
                (),
                {},
                t.end,
            )
        )
    return out


def prepare_all(splits: tuple[str, ...]) -> tuple[dict, list]:
    runs = es.load_runs(splits)
    es.init_adjacency(runs["segments"])
    data = es.prepare(runs, splits)
    for split in splits:
        for src, dst in zip(runs[split], data[split], strict=True):
            dst["events"] = src["events"]
            dst["truth_queues"] = truth_episodes(src["truth"], runs["segments"], ANCHOR)
    return data, runs["segments"]


def score_run(
    run: dict, segments, policy: Policy, fusion: FusionParams, artifact: dict, topo: Topology
) -> dict:
    time_of = {e["event_id"]: parse_time(e["observation_time"]) for e in run["events"]}
    spill = detect_spillback(run["episodes"], segments, SpillParams(**artifact["spillback_params"]))
    dicts = to_candidates(run["episodes"], spill, GEOMETRY, artifact) + fuse(
        run["spans"], run["episodes"], fusion, GEOMETRY
    )
    cands = [to_cand(d, time_of) for d in dicts]
    incidents = simulate_openings(cands, topo, policy)
    truth = truth_cands(run)
    by_id = {c.candidate_id: c for c in cands}

    def related(cand: Cand, t: Cand) -> bool:
        return link(cand, t, topo, policy, RUN_END) is not None

    rows, covered_by, latencies = [], defaultdict(list), []
    for inc in incidents:
        members = [by_id[i] for i in inc.members]
        hits = [t for t in truth if any(related(m, t) for m in members)]
        # a live pipeline only has evidence up to the moment it confirms detection; a congestion episode's
        # `evidence` list keeps growing after detected_at (it accumulates until clear), so evidence timestamped
        # after opened_at is not what actually triggered this incident and must not count toward its latency
        last_evidence = max(
            (
                t
                for e in inc.opening_evidence
                if (t := time_of.get(e)) is not None and t <= inc.opened_at
            ),
            default=inc.opened_at,
        )
        latencies.append((inc.opened_at - last_evidence).total_seconds())
        rows.append(
            {
                "opened_at": inc.opened_at,
                "type": inc.primary.kind,
                "element": inc.primary.element_id,
                "confidence": inc.confidence,
                "true_positive": bool(hits),
                "kinds": sorted({m.kind for m in members}),
                "latency_s": latencies[-1],
            }
        )
        for t in hits:
            covered_by[t.candidate_id].append(inc.opened_at)
    blockages = [t for t in truth if t.candidate_id.startswith("truth-blockage")]
    queues = [t for t in truth if t.candidate_id.startswith("truth-queue")]
    delays = {"blockage": [], "queue": []}
    for t in truth:
        if t.candidate_id in covered_by:
            delays["blockage" if t in blockages else "queue"].append(
                (min(covered_by[t.candidate_id]) - t.onset).total_seconds()
            )
    return {
        "incidents": rows,
        "candidates": len(cands),
        "blockages": len(blockages),
        "blockages_covered": sum(1 for t in blockages if t.candidate_id in covered_by),
        "queues": len(queues),
        "queues_covered": sum(1 for t in queues if t.candidate_id in covered_by),
        "delays": delays,
        "latencies_s": latencies,
    }


def summarise(per_run: list[dict]) -> dict:
    n = sum(len(r["incidents"]) for r in per_run)
    tp = sum(1 for r in per_run for i in r["incidents"] if i["true_positive"])
    fp = n - tp
    delays = {k: sorted(x for r in per_run for x in r["delays"][k]) for k in ("blockage", "queue")}
    latencies = sorted(x for r in per_run for x in r["latencies_s"])

    def stat(xs: list[float], q: float) -> float | None:
        return xs[int(q * (len(xs) - 1))] if xs else None

    return {
        "runs": len(per_run),
        "candidates": sum(r["candidates"] for r in per_run),
        "incidents_raised": n,
        "true_positive": tp,
        "false_positive": fp,
        "false_positive_rate": round(fp / n, 3) if n else None,
        "false_positive_rate_ci95": wilson(fp, n),
        "measured_blockages": sum(r["blockages"] for r in per_run),
        "blockages_covered": sum(r["blockages_covered"] for r in per_run),
        "blockage_recall": round(
            sum(r["blockages_covered"] for r in per_run)
            / max(1, sum(r["blockages"] for r in per_run)),
            3,
        ),
        "blockage_recall_ci95": wilson(
            sum(r["blockages_covered"] for r in per_run), sum(r["blockages"] for r in per_run)
        ),
        "truth_queue_episodes": sum(r["queues"] for r in per_run),
        "queues_covered": sum(r["queues_covered"] for r in per_run),
        "queue_recall": round(
            sum(r["queues_covered"] for r in per_run) / max(1, sum(r["queues"] for r in per_run)), 3
        ),
        "queue_recall_ci95": wilson(
            sum(r["queues_covered"] for r in per_run), sum(r["queues"] for r in per_run)
        ),
        "incident_delay_after_blockage_onset_s": {
            "n": len(delays["blockage"]),
            "median": statistics.median(delays["blockage"]) if delays["blockage"] else None,
            "p90": stat(delays["blockage"], 0.9),
        },
        "incident_delay_after_queue_onset_s": {
            "n": len(delays["queue"]),
            "median": statistics.median(delays["queue"]) if delays["queue"] else None,
            "p90": stat(delays["queue"], 0.9),
        },
        "incidents_by_type": {
            k: sum(1 for r in per_run for i in r["incidents"] if i["type"] == k)
            for k in sorted({i["type"] for r in per_run for i in r["incidents"]})
        },
        "candidate_to_incident_latency_s": {
            "n": len(latencies),
            "median": statistics.median(latencies) if latencies else None,
            "p95": stat(latencies, 0.95),
            "max": latencies[-1] if latencies else None,
            "note": "last contributing evidence event's observation_time to incident opened_at; event-driven correlation (no polling delay), nominal load",
        },
    }


def evaluate_split(
    data_split: list[dict], segments, policy: Policy, fusion: FusionParams, artifact: dict
) -> dict:
    topo = Topology(segments, policy.max_hops)
    by_class: dict[str, list[dict]] = defaultdict(list)
    for run in data_split:
        by_class[run["spec"]].append(score_run(run, segments, policy, fusion, artifact, topo))
    out = {cls: summarise(rs) for cls, rs in sorted(by_class.items())}
    out["_all_classes_pooled_for_FA-01"] = summarise([r for rs in by_class.values() for r in rs])
    return out


def main(dry_run: bool) -> int:
    policy = load_policy()
    artifact = load_congestion()
    fusion = FusionParams(**json.loads(es.ARTIFACT.read_text(encoding="utf-8"))["params"])
    data, segments = prepare_all(("train", "validation", "test"))
    report = {
        "schema": "incident-level-evaluation-v1",
        "policy": {
            k: getattr(policy, k)
            for k in ("open_confidence", "slack_s", "max_hops", "resolve_hysteresis_s")
        },
        "truth": "SUMO stop-output blockage windows + standing-queue episodes (edge-wide waiting vehicles); an incident is a TP if a member candidate relates to a truth event on the graph",
    }
    for split in ("train", "validation"):
        report[split] = evaluate_split(data[split], segments, policy, fusion, artifact)
        pooled = report[split]["_all_classes_pooled_for_FA-01"]
        print(
            split,
            "FA-01",
            pooled["false_positive_rate"],
            f"{pooled['false_positive']}/{pooled['incidents_raised']}",
            "blockage recall",
            pooled["blockage_recall"],
            "queue recall",
            pooled["queue_recall"],
        )
    if dry_run:
        for cls, v in report["validation"].items():
            print(
                cls,
                {
                    k: v[k]
                    for k in (
                        "incidents_raised",
                        "true_positive",
                        "false_positive",
                        "blockages_covered",
                        "measured_blockages",
                        "queues_covered",
                        "truth_queue_episodes",
                    )
                },
            )
        return 0
    sha = json.loads((DATASET / "dataset_manifest.json").read_text(encoding="utf-8"))[
        "dataset_sha256"
    ]
    record_opening(
        "incident_report",
        sha,
        FEATURE_VERSION,
        {"task": "P06.08", "note": "policy fixed before TEST; test scored once"},
    )
    report["test"] = evaluate_split(data["test"], segments, policy, fusion, artifact)
    REGISTRY.mkdir(parents=True, exist_ok=True)
    (REGISTRY / "incident_evaluation.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    pooled = report["test"]["_all_classes_pooled_for_FA-01"]
    print(
        "TEST FA-01",
        pooled["false_positive_rate"],
        f"{pooled['false_positive']}/{pooled['incidents_raised']}",
        "blockage recall",
        pooled["blockage_recall"],
        "queue recall",
        pooled["queue_recall"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--dry-run" in sys.argv))
