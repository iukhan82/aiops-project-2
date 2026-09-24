"""P04.09: validate the consolidated benchmark report - conditions, sample
sizes and limitations are present, and it cites (not recomputes) the sealed
test-split accuracy numbers."""

import json
from pathlib import Path

import pytest

REPORT_PATH = (
    Path(__file__).resolve().parents[1]
    / "models"
    / "evaluation"
    / "output"
    / "benchmark_report.json"
)
CARD_PATH = (
    Path(__file__).resolve().parents[1]
    / "models"
    / "registry"
    / "traffic-safety-blockage"
    / "1.0.0"
    / "model_card.json"
)


def _require_report() -> dict:
    if not REPORT_PATH.is_file():
        pytest.skip(
            "benchmark report not built; run prepare_benchmark_run.py, "
            "run_container_benchmark.sh, then build_report.py"
        )
    return json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def test_report_names_container_conditions_and_resource_limits() -> None:
    report = _require_report()
    conditions = report["conditions"]
    assert "0.75" in conditions["resource_limits"] and "512m" in conditions["resource_limits"]
    assert "read-only" in conditions["container"] and "non-root" in conditions["container"]
    assert conditions["workload"]


def test_latency_section_has_cold_and_warm_with_sample_sizes() -> None:
    report = _require_report()
    lat = report["latency_ms"]
    assert lat["cold_first_inference"] is not None and lat["cold_first_inference"] >= 0
    warm = lat["warm_inference"]
    assert warm["n"] > 100  # a real sample, not a single point
    assert warm["p50"] <= warm["p95"] <= warm["p99"] <= warm["max"]
    assert isinstance(lat["LAT01_met"], bool)
    assert lat["target_LAT01_warm_p95_below_ms"] == 100.0


def test_resource_use_reports_against_the_real_container_limit() -> None:
    report = _require_report()
    res = report["resource_use"]
    assert res["memory_limit_bytes"] == 512 * 1024 * 1024
    assert res["vm_hwm_kb"] is not None and res["vm_hwm_kb"] > 0
    assert 0 < res["peak_rss_fraction_of_limit"] < 1  # ran, and fit, inside the real budget


def test_throughput_section_is_a_real_positive_measurement() -> None:
    report = _require_report()
    tp = report["throughput"]
    assert tp["events_ingested"] > 1000
    assert tp["wall_seconds"] > 0
    assert tp["events_per_second"] == pytest.approx(
        tp["events_ingested"] / tp["wall_seconds"], rel=0.05
    )


def test_accuracy_section_cites_the_sealed_test_split_not_a_fresh_computation() -> None:
    report = _require_report()
    acc = report["accuracy_held_out_test_split"]
    assert "not recomputed" in acc["note"]
    sizes = acc["sample_sizes"]
    assert sizes["test_rows"] > 0 and sizes["test_incidents"] > 0
    assert len(sizes["test_seeds"]) == 2  # P03.08's test split
    for side in ("model", "rule_baseline"):
        m = acc[side]
        assert m["rows"] == sizes["test_rows"]
        assert 0.0 <= m["f1"] <= 1.0
        assert m["f1_ci95"][0] <= m["f1"] <= m["f1_ci95"][1]
    assert isinstance(acc["ACC01_model_beats_baseline_on_row_f1"], bool)
    assert isinstance(acc["FA01_met_by_model"], bool)
    assert isinstance(acc["FA01_met_by_baseline"], bool)


def test_report_matches_the_committed_model_card_it_cites() -> None:
    if not CARD_PATH.is_file():
        pytest.skip("model card not built")
    report = _require_report()
    card = json.loads(CARD_PATH.read_text(encoding="utf-8"))
    assert report["onnx_sha256"] == card["export"]["onnx_sha256"]
    assert (
        report["accuracy_held_out_test_split"]["model"]
        == card["held_out_test_opened_once"]["model"]
    )


def test_limitations_are_explicit_and_include_benchmark_specific_caveats() -> None:
    report = _require_report()
    limitations = report["limitations"]
    assert len(limitations) >= 4
    joined = " ".join(limitations).lower()
    assert "simulated" in joined
    assert "concatenat" in joined  # the multi-run benchmark methodology caveat
    assert any("host" in item.lower() for item in limitations)


def test_report_is_also_published_to_docs_evidence() -> None:
    _require_report()
    evidence_path = (
        Path(__file__).resolve().parents[2] / "docs" / "evidence" / "edge_benchmark_report.json"
    )
    if not evidence_path.is_file():
        pytest.skip("docs/evidence copy not written yet")
    assert json.loads(evidence_path.read_text(encoding="utf-8")) == json.loads(
        REPORT_PATH.read_text(encoding="utf-8")
    )
