"""P10.06 acceptance evidence: the operational anomaly detector's package, provenance, honesty and independent reproduction.

    python source-code/models/operations_detector/verify_operations_detector.py

The trained package (`models/registry/ops-anomaly-detector/1.0.0/`) is checked, not trusted:

A. Integrity: every file matches its manifest hash and the fitted model loads for the feature version it claims.
B. Provenance: the recorded dataset hash is the captured dataset's, the recorded code hashes are the code on disk now (so the model
   was not trained by different code than the one that will score with it), and the split seeds are disjoint.
C. Test-split discipline: the ledger holds exactly one selecting opening ("final_comparison") for this dataset, feature version and
   model, and it names the served detector as chosen on validation.
D. Independent reproduction: the package detector is loaded and re-run on the VALIDATION and TEST splits at the stored thresholds; the
   numbers must equal the ones recorded, and re-running the threshold selection on validation must give the stored threshold. The
   selection rule (a learned model is served only if it beats both transparent baselines within the false-alarm budget) is recomputed
   from the recorded validation numbers.
E. No truth in the features; live scoring equals batch scoring; live scoring latency is measured again and is far below the 5 s step.
F. What a reader is owed: both transparent baselines are reported beside the model, the severity breakdown is present, and the
   limitations are recorded and name the synthetic source and the faults the model does not cover.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.evidence import Evidence  # noqa: E402
from models.evaluation.test_gate import LEDGER_PATH, record_opening  # noqa: E402
from models.operations_detector import train_evaluate as te  # noqa: E402
from models.operations_detector.detectors import Prepared  # noqa: E402
from models.operations_detector.evaluate import (  # noqa: E402
    FALSE_ALARM_BUDGET_PER_HOUR,
    score_run,
    select_threshold,
    summarise,
)
from models.operations_detector.features import (  # noqa: E402
    DATASET_ROOT,
    FEATURE_NAMES,
    FEATURE_VERSION,
    HISTORY,
    SIGNAL_NAMES,
    STEP_S,
    load_split,
)
from models.operations_detector.runtime import OperationsDetector  # noqa: E402

ev = Evidence("P10.06", "p10_06_operations_detector", docs_name="p10_06_operations_detector")
PACKAGE = te.PACKAGE
LATENCY_BUDGET_MS = 100.0  # the newest step is scored once per 5 s
TRUTH_WORDS = ("fault", "severity", "scenario", "burst", "truth", "label")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def same(a: dict, b: dict, keys: tuple[str, ...]) -> bool:
    return all(
        a[k] == b[k] or (a[k] is not None and b[k] is not None and abs(a[k] - b[k]) < 1e-9)
        for k in keys
    )


def main() -> int:  # noqa: PLR0915
    files = ("model.joblib", "evaluation.json", "model_card.json", "artifact_manifest.json")
    ev.check(
        "the_package_has_all_four_files", all((PACKAGE / n).is_file() for n in files), str(PACKAGE)
    )
    if not all((PACKAGE / n).is_file() for n in files):
        return ev.finish()
    manifest = json.loads((PACKAGE / "artifact_manifest.json").read_text(encoding="utf-8"))
    card = json.loads((PACKAGE / "model_card.json").read_text(encoding="utf-8"))
    evaluation = json.loads((PACKAGE / "evaluation.json").read_text(encoding="utf-8"))
    dataset = json.loads(
        (DATASET_ROOT / "run-a" / "dataset_manifest.json").read_text(encoding="utf-8")
    )

    # ---- A. integrity
    ev.check(
        "every_package_file_matches_its_manifest_hash",
        all(sha(PACKAGE / n) == h for n, h in manifest["files"].items()) and set(manifest["files"]) == {"model.joblib", "evaluation.json", "model_card.json"},
    )  # fmt: skip
    bundle = joblib.load(PACKAGE / "model.joblib")
    ev.check(
        "the_fitted_model_loads_for_the_feature_version_it_claims",
        bundle["feature_version"] == FEATURE_VERSION == card["feature_version"] and {"detector", "fill", "scale", "threshold"} <= set(bundle),
        f"served detector {card['served_detector']}",
    )  # fmt: skip

    # ---- B. provenance
    prov = card["provenance"]
    ev.check(
        "the_recorded_dataset_hash_is_the_captured_datasets_and_the_manifest_records_the_same_hash",
        prov["dataset_sha256"] == dataset["dataset_sha256"] == manifest["dataset_sha256"],
    )
    current_code = te.code_hashes()
    stale = sorted(k for k, v in prov["code_sha256"].items() if current_code.get(k) != v)
    ev.check(
        "the_code_that_trained_the_model_is_the_code_on_disk_now",
        not stale,
        f"changed since training: {stale}",
    )
    seeds = {split: set(seed_list) for split, seed_list in dataset["splits"].items()}
    ev.check(
        "the_train_validation_and_test_seeds_are_disjoint",
        not (seeds["train"] & seeds["validation"]) and not (seeds["train"] & seeds["test"]) and not (seeds["validation"] & seeds["test"]),
        str({k: sorted(v) for k, v in seeds.items()}),
    )  # fmt: skip

    train_runs, val_runs, test_runs = (load_split(s) for s in ("train", "validation", "test"))
    ev.check(
        "each_split_holds_only_runs_of_its_own_seeds_and_the_recorded_counts_match",
        all(r.seed in seeds["train"] for r in train_runs) and all(r.seed in seeds["validation"] for r in val_runs)
        and all(r.seed in seeds["test"] for r in test_runs)
        and (len(train_runs), len(val_runs), len(test_runs)) == (prov["train_runs"], prov["validation_runs"], prov["test_runs"]),
        f"{len(train_runs)}/{len(val_runs)}/{len(test_runs)} runs",
    )  # fmt: skip

    # ---- C. the test split was opened once for selection
    ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))["entries"]
    mine = [
        e for e in ledger
        if e["purpose"] == "final_comparison" and e["dataset_sha256"] == dataset["dataset_sha256"]
        and e["feature_version"] == FEATURE_VERSION and e["detail"].get("model_id") == "ops-anomaly-detector"
    ]  # fmt: skip
    ev.check(
        "the_ledger_holds_exactly_one_selecting_test_opening_and_it_names_the_served_detector_chosen_on_validation",
        len(mine) == 1 and mine[0]["detail"]["selected_candidate"] == card["served_detector"] and mine[0]["detail"]["selected_on"] == "validation",
        str(mine[0]["detail"]) if mine else "no entry",
    )  # fmt: skip

    # ---- D. independent reproduction
    detector, fill, threshold = bundle["detector"], bundle["fill"], float(bundle["threshold"])
    served = card["served_detector"]
    val = [Prepared(r, fill) for r in val_runs]
    test = [Prepared(r, fill) for r in test_runs]
    keys = ("event_recall", "false_alarm_episodes", "detected", "fault_runs")
    got_val = summarise([score_run(p, detector.score(p), threshold) for p in val])
    ev.check(
        "re_running_the_package_on_validation_at_its_stored_threshold_gives_the_recorded_numbers",
        same(got_val, evaluation["validation"][served], keys) and abs(threshold - evaluation["thresholds"][served]) < 1e-12,
        f"recall {got_val['event_recall']:.3f}, false-alarm episodes {got_val['false_alarm_episodes']}",
    )  # fmt: skip
    if served not in ("slo_thresholds",):
        redo, _ = select_threshold(val, [detector.score(p) for p in val])
        ev.check(
            "re_running_the_threshold_selection_on_validation_gives_the_stored_threshold",
            abs(redo - threshold) < 1e-9,
            f"{redo:.6f} vs {threshold:.6f}",
        )
    else:
        ev.check(
            "the_slo_baseline_uses_its_fixed_threshold_of_one_times_the_limit",
            abs(threshold - te.SLO_FIXED_THRESHOLD) < 1e-12,
        )
    record_opening(
        "operations_detector_verification",
        dataset["dataset_sha256"],
        FEATURE_VERSION,
        {"task": "P10.06", "note": "non-selecting reproduction of the recorded test numbers"},
    )
    got_test = summarise([score_run(p, detector.score(p), threshold) for p in test])
    ev.check(
        "re_running_the_package_on_the_test_split_reproduces_the_recorded_test_numbers_a_non_selecting_use",
        same(got_test, evaluation["test"][served], keys),
        f"recall {got_test['event_recall']:.3f} ({got_test['detected']}/{got_test['fault_runs']}), false alarms/h {got_test['false_alarms_per_hour']:.2f}",
    )  # fmt: skip
    val_numbers = evaluation["validation"]
    learned = ("isolation_forest", "gradient_boosting")
    best_base = max(
        ("slo_thresholds", "robust_zscore"),
        key=lambda n: (
            val_numbers[n]["event_recall"],
            -(val_numbers[n]["false_alarms_per_hour"] or 0),
        ),
    )
    choice = max(
        learned,
        key=lambda n: (
            val_numbers[n]["event_recall"],
            -(val_numbers[n]["false_alarms_per_hour"] or 0),
        ),
    )
    in_budget = lambda n: val_numbers[n]["false_alarms_per_hour"] <= FALSE_ALARM_BUDGET_PER_HOUR  # noqa: E731
    beats = in_budget(choice) and all(val_numbers[choice]["event_recall"] > val_numbers[b]["event_recall"] or not in_budget(b) for b in ("slo_thresholds", "robust_zscore"))  # fmt: skip
    ev.check(
        "the_served_detector_follows_the_recorded_selection_rule_recomputed_from_the_validation_numbers",
        served == (choice if beats else best_base) and evaluation["candidate_model_accepted"] == beats and evaluation["served_detector"] == served,
        f"candidate {choice} {'accepted' if beats else 'not accepted'}, best baseline {best_base}, serving {served}",
    )  # fmt: skip

    # ---- E. features, parity, latency
    ev.check(
        "no_feature_name_carries_a_fault_severity_scenario_burst_or_label",
        not [n for n in FEATURE_NAMES if any(w in n for w in TRUTH_WORDS)]
        and len(FEATURE_NAMES) == 4 * len(SIGNAL_NAMES),
    )
    ev.check(
        "live_scoring_of_a_window_equals_batch_scoring_of_the_same_data",
        evaluation["live_batch_parity"]["max_abs_difference"] < 1e-6,
        f"max difference {evaluation['live_batch_parity']['max_abs_difference']:.2e}",
    )
    live = OperationsDetector(bundle, card)
    sample = test[0]
    windows = [
        sample.run.raw[i - HISTORY + 1 : i + 1]
        for i in sample.index[:: max(1, len(sample.index) // 200)]
    ]
    times = []
    for w in windows * 3:
        t0 = time.perf_counter()
        live.score(w)
        times.append((time.perf_counter() - t0) * 1000.0)
    p95 = float(np.percentile(times, 95))
    ev.check(
        "live_scoring_latency_measured_again_now_is_far_below_the_five_second_step",
        p95 < LATENCY_BUDGET_MS and evaluation["latency"]["p95_ms"] < LATENCY_BUDGET_MS,
        f"p95 {p95:.2f} ms now, {evaluation['latency']['p95_ms']:.2f} ms recorded, budget {LATENCY_BUDGET_MS} ms for a {STEP_S} s step",
    )  # fmt: skip
    ev.metrics["latency_ms_now"] = {
        "p50": float(np.percentile(times, 50)),
        "p95": p95,
        "n": len(times),
    }

    # ---- F. what a reader is owed
    ev.check(
        "both_transparent_baselines_are_reported_beside_the_model_on_validation_and_test",
        all(n in evaluation["validation"] and n in evaluation["test"] for n in ("slo_thresholds", "robust_zscore", "isolation_forest", "gradient_boosting")),
    )  # fmt: skip
    ev.check(
        "the_severity_breakdown_of_the_served_detector_on_test_is_reported",
        set(evaluation["served_by_severity_on_test"])
        == {"mildest_third", "middle_third", "most_severe_third"},
    )
    limits = " ".join(card["limitations"]).lower()
    ev.check(
        "the_limitations_name_the_synthetic_source_the_faults_not_covered_the_small_test_set_and_that_no_cause_is_asserted",
        len(card["limitations"]) >= 5 and all(w in limits for w in ("synthetic", "alert rules", "test set", "never why")),
        f"{len(card['limitations'])} limitations",
    )  # fmt: skip
    ev.check(
        "the_alarm_rule_the_false_alarm_budget_and_the_threshold_selection_are_recorded",
        "consecutive steps" in card["alarm_rule"] and str(FALSE_ALARM_BUDGET_PER_HOUR) in card["threshold_selection"] and card["threshold"] == threshold,
    )  # fmt: skip

    def line(name: str, block: dict) -> dict:
        return {
            "event_recall": block["event_recall"], "recall_wilson95": block["event_recall_wilson95"],
            "false_alarms_per_hour": block["false_alarms_per_hour"], "median_delay_s": block["median_delay_s"],
        }  # fmt: skip

    ev.metrics["validation"] = {n: line(n, b) for n, b in evaluation["validation"].items()}
    ev.metrics["test"] = {n: line(n, b) for n, b in evaluation["test"].items()}
    ev.metrics["served"] = {
        "detector": served,
        "threshold": threshold,
        "model_accepted_over_baselines": bool(evaluation["candidate_model_accepted"]),
    }
    ev.metrics["by_fault_type_on_test"] = evaluation["test"][served]["by_fault_type"]
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
