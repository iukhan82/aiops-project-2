"""P04.03/P04.04: released model package - verified loading, parity artifacts,
load-failure behavior, and honest provenance."""

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from edge.features import FEATURE_NAMES, FEATURE_VERSION
from edge.model_runtime import (
    ArtifactMissing,
    GoldenMismatch,
    InferenceError,
    IntegrityError,
    LoadError,
    SchemaMismatch,
    load_package,
)

REGISTRY = Path(__file__).resolve().parents[1] / "models" / "registry"
PACKAGE = REGISTRY / "traffic-safety-blockage" / "1.0.0"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy(tmp_path: Path) -> Path:
    dest = tmp_path / "pkg"
    shutil.copytree(PACKAGE, dest)
    return dest


def _rehash(pkg: Path) -> None:
    """Make the manifest agree with the (tampered) files so a later check fires."""
    manifest = json.loads((pkg / "artifact_manifest.json").read_text(encoding="utf-8"))
    for name in manifest["files"]:
        manifest["files"][name] = _sha(pkg / name)
    (pkg / "artifact_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _edit(pkg: Path, name: str, mutate) -> None:
    doc = json.loads((pkg / name).read_text(encoding="utf-8"))
    mutate(doc)
    (pkg / name).write_text(json.dumps(doc), encoding="utf-8")


# --- happy path -------------------------------------------------------------------


def test_released_package_loads_and_matches_runtime_feature_contract() -> None:
    model = load_package(PACKAGE, expected_feature_version=FEATURE_VERSION)
    ident = model.identity
    assert ident.model_id == "traffic-safety-blockage" and ident.model_version == "1.0.0"
    assert ident.feature_names == FEATURE_NAMES
    assert ident.onnx_sha256 == _sha(PACKAGE / "model.onnx")
    assert ident.label.startswith("traffic-safety-blockage/1.0.0@sha256:")
    assert 0 < ident.policy.abstain_lower < ident.policy.alarm_threshold < 1


def test_golden_vectors_reproduce_probabilities_and_decisions() -> None:
    model = load_package(PACKAGE)
    golden = json.loads((PACKAGE / "golden_vectors.json").read_text(encoding="utf-8"))
    x = np.asarray(golden["inputs"], dtype=np.float32)
    p = model.predict_proba(x)
    assert np.max(np.abs(p - np.asarray(golden["expected_probability"]))) < 1e-5
    decisions = [model.identity.policy.decide(v) for v in p]
    assert decisions == golden["expected_decision"]
    assert set(decisions) == {"alarm", "abstain", "clear"}


def test_single_vector_inference_returns_probability_decision_latency() -> None:
    model = load_package(PACKAGE)
    golden = json.loads((PACKAGE / "golden_vectors.json").read_text(encoding="utf-8"))
    p, decision, latency_ms = model.infer(np.asarray(golden["inputs"][0], dtype=np.float32))
    assert 0.0 <= p <= 1.0 and decision in {"alarm", "abstain", "clear"}
    assert 0.0 < latency_ms < 100.0  # ADR-0005 / LAT-01 budget; formally measured in P04.09


@pytest.mark.parametrize(
    "bad",
    [
        np.zeros((1, 23)),
        np.zeros((1, 25)),
        np.zeros((2, 3, 24)),
        np.full((1, 24), np.nan),
        np.full((1, 24), np.inf),
    ],
)
def test_malformed_or_non_finite_input_is_refused_not_guessed(bad: np.ndarray) -> None:
    with pytest.raises(InferenceError):
        load_package(PACKAGE).predict_proba(bad)


# --- load-failure behavior --------------------------------------------------------


def test_missing_manifest_or_file_is_artifact_missing(tmp_path: Path) -> None:
    pkg = _copy(tmp_path)
    (pkg / "model.onnx").unlink()
    with pytest.raises(ArtifactMissing, match="model.onnx"):
        load_package(pkg)
    (pkg / "artifact_manifest.json").unlink()
    with pytest.raises(ArtifactMissing, match="artifact_manifest.json"):
        load_package(pkg)
    with pytest.raises(ArtifactMissing):
        load_package(tmp_path / "does-not-exist")


def test_flipped_byte_and_truncation_fail_integrity(tmp_path: Path) -> None:
    pkg = _copy(tmp_path)
    data = bytearray((pkg / "model.onnx").read_bytes())
    data[len(data) // 2] ^= 0xFF
    (pkg / "model.onnx").write_bytes(bytes(data))
    with pytest.raises(IntegrityError, match="model.onnx"):
        load_package(pkg)
    (pkg / "model.onnx").write_bytes(bytes(data)[: len(data) // 2])
    with pytest.raises(IntegrityError):
        load_package(pkg)


def test_wrong_hash_in_manifest_fails_integrity(tmp_path: Path) -> None:
    pkg = _copy(tmp_path)
    _edit(
        pkg, "artifact_manifest.json", lambda d: d["files"].__setitem__("io_schema.json", "0" * 64)
    )
    with pytest.raises(IntegrityError, match="io_schema.json"):
        load_package(pkg)


def test_garbage_onnx_with_matching_hash_is_a_load_error(tmp_path: Path) -> None:
    pkg = _copy(tmp_path)
    (pkg / "model.onnx").write_bytes(b"this is not an onnx graph")
    _rehash(pkg)
    with pytest.raises(LoadError):
        load_package(pkg)


def test_schema_drift_is_refused(tmp_path: Path) -> None:
    pkg = _copy(tmp_path)
    _edit(pkg, "io_schema.json", lambda d: d["feature_names"].pop())
    _rehash(pkg)
    with pytest.raises(SchemaMismatch, match="input"):
        load_package(pkg)

    pkg2 = tmp_path / "pkg2"
    shutil.copytree(PACKAGE, pkg2)
    _edit(pkg2, "io_schema.json", lambda d: d["input"].__setitem__("name", "other"))
    _rehash(pkg2)
    with pytest.raises(SchemaMismatch):
        load_package(pkg2)


def test_feature_version_mismatch_between_model_and_runtime_is_refused() -> None:
    with pytest.raises(SchemaMismatch, match="runtime provides"):
        load_package(PACKAGE, expected_feature_version="loop-window/999")


def test_golden_vector_disagreement_catches_a_degraded_graph(tmp_path: Path) -> None:
    pkg = _copy(tmp_path)
    _edit(
        pkg,
        "golden_vectors.json",
        lambda d: d.__setitem__("expected_probability", [0.5] * len(d["expected_probability"])),
    )
    _rehash(pkg)
    with pytest.raises(GoldenMismatch):
        load_package(pkg)


def test_inconsistent_policy_and_unknown_format_are_refused(tmp_path: Path) -> None:
    pkg = _copy(tmp_path)
    _edit(pkg, "artifact_manifest.json", lambda d: d["identity"].__setitem__("abstain_lower", 0.99))
    with pytest.raises(SchemaMismatch, match="thresholds"):
        load_package(pkg)
    pkg2 = tmp_path / "pkg2"
    shutil.copytree(PACKAGE, pkg2)
    _edit(pkg2, "artifact_manifest.json", lambda d: d.__setitem__("format", "edge-model-package/9"))
    with pytest.raises(SchemaMismatch, match="format"):
        load_package(pkg2)


# --- provenance and honesty of the released artifacts -----------------------------


def _card() -> dict:
    return json.loads((PACKAGE / "model_card.json").read_text(encoding="utf-8"))


def test_model_card_records_provenance_parity_and_limitations() -> None:
    card = _card()
    assert card["export"]["onnx_sha256"] == _sha(PACKAGE / "model.onnx")
    parity = card["export"]["parity"]
    assert parity["max_abs_probability_diff_vs_sklearn"] <= parity["tolerance"] == 1e-5
    assert parity["decision_mismatches_away_from_thresholds"] == 0
    assert set(parity["per_split"]) == {"train", "validation", "test"}
    prov = card["provenance"]
    assert prov["reproducible_refit_bitwise_identical"] is True
    assert prov["procedure"]["fit"] == "train split only"
    seeds = prov["split_seeds"]
    all_seeds = [s for v in seeds.values() for s in v]
    assert len(all_seeds) == len(set(all_seeds))
    assert len(card["limitations"]) >= 5
    assert any("simulated" in lim.lower() for lim in card["limitations"])


def test_model_card_reports_the_baseline_comparison_whether_or_not_it_wins() -> None:
    held = _card()["held_out_test_opened_once"]
    model, base = held["model"], held["rule_baseline"]
    for m in (model, base):
        assert m["rows"] == base["rows"] and m["incidents"] == base["incidents"]
        assert 0 <= m["f1"] <= 1 and m["f1_ci95"][0] <= m["f1"] <= m["f1_ci95"][1]
    verdict = held["acc01_verdict"]
    assert verdict["model_beats_baseline_on_row_f1"] == (model["f1"] > base["f1"])
    assert verdict["model_beats_baseline_on_incident_recall"] == (
        model["incident_recall"] > base["incident_recall"]
    )
    delta = held["paired_delta_model_minus_baseline_cluster_bootstrap"]["f1"]
    assert delta["ci95"][0] <= delta["mean"] <= delta["ci95"][1]
    assert {"brier", "ece"} <= set(held["calibration"])


def test_selection_was_made_from_validation_only_and_is_reproducible_from_the_report() -> None:
    report = json.loads((PACKAGE / "training_report.json").read_text(encoding="utf-8"))
    candidates = report["candidates_validation"]
    assert len(candidates) == report["procedure"]["candidates_tried"] == 30
    best = max(
        candidates,
        key=lambda c: (
            c["validation_at_best_threshold"]["f1"],
            c["validation_average_precision"],
        ),
    )
    assert report["selected"]["index"] == best["index"]
    assert report["procedure"]["test"].startswith("opened once")
    assert "validation only" in report["procedure"]["tuning"]
    policy = report["decision_policy"]
    assert policy["abstain_if_probability_in"][1] == policy["alarm_if_probability_at_least"]
    assert report["data"]["rows"]["train"] > report["data"]["rows"]["validation"]


def test_test_split_ledger_shows_one_selecting_opening_and_logged_parity_use() -> None:
    """Selecting purposes are guarded per (purpose, dataset_sha256, feature_version): other phases
    (e.g. P06.03's forecast model) legitimately open their own 'final_comparison' under a different
    feature version, so only P04's edge-model opening is asserted here to be exactly one and unreopened."""
    ledger = json.loads((REGISTRY / "test_split_ledger.json").read_text(encoding="utf-8"))
    purposes = [e["purpose"] for e in ledger["entries"]]
    assert "onnx_parity" in purposes
    finals = [
        e
        for e in ledger["entries"]
        if e["purpose"] == "final_comparison" and e["feature_version"] == FEATURE_VERSION
    ]
    assert len(finals) == 1
    assert all("reopen_reason" not in e for e in finals)
    assert finals[0]["feature_version"] == FEATURE_VERSION
