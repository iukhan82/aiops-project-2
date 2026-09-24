"""P06.07: calibrate the incident policy from measured detector precision.

    python source-code/backend/analytics/calibrate_incident_policy.py

The correlator does not trust a detector's own score (the stall fusion, for
instance, emits noisy-OR probabilities near 0.97 for candidates whose measured
precision is well below that). Each candidate kind gets the *empirical
precision* of its detector, measured on TRAIN + VALIDATION only (TEST is
reserved for P06.08's held-out incident evaluation), with its Wilson interval.

Kinds SUMO cannot physically produce (collision, wrong-way, flooding, low
visibility come from labelled telemetry overlays) have no measurable false
positive rate in simulation; they carry explicit *assumed* priors, marked as
such, that a real deployment must replace with measured values.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics import evaluate_congestion as ec  # noqa: E402
from backend.analytics import evaluate_stall as es  # noqa: E402
from backend.analytics.congestion import SpillParams, match_spans, wilson  # noqa: E402
from backend.analytics.congestion_service import load_artifact as load_congestion  # noqa: E402
from backend.analytics.stall_candidates import FusionParams, fuse  # noqa: E402

OUT = SOURCE_ROOT / "backend" / "analytics" / "artifacts" / "incident_policy.json"
VRU_ARTIFACT = SOURCE_ROOT / "backend" / "analytics" / "artifacts" / "vru_conflict.json"
ASSUMED = {"collision": 0.8, "wrong_way": 0.7, "flooding": 0.7, "low_visibility": 0.8}


def congestion_and_spillback() -> tuple[dict, dict]:
    artifact = load_congestion()
    from backend.analytics.congestion import Params

    runs = ec.load_runs(("train", "validation"))
    params, spill = Params(**artifact["params"]), SpillParams(**artifact["spillback_params"])
    det = tp = sb_det = sb_tp = 0
    for split in ("train", "validation"):
        scores = ec.score_runs(runs[split], runs["segments"], params, spill)
        for v in scores.values():
            det += v["detected_episodes"]
            tp += v["matched"]
            sb_det += v["spillback"]["detected"]
            sb_tp += v["spillback"]["matched"]
    return (
        {
            "matched": tp,
            "candidates": det,
            "precision": round(tp / det, 3),
            "wilson95": wilson(tp, det),
        },
        {
            "matched": sb_tp,
            "candidates": sb_det,
            "precision": round(sb_tp / sb_det, 3) if sb_det else None,
            "wilson95": wilson(sb_tp, sb_det),
        },
    )


def stall() -> dict:
    runs = es.load_runs(("train", "validation"))
    es.init_adjacency(runs["segments"])
    data = es.prepare(runs, ("train", "validation"))
    params = FusionParams(**json.loads(es.ARTIFACT.read_text(encoding="utf-8"))["params"])
    counts = {True: [0, 0], False: [0, 0]}
    for split in ("train", "validation"):
        for run in data[split]:
            cands = fuse(run["spans"], run["episodes"], params)
            spans = [(c["network_element_id"], c["onset_time"], es.parse_last(c)) for c in cands]
            truth = [(e, on, end) for e, on, end, _ in run["incidents"]]
            matched = {p[0] for p in match_spans(spans, truth)}
            for i, c in enumerate(cands):
                bucket = counts[bool(c["attributes"]["corroborated"])]
                bucket[1] += 1
                bucket[0] += i in matched
    return {
        ("corroborated" if k else "single_source"): {
            "matched": v[0],
            "candidates": v[1],
            "precision": round(v[0] / v[1], 3) if v[1] else None,
            "wilson95": wilson(v[0], v[1]),
        }
        for k, v in counts.items()
    }


def main() -> int:
    cong, spill = congestion_and_spillback()
    stall_cal = stall()
    vru = json.loads(VRU_ARTIFACT.read_text(encoding="utf-8"))
    print("congestion", cong, "\nspillback", spill, "\nstall", stall_cal)
    precision = {
        "congestion": cong["precision"],
        "spillback": spill["precision"],
        "stalled_vehicle": {
            "single_source": stall_cal["single_source"]["precision"],
            "corroborated": stall_cal["corroborated"]["precision"],
        },
        "pedestrian_conflict": vru["window_precision"],
        **ASSUMED,
    }
    policy = {
        "schema": "incident-policy-v1",
        "calibrated_precision": precision,
        "basis": {
            "congestion": {
                "measured_on": "TRAIN+VALIDATION episodes vs SUMO standing-queue truth",
                **cong,
            },
            "spillback": {"measured_on": "TRAIN+VALIDATION", **spill},
            "stalled_vehicle": {
                "measured_on": "TRAIN+VALIDATION candidates vs SUMO measured blockage windows, split by whether a congestion episode corroborated the edge alarm",
                **stall_cal,
            },
            "pedestrian_conflict": {
                "measured_on": "validation site x 5-min windows flagged by the VRU indicator that contained a true conflict",
                "precision": vru["window_precision"],
            },
            "collision/wrong_way/flooding/low_visibility": {
                "assumed": ASSUMED,
                "reason": "SUMO cannot simulate these; the detectors are specification-tested overlays with no measurable false-positive rate. Replace with measured values before any real use.",
            },
        },
        "open_confidence": 0.6,
        "slack_s": 300.0,
        "max_hops": 3,
        "resolve_hysteresis_s": 120.0,
        "escalate_unacknowledged_after_s": 600.0,
        "escalate_corroborated_confidence": 0.85,
        "policy_note": "open_confidence (0.6: a single-source candidate whose measured precision is below 0.6 - spillback 0.5, uncorroborated stall 0.46, pedestrian window 0.5 - never opens an incident alone, FA-01 asks for <= 10% false incidents), slack and hysteresis are design choices, evaluated (not tuned on TEST) in P06.08; only the precisions are measured.",
    }
    OUT.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
