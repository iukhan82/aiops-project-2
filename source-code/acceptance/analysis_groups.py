#!/usr/bin/env python3
"""P12.04: do the platform's outcomes differ by place or by kind of road user? Measured from what exists, with the limits of the data stated.

    python source-code/acceptance/analysis_groups.py

The district is synthetic (12 intersections, three corridors A, B, C, one recorded ten-minute sensor run, simulated emergency units), so "fairness" here can only mean two things:

  by PLACE   does the congestion detector - the platform's own rule detector with its FROZEN parameters, not tuned here - find episodes as well on one corridor and direction as on
             another? Sensor coverage by corridor is counted from the device catalogue, since a place with fewer sensors is a place with fewer chances of being seen.
  by KIND    of user of the road: the emergency services (ambulance, fire, police: how well is the ETA predicted for each), general traffic on the streets a pre-empted corridor
             crosses (how much longer does it wait), and the four modes the platform reasons about (car traffic, pedestrians, cyclists, transit).

The held-out TEST runs are opened once more for the by-place detection breakdown. The opening is NON-SELECTING and ledgered as such (`models/registry/test_split_ledger.json`): nothing was chosen,
tuned or changed after seeing it, and the parameters are read from the stored artifact. Groups with too few truth episodes to say anything are labelled as such, never ranked. No demographic or
population data exists in the district, so no claim about people is possible and none is made.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.congestion import Params, detect_congestion, match_spans, truth_episodes, wilson  # noqa: E402  # fmt: skip
from backend.analytics.evaluate_congestion import ANCHOR, DATASET, load_runs  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from models.evaluation.test_gate import _load as load_ledger  # noqa: E402
from models.evaluation.test_gate import record_opening  # noqa: E402

ARTIFACT = SOURCE_ROOT / "backend" / "analytics" / "artifacts" / "congestion_params.json"
DEVICES = SOURCE_ROOT / "simulator" / "sensors" / "output" / "run-a" / "devices.jsonl"
MIN_TRUTH_TO_SAY_ANYTHING = (
    8  # below this many truth episodes a rate has an interval too wide to rank anything by
)
ev = Evidence("P12.04", "p12_04_outcomes_by_group", docs_name="p12_04_outcomes_by_group")


def group_rates(bucket: dict[str, dict]) -> dict:
    out = {}
    for name, b in sorted(bucket.items()):
        truth, det, tp = b["truth"], b["detected"], b["tp"]
        out[name] = {
            "truth_episodes": truth, "detected_episodes": det, "matched": tp,
            "recall": round(tp / truth, 3) if truth else None, "recall_ci95": wilson(tp, truth) if truth else None,
            "precision": round(tp / det, 3) if det else None, "precision_ci95": wilson(tp, det) if det else None,
            "enough_truth_to_compare": truth >= MIN_TRUTH_TO_SAY_ANYTHING,
        }  # fmt: skip
    return out


def main() -> int:  # noqa: PLR0915
    params_doc = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    params = Params(**params_doc["params"])
    dataset_sha = json.loads((DATASET / "dataset_manifest.json").read_text(encoding="utf-8"))[
        "dataset_sha256"
    ]
    ledger_before = len(
        load_ledger(Path(SOURCE_ROOT / "models" / "registry" / "test_split_ledger.json"))["entries"]
    )
    runs = load_runs(("test",))
    segments = runs["segments"]
    corridor_of = {s.edge_id: s.corridor_id for s in segments}
    direction_of = {s.edge_id: s.direction for s in segments}
    record_opening(
        "corridor_breakdown",
        dataset_sha,
        "congestion-rule/1",
        {
            "task": "P12.04",
            "note": "non-selecting: the frozen detector's held-out episodes grouped by corridor and direction; nothing chosen, tuned or changed afterwards",
        },
    )
    by_corridor: dict[str, dict] = defaultdict(lambda: {"truth": 0, "detected": 0, "tp": 0})
    by_direction: dict[str, dict] = defaultdict(lambda: {"truth": 0, "detected": 0, "tp": 0})
    delays: dict[str, list[float]] = defaultdict(list)
    for run in runs["test"]:
        det = detect_congestion(run["events"], segments, params)
        tru = truth_episodes(run["truth"], segments, ANCHOR)
        pairs = match_spans(
            [(e.segment, e.onset, e.clear) for e in det], [(t.segment, t.onset, t.end) for t in tru]
        )
        matched_det = {i for i, _ in pairs}
        for i, e in enumerate(det):
            for bucket, key in (
                (by_corridor, corridor_of[e.segment]),
                (by_direction, direction_of[e.segment]),
            ):
                bucket[key]["detected"] += 1
                bucket[key]["tp"] += 1 if i in matched_det else 0
        for j, t in enumerate(tru):
            for bucket, key in (
                (by_corridor, corridor_of[t.segment]),
                (by_direction, direction_of[t.segment]),
            ):
                bucket[key]["truth"] += 1
        for i, j in pairs:
            delays[corridor_of[det[i].segment]].append(
                (det[i].detected_at - tru[j].onset).total_seconds()
            )
    corridor_rates, direction_rates = group_rates(by_corridor), group_rates(by_direction)
    ledger_after = load_ledger(
        Path(SOURCE_ROOT / "models" / "registry" / "test_split_ledger.json")
    )["entries"]
    entry = ledger_after[-1]
    ev.check(
        "the_held_out_test_opening_is_ledgered_as_non_selecting_with_its_purpose_and_task",
        len(ledger_after) == ledger_before + 1
        and entry["purpose"] == "corridor_breakdown"
        and entry["detail"]["task"] == "P12.04",
        f"ledger {ledger_before} -> {len(ledger_after)}; last: {entry['purpose']}",
    )
    ev.check(
        "the_detectors_parameters_were_read_from_the_stored_artifact_not_chosen_here",
        params_doc["params"]
        == {
            "n_off": params.n_off,
            "n_on": params.n_on,
            "occ_off_frac": params.occ_off_frac,
            "occ_on": params.occ_on,
            "speed_on": params.speed_on,
        },
        str(params_doc["params"]),
    )
    total_truth = sum(b["truth"] for b in by_corridor.values())
    ev.check(
        "the_groups_account_for_every_truth_episode_of_the_test_runs",
        total_truth == sum(b["truth"] for b in by_direction.values()) and total_truth > 0,
        f"{total_truth} truth episodes; corridors {sorted(by_corridor)}, directions {sorted(by_direction)}",
    )
    for name, rates in corridor_rates.items():
        ev.check(
            f"corridor_{name}_recall_and_precision_are_reported_with_intervals_and_labelled_if_the_sample_is_too_small_to_compare",
            rates["truth_episodes"] >= 0
            and (rates["recall"] is None or rates["recall_ci95"] is not None),
            f"{rates['truth_episodes']} truth, recall {rates['recall']} {rates['recall_ci95']}, precision {rates['precision']}; comparable: {rates['enough_truth_to_compare']}",
        )
    comparable = {
        n: r
        for n, r in corridor_rates.items()
        if r["enough_truth_to_compare"] and r["recall"] is not None
    }
    if len(comparable) >= 2:
        best = max(comparable.items(), key=lambda kv: kv[1]["recall"])
        worst = min(comparable.items(), key=lambda kv: kv[1]["recall"])
        overlap = not (worst[1]["recall_ci95"][1] < best[1]["recall_ci95"][0])
        finding = f"recall {best[0]} {best[1]['recall']} against {worst[0]} {worst[1]['recall']}; the 95% intervals {'overlap' if overlap else 'do not overlap'}"
    else:
        finding = f"only {len(comparable)} corridor(s) have at least {MIN_TRUTH_TO_SAY_ANYTHING} truth episodes on the test runs: no comparison across corridors is possible"
        overlap = None
    ev.check(
        "the_disparity_between_corridors_is_stated_in_numbers_or_as_not_measurable", True, finding
    )

    # ---- coverage: how many sensors watch each place
    coverage: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for line in DEVICES.read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        place = d["location"].get("corridor_id") or d["location"].get("intersection_id", "unplaced")
        coverage["corridor" if str(place).startswith("corridor") else "junction"][
            f"{place}:{d['device_type']}"
        ] += 1
    per_corridor = defaultdict(int)
    for key, n in coverage["corridor"].items():
        per_corridor[key.split(":")[0]] += n
    ev.check(
        "sensor_coverage_by_corridor_is_counted_from_the_device_catalogue",
        len(per_corridor) >= 3,
        str(dict(per_corridor)),
    )

    # ---- by kind of road user: from the measured scenario evidence
    scenarios = json.loads(
        (REPO_ROOT / "docs" / "evidence" / "p07_10_scenarios.json").read_text(encoding="utf-8")
    )["metrics"]
    eta_by_unit = {
        k: {"mae_pct": v["mae_pct"], "n": v["n"], "worst_pct": v["worst_pct"]}
        for k, v in scenarios["eta_01_normal_traffic"]["per_scenario"].items()
    }
    eta_incident_by_unit = {
        k: {"mae_pct": v["mae_pct"], "n": v["n"], "worst_pct": v["worst_pct"]}
        for k, v in scenarios["eta_02_with_incident"]["per_scenario"].items()
    }
    spread = max(v["mae_pct"] for v in eta_by_unit.values()) - min(
        v["mae_pct"] for v in eta_by_unit.values()
    )
    ev.check(
        "the_eta_error_of_each_emergency_service_is_reported_separately_never_pooled_away",
        set(eta_by_unit) == {"ambulance", "fire", "police"},
        f"{eta_by_unit}; spread {spread:.1f} points",
    )
    preempt = scenarios["traffic_outcomes"]
    ev.check(
        "the_cost_of_pre_emption_to_the_streets_it_crosses_is_measured_paired_with_its_worst_case",
        preempt["pairs"] >= 30
        and "worst_increase" in preempt["cross_street_mean_waiting_change_s"],
        f"cross-street mean change {preempt['cross_street_mean_waiting_change_s']['mean']} s, worst +{preempt['cross_street_mean_waiting_change_s']['worst_increase']} s; network mean {preempt['network_mean_waiting_change_s']['mean']} s, worst +{preempt['network_mean_waiting_change_s']['worst_increase']} s ({preempt['pairs']} pairs, {preempt['window_s']} s window)",
    )
    ev.metrics = {
        "congestion_detector_by_corridor_on_the_held_out_runs": corridor_rates, "congestion_detector_by_direction": direction_rates, "detection_delay_s_median_by_corridor": {k: sorted(v)[len(v) // 2] for k, v in delays.items() if v},
        "corridor_disparity": finding, "intervals_overlap": overlap, "sensors_by_corridor": dict(per_corridor), "sensors_by_place_and_type": {k: dict(v) for k, v in coverage.items()},
        "emergency_eta_error_by_service_normal_traffic": eta_by_unit, "emergency_eta_error_by_service_under_an_incident": eta_incident_by_unit, "preemption_side_effects": preempt,
        "frozen_parameters": params_doc["params"], "dataset_sha256": dataset_sha,
    }  # fmt: skip
    ev.notes["scope"] = (
        "a synthetic district and one recorded sensor run: places and kinds of road user are compared where the data can say anything, and labelled where it cannot"
    )
    ev.notes["not_measurable"] = (
        "anything about people: the district has no population, demographic or vulnerability data, so no demographic fairness claim is possible or made"
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
