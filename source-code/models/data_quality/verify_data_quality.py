"""P10.10 acceptance evidence: the data-quality detector's package, discipline and independent reproduction (acceptance target FA-02).

    python source-code/models/data_quality/verify_data_quality.py        # about six minutes: it re-runs the whole selection

A. Provenance: the recorded code hashes are the code on disk now, the recorded dataset hash is the generator's, and the split seeds are
   disjoint, with the selection using exactly the train and validation seeds.
B. Test-split discipline: the ledger holds exactly one selecting opening for this dataset and rule version, and it names the stored numbers.
C. Independent reproduction: the selection is re-run from scratch on the selection seeds and must choose the stored numbers; the detector
   is re-run on the validation and test seeds and must give the recorded incident counts.
D. Clean streams: the detector is run on the test seeds WITHOUT any injected fault and must raise nothing.
E. Truth stays separate: the detector's entry point takes no fault list, an incident carries no cause, and every incident record says so.
F. FA-02, at the strict rule: false-incident share on the held-out test seeds against the 5 % target, with the interval beside it, all five
   classes present and detected, the overlap rule reported beside the strict one.
G. What a reader is owed: limitations recorded and naming the synthetic source, the stuck-at-zero blind spot and the weak drift class.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.evidence import Evidence  # noqa: E402
from models.data_quality import detector, scoring, streams  # noqa: E402
from models.data_quality import tune_evaluate as te  # noqa: E402
from models.evaluation.test_gate import LEDGER_PATH, record_opening  # noqa: E402

ev = Evidence("P10.10", "p10_10_data_quality", docs_name="p10_10_data_quality")
FIVE = streams.CLASSES


def false_count(m: dict) -> int:
    return m["fragment"] + m["wrong_class"] + m["spurious"]


def reselect(selection_data: dict) -> detector.Params:
    """The selection rule written out again, independently of `tune_evaluate.main`."""

    def run_class(params: detector.Params, cls: tuple[str, ...]) -> tuple[int, int, float]:
        incidents = {s: detector.run(params, e, d) for s, (e, _, d) in selection_data.items()}
        false = detected = 0
        delays = []
        for c in cls:
            for s, (_, faults, _) in selection_data.items():
                r = scoring.score(incidents[s], faults, 1.0, only=c)
                false += r["fragment"] + r["wrong_class"] + r["spurious"]
                detected += r["per_class"][c]["detected"]
                if r["per_class"][c]["median_delay_s"] is not None:
                    delays.append(r["per_class"][c]["median_delay_s"])
        return false, -detected, sum(delays) / len(delays) if delays else 1e9

    params = detector.Params()
    params = detector.Params(
        **{
            **params.as_dict(),
            "stuck_run": min(
                te.GRID["stuck_run"],
                key=lambda n: run_class(
                    detector.Params(**{**params.as_dict(), "stuck_run": n}), ("stuck",)
                ),
            ),
        }
    )
    silence, grace = min(
        te.GRID["missing"],
        key=lambda sg: run_class(
            detector.Params(**{**params.as_dict(), "silence_after_s": sg[0], "gap_grace_s": sg[1]}),
            ("missing", "out_of_order"),
        ),
    )
    params = detector.Params(
        **{**params.as_dict(), "silence_after_s": silence, "gap_grace_s": grace}
    )
    results = {}
    for k, h in te.GRID["drift"]:
        p = detector.Params(**{**params.as_dict(), "cusum_k": k, "cusum_h": h})
        m = te.measure(p, selection_data)
        results[(k, h)] = (m["strict_false_share"], m["per_class"]["drift"]["detected"])
    within = {kh: v for kh, v in results.items() if v[0] <= te.SELECTION_MARGIN}
    pool = within or {min(results, key=lambda kh: results[kh][0]): None}
    k, h = max(pool, key=lambda kh: (results[kh][1], -results[kh][0]))
    return detector.Params(**{**params.as_dict(), "cusum_k": k, "cusum_h": h})


def main() -> int:  # noqa: PLR0915
    package = json.loads(te.PACKAGE.read_text(encoding="utf-8"))
    params = detector.Params(**package["params"])
    seeds = streams.SEEDS
    selection_seeds = seeds["train"] + seeds["validation"]

    # ---- A. provenance
    changed = [
        n for n, h in te.code_hashes().items() if package["provenance"]["code_sha256"].get(n) != h
    ]
    ev.check(
        "the_code_that_produced_the_package_is_the_code_on_disk_now",
        not changed,
        f"changed since: {changed}",
    )
    ev.check(
        "the_recorded_dataset_hash_is_the_generators_now",
        package["dataset"]["sha256"] == te.dataset_sha256(),
        package["dataset"]["sha256"][:16],
    )
    disjoint = not (
        set(seeds["train"]) & set(seeds["validation"])
        or set(seeds["train"]) & set(seeds["test"])
        or set(seeds["validation"]) & set(seeds["test"])
    )
    ev.check(
        "the_train_validation_and_test_seeds_are_disjoint_and_the_selection_used_only_the_first_two",
        disjoint
        and package["selection"]["seeds"] == selection_seeds
        and not set(package["selection"]["seeds"]) & set(seeds["test"]),
        f"{len(seeds['train'])}/{len(seeds['validation'])}/{len(seeds['test'])} seeds",
    )

    # ---- B. ledger
    ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    mine = [
        e
        for e in ledger["entries"]
        if e["dataset_sha256"] == package["dataset"]["sha256"]
        and e["feature_version"] == package["feature_version"]
    ]
    selecting = [e for e in mine if e["purpose"] == "final_comparison"]
    ev.check(
        "the_ledger_holds_exactly_one_selecting_test_opening_and_it_names_the_stored_numbers",
        len(selecting) == 1 and selecting[0]["detail"]["params"] == package["params"],
        f"{len(selecting)} selecting opening(s)",
    )

    # ---- C. independent reproduction
    selection_data = te.load(selection_seeds)
    chosen = reselect(selection_data)
    ev.check(
        "re_running_the_selection_on_the_selection_seeds_chooses_the_stored_numbers",
        chosen == params,
        f"{chosen.as_dict()} vs {params.as_dict()}",
    )
    validation = te.measure(params, te.load(seeds["validation"]))
    stored_val = package["metrics"]["validation"]
    ev.check(
        "re_running_the_detector_on_validation_gives_the_recorded_incident_counts",
        all(
            validation[k] == stored_val[k]
            for k in ("incidents", "true", "fragment", "wrong_class", "spurious", "faults")
        ),
        f"{validation['incidents']} incidents, {validation['true']} true",
    )
    record_opening(
        "reproduction",
        package["dataset"]["sha256"],
        package["feature_version"],
        {
            "by": "verify_data_quality.py",
            "purpose": "non-selecting reproduction of the recorded test numbers",
        },
    )
    test = te.measure(params, te.load(seeds["test"]))
    stored_test = package["metrics"]["test"]
    ev.check(
        "re_running_the_detector_on_the_test_seeds_reproduces_the_recorded_numbers_a_non_selecting_use",
        all(
            test[k] == stored_test[k]
            for k in ("incidents", "true", "fragment", "wrong_class", "spurious", "faults")
        ),
        f"{test['incidents']} incidents, {false_count(test)} false",
    )

    # ---- D. clean streams
    devices = streams.load_devices()
    quiet = {}
    for seed in seeds["test"]:
        events = sorted(
            (e for evs in streams.clean_streams(seed, devices).values() for e in evs),
            key=lambda e: (e.arrival, e.device_id, e.sequence_number),
        )
        quiet[seed] = len(detector.run(params, events, devices))
    ev.check(
        "fault_free_streams_raise_no_incident_on_any_test_seed",
        not any(quiet.values()),
        f"incidents per seed {quiet} over {len(devices)} devices x {streams.STEPS - streams.CALIBRATION_STEPS} minutes",
    )

    # ---- E. truth separate
    signature = list(inspect.signature(detector.run).parameters)
    ev.check(
        "the_detectors_entry_point_takes_no_fault_list_and_the_event_has_no_truth_field",
        signature == ["params", "events", "devices", "tick_s"]
        and not {"fault", "truth", "label"} & set(streams.Event.__dataclass_fields__),
        str(signature),
    )
    events, _, _ = streams.scenario(seeds["test"][0])
    incidents = detector.run(params, events, devices)
    ev.check(
        "no_incident_asserts_a_cause_and_every_one_says_so",
        all(i["verified_cause"] is None for i in incidents) and bool(incidents),
        f"{len(incidents)} incidents",
    )

    # ---- F. FA-02
    n, false = test["incidents"], false_count(test)
    strict = false / n
    low, high = te.wilson(false, n)
    ev.check(
        "FA_02_data_quality_incident_false_positive_rate_is_at_most_5_percent_on_the_held_out_test_seeds_strict_rule",
        strict <= te.TARGET,
        f"{false} false of {n} incidents = {strict:.1%} (Wilson 95% {low:.1%}-{high:.1%}); the interval's upper end is {'above' if high > te.TARGET else 'below'} the 5% target",
    )
    per = test["per_class"]
    ev.check(
        "every_one_of_the_five_classes_is_present_and_detected_on_the_test_seeds",
        all(per[c]["faults"] > 0 and per[c]["detected"] > 0 for c in FIVE),
        ", ".join(f"{c} {per[c]['detected']}/{per[c]['faults']}" for c in FIVE),
    )
    ev.check(
        "the_overlap_rule_is_reported_beside_the_strict_one_and_not_used_as_the_headline",
        "overlap_false_share" in stored_test
        and package["fa_02"]["test_strict_false_share"] == stored_test["strict_false_share"],
        f"overlap {stored_test['overlap_false_share']:.1%}, strict {stored_test['strict_false_share']:.1%}",
    )

    # ---- G. owed to the reader
    limits = " ".join(package["limitations"]).lower()
    ev.check(
        "the_limitations_name_the_synthetic_source_the_stuck_at_zero_blind_spot_and_the_weak_drift_class",
        all(
            w in limits
            for w in (
                "synthetic",
                "frozen at exactly zero",
                "drift is the weak class",
                "no database table",
            )
        ),
        f"{len(package['limitations'])} limitations",
    )
    ev.check(
        "the_selection_grid_and_every_candidate_result_are_recorded",
        all(package["selection"]["grid"][k] for k in ("stuck_run", "missing", "drift")),
        f"{sum(len(v) for v in package['selection']['grid'].values())} candidates",
    )

    ev.metrics = {
        "params": package["params"],
        "test": {k: v for k, v in stored_test.items()},
        "validation": {k: v for k, v in stored_val.items()},
        "selection": package["metrics"]["selection"],
        "test_drift_recall_by_severity": package["metrics"]["test_drift_recall_by_severity"],
        "test_drift_recall_by_channel": package["metrics"]["test_drift_recall_by_channel"],
        "fa_02": package["fa_02"],
    }
    ev.notes["scope"] = (
        "seeded synthetic streams over the P03.03 device set with injected faults; not a real fleet (see the package limitations)"
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
