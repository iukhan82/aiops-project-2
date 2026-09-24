"""P04.02: transparent rule baseline - runtime behavior and committed artifact."""

import json
import random
from pathlib import Path

import numpy as np
import pytest

from edge.baseline import (
    BASELINE_FORMAT,
    BaselineError,
    RuleBaseline,
    RuleParams,
)
from edge.features import FEATURE_NAMES, FEATURE_VERSION

REGISTRY = Path(__file__).resolve().parents[1] / "models" / "registry" / "baseline"
ARTIFACT = REGISTRY / "baseline_v1.json"
EVALUATION = REGISTRY / "baseline_v1_evaluation.json"


def _vec(**overrides) -> list[float]:
    base = dict.fromkeys(FEATURE_NAMES, 0.0)
    base.update(speed_last=13.9, speed_mean=13.9, speed_min=13.9)
    base.update(overrides)
    return [base[n] for n in FEATURE_NAMES]


def _baseline(variant: str = "occupancy_flow_speed") -> RuleBaseline:
    params = {
        "occupancy_threshold": RuleParams("occupancy_threshold", 0.1),
        "occupancy_persistence": RuleParams("occupancy_persistence", 0.1, occ_mean_min=0.05),
        "occupancy_flow_speed": RuleParams(
            "occupancy_flow_speed", 0.1, count_last_max=1, speed_last_max=3.0
        ),
    }[variant]
    return RuleBaseline(params, FEATURE_NAMES, baseline_id="rules/test", feature_version="v")


def test_each_variant_alarms_only_when_its_conditions_all_hold() -> None:
    blocked = _vec(occ_last=0.5, occ_mean=0.4, count_last=0, speed_last=0.0)
    calm = _vec(occ_last=0.01, occ_mean=0.01, count_last=2, speed_last=13.0)
    for variant in ("occupancy_threshold", "occupancy_persistence", "occupancy_flow_speed"):
        b = _baseline(variant)
        assert b.evaluate(blocked).alarm is True
        assert b.evaluate(calm).alarm is False


def test_decision_explains_which_conditions_fired() -> None:
    d = _baseline("occupancy_flow_speed").evaluate(_vec(occ_last=0.5, count_last=0, speed_last=0.0))
    assert d.alarm and d.score == 0.5
    assert d.fired == ("occ_last>=0.1", "count_last<=1", "speed_last<=3")
    assert _baseline().evaluate(_vec(occ_last=0.05)).fired == ()


def test_dense_flowing_traffic_does_not_alarm_the_flow_speed_variant() -> None:
    """High occupancy alone (congestion) must not read as a blockage."""
    d = _baseline("occupancy_flow_speed").evaluate(_vec(occ_last=0.6, count_last=6, speed_last=9.0))
    assert d.alarm is False


def test_batch_prediction_matches_row_by_row_evaluation() -> None:
    rng = random.Random(7)
    for variant in ("occupancy_threshold", "occupancy_persistence", "occupancy_flow_speed"):
        b = _baseline(variant)
        rows = [
            _vec(
                occ_last=rng.random() * 0.3,
                occ_mean=rng.random() * 0.3,
                count_last=float(rng.randint(0, 4)),
                speed_last=rng.random() * 14,
            )
            for _ in range(300)
        ]
        batch = b.predict_batch(np.asarray(rows, dtype=np.float32))
        single = [int(b.evaluate(r).alarm) for r in rows]
        assert list(batch) == single


def test_wrong_feature_count_is_an_error_not_a_guess() -> None:
    with pytest.raises(BaselineError, match="expected 24 features"):
        _baseline().evaluate([0.0] * 5)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"variant": "made_up", "occ_last_min": 0.1},
        {"variant": "occupancy_persistence", "occ_last_min": 0.1},
        {"variant": "occupancy_flow_speed", "occ_last_min": 0.1, "count_last_max": 1},
    ],
)
def test_invalid_rule_parameters_are_rejected(kwargs: dict) -> None:
    with pytest.raises(BaselineError):
        RuleParams(**kwargs)


def test_load_rejects_bad_artifacts(tmp_path: Path) -> None:
    with pytest.raises(BaselineError, match="cannot read"):
        RuleBaseline.load(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(BaselineError, match="cannot read"):
        RuleBaseline.load(bad)
    bad.write_text(json.dumps({"format": "other/9"}), encoding="utf-8")
    with pytest.raises(BaselineError, match="unsupported baseline format"):
        RuleBaseline.load(bad)
    bad.write_text(
        json.dumps({"format": BASELINE_FORMAT, "feature_version": "x/1"}), encoding="utf-8"
    )
    with pytest.raises(BaselineError, match="malformed"):
        RuleBaseline.load(bad)


def test_committed_artifact_loads_and_matches_runtime_feature_contract() -> None:
    baseline = RuleBaseline.load(ARTIFACT, expected_feature_version=FEATURE_VERSION)
    assert baseline.baseline_id.startswith("rules-blockage/")
    with pytest.raises(BaselineError, match="runtime provides"):
        RuleBaseline.load(ARTIFACT, expected_feature_version="loop-window/999")
    doc = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    assert tuple(doc["feature_names"]) == FEATURE_NAMES


def test_artifact_provenance_records_train_fit_and_untouched_test_split() -> None:
    doc = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    prov = doc["provenance"]
    assert prov["fit_split"] == "train" and prov["selection_split"] == "validation"
    assert prov["test_split_touched"] is False
    assert not set(prov["train_seeds"]) & set(prov["validation_seeds"])
    assert len(prov["dataset_sha256"]) == 64


def test_evaluation_report_has_held_out_metrics_slices_and_failure_cases() -> None:
    ev = json.loads(EVALUATION.read_text(encoding="utf-8"))
    val = ev["validation_held_out"]
    rows = val["rows"]
    assert rows["tp"] + rows["fn"] == rows["positives"]
    assert rows["tp"] + rows["fp"] + rows["fn"] + rows["tn"] == rows["n"]
    assert {"precision", "recall", "f1"} <= set(val["rows_ci95"])
    assert val["by_demand_scale"] and all(s["n"] > 0 for s in val["by_demand_scale"])
    assert val["recall_by_stall_distance_m"]
    fc = ev["failure_cases_validation"]
    assert (
        fc["missed_incidents_total"]
        == val["episodes"]["incidents"] - val["episodes"]["incidents_detected"]
    )
    assert fc["missed_incidents_worst"], "the baseline is not perfect: misses must be listed"
    assert set(ev["all_variants"]) == {
        "occupancy_threshold",
        "occupancy_persistence",
        "occupancy_flow_speed",
    }
    assert ev["reference"]["never_alarm"]["recall"] == 0.0
    assert ev["selected_variant"] == json.loads(ARTIFACT.read_text(encoding="utf-8"))["variant"]
