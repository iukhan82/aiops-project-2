"""P06.08: assemble the acceptance report for Phase 06 (detection/forecast/
incident latency and errors, by scenario class and never blended - ACC-03).

    python source-code/backend/analytics/evaluate_p06_08.py

This does not recompute anything: it cites the held-out numbers already
produced and ledgered by P06.02-P06.07's own evaluation scripts (each scored
TEST exactly once) plus P06.08's own incident-level pass
(evaluate_intelligence.py), and states plainly which ACCEPTANCE_TARGETS.md
rows this phase can and cannot close.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

REGISTRY = SOURCE_ROOT / "models" / "registry"


def load(*parts: str) -> dict:
    return json.loads((REGISTRY.joinpath(*parts)).read_text(encoding="utf-8"))


def main() -> int:
    forecast = load("traffic-forecast", "training_report.json")["test"]
    congestion = load("congestion-detector", "evaluation.json")["test"]
    stall = load("stall-fusion", "evaluation.json")["test"]
    overlay = load("overlay-detectors", "evaluation.json")
    vru = load("vru-conflict", "evaluation.json")
    incident = load("intelligence-eval", "incident_evaluation.json")

    acc02 = {
        k: {
            "model_mae": v["model_test_mae"],
            "baseline_mae": v["baseline_test_mae"],
            "verdict": v["test_verdict_model_vs_baseline"],
            "served": v["validation_decision"],
        }
        for k, v in forecast.items()
    }
    acc03 = {
        "congestion_by_scenario_class": {
            k: {
                "precision": v["precision"],
                "recall": v["recall"],
                "truth_episodes": v["truth_episodes"],
            }
            for k, v in congestion.items()
        },
        "stall_fusion": {"precision": stall["precision"], "recall": stall["recall"]},
        "vru_conflict_event_level": {
            "precision": vru["test"]["precision"],
            "recall": vru["test"]["recall"],
        },
    }

    pooled = {
        split: incident[split]["_all_classes_pooled_for_FA-01"]
        for split in ("train", "validation", "test")
    }
    fa01_test = pooled["test"]
    lat03_test = fa01_test["candidate_to_incident_latency_s"]

    fa02 = {
        "status": "not measurable in this phase",
        "reason": "no data-quality incident detector exists yet - P10.07 ('Correlate platform signals into incidents') "
        "builds it from OpenTelemetry/health signals (P10.01-P10.06). P03.06's fault catalog (7 types, "
        "byte-identical ground truth, source-code/simulator/faults/) is ready as that stage's held-out set; "
        "it is unused here because using it now would mean inventing a detector to satisfy the metric.",
    }
    safe03 = {
        "status": "partially measurable now, not yet enforced",
        "measured": "P05.06/P05.07 (verify_network_state.py, verify_api.py) prove freshness_status flips to 'stale' "
        "past the staleness budget and the API reports it correctly.",
        "gap": "No dependent service currently reads freshness_status and refuses to act on it - that gate belongs to "
        "P07.05's command execution policy ('fresh state' check) and P07's route/recommendation consumers. "
        "SAFE-03 cannot be closed here; it is carried to P07.05/P07.09 and P12.02's fault-injection suite.",
    }

    report = {
        "schema": "p06-08-acceptance-v1",
        "ACC-02_forecast_beats_or_is_rejected_against_baseline": acc02,
        "ACC-03_congestion_spillback_precision_recall_per_scenario_class": acc03,
        "FA-01_incident_false_positive_rate": {
            "target": "<= 10% of raised incidents, counted after correlation/deduplication",
            "train": {
                "false_positive_rate": pooled["train"]["false_positive_rate"],
                "ci95": pooled["train"]["false_positive_rate_ci95"],
                "n": pooled["train"]["incidents_raised"],
            },
            "validation": {
                "false_positive_rate": pooled["validation"]["false_positive_rate"],
                "ci95": pooled["validation"]["false_positive_rate_ci95"],
                "n": pooled["validation"]["incidents_raised"],
            },
            "test": {
                "false_positive_rate": fa01_test["false_positive_rate"],
                "ci95": fa01_test["false_positive_rate_ci95"],
                "n": fa01_test["incidents_raised"],
            },
            "met": (fa01_test["false_positive_rate"] or 0) <= 0.10,
            "caveat": f"only {fa01_test['incidents_raised']} incidents were raised on TEST (2 blockage-seed runs, 6 measured physical blockages "
            f"plus queue episodes): the interval is wide ({pooled['test']['false_positive_rate_ci95']}) and 0% should be read as "
            "'no false positives observed on a small sample', not as a tight bound.",
        },
        "LAT-03_candidate_to_incident_latency": {
            "target": "P95 < 5 s, last contributing evidence event to incident record creation, nominal load",
            "test": lat03_test,
            "met": (lat03_test["p95"] is not None and lat03_test["p95"] < 5.0),
            "caveat": "measured for event-driven correlation (sync() called per new/changed candidate). The Part-A "
            "replay in verify_incidents.py polls every 5 minutes for demonstration convenience and would "
            "itself violate this target - deployment MUST trigger sync() on candidate write, not on a timer.",
        },
        "FA-02_data_quality_incident_false_positive_rate": fa02,
        "SAFE-03_stale_state_triggers_fail_safe": safe03,
        "incident_level_recall_TEST": {
            "blockage_recall": pooled["test"]["blockage_recall"],
            "blockage_recall_ci95": pooled["test"]["blockage_recall_ci95"],
            "queue_recall": pooled["test"]["queue_recall"],
            "queue_recall_ci95": pooled["test"]["queue_recall_ci95"],
            "incidents_by_type": pooled["test"]["incidents_by_type"],
        },
        "overlay_kinds_note": "collision/wrong_way/flooding/low_visibility are specification-tested (P06.05), not scenario-scored: "
        f"{overlay['injection_totals']['detected_exactly']}/{overlay['injection_totals']['injected']} injected "
        "signatures detected exactly, 0 false alarms - no ACC-03/FA-01 claim is made for them beyond that.",
        "cross_references": {
            "P06.02_forecast_baselines": "models/registry/traffic-forecast/baseline_evaluation.json",
            "P06.03_forecast_model": "models/registry/traffic-forecast/training_report.json",
            "P06.04_congestion": "models/registry/congestion-detector/evaluation.json",
            "P06.05_stall_and_overlays": "models/registry/stall-fusion/evaluation.json, models/registry/overlay-detectors/evaluation.json",
            "P06.06_vru_conflict": "models/registry/vru-conflict/evaluation.json",
            "P06.07_incident_policy": "backend/analytics/artifacts/incident_policy.json",
            "P06.08_incident_level": "models/registry/intelligence-eval/incident_evaluation.json",
        },
    }
    out = REGISTRY / "intelligence-eval" / "p06_08_acceptance.json"
    out.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "FA-01_incident_false_positive_rate",
                    "LAT-03_candidate_to_incident_latency",
                )
            },
            default=str,
            indent=1,
        )
    )
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
