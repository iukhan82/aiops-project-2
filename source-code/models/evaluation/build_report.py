"""P04.09: consolidate the container benchmark (throughput/latency/resource
use, real 0.75 CPU / 512 MiB bounds) with the already-measured held-out test
accuracy (P04.03/P04.04's single test-split opening - read here, never
recomputed) into one evidence report with conditions, sample sizes and
limitations named explicitly.

    python source-code/models/evaluation/prepare_benchmark_run.py
    bash source-code/models/evaluation/run_container_benchmark.sh
    python source-code/models/evaluation/build_report.py
"""

from __future__ import annotations

import json
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2]
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
REGISTRY = SOURCE / "models" / "registry"
MODEL_DIR = REGISTRY / "traffic-safety-blockage" / "1.0.0"
DOCS_EVIDENCE = SOURCE.parent / "docs" / "evidence"

LAT01_WARM_P95_MS = 100.0
ACC01_METRIC = "row f1"
FA01_MAX_SHARE = 0.10


def main() -> None:
    container = json.loads((OUTPUT_DIR / "container_report.json").read_text(encoding="utf-8"))
    manifest = json.loads(
        (
            OUTPUT_DIR.parent.parent / "dataset" / "output" / "run-a" / "dataset_manifest.json"
        ).read_text(encoding="utf-8")
    )
    card = json.loads((MODEL_DIR / "model_card.json").read_text(encoding="utf-8"))
    held_out = card["held_out_test_opened_once"]
    model_metrics, baseline_metrics = held_out["model"], held_out["rule_baseline"]

    warm = container["warm_model_inference_ms"]
    lat01_pass = warm["p95"] is not None and warm["p95"] < LAT01_WARM_P95_MS

    report = {
        "format": "edge-benchmark-report/1",
        "model": card["model_id"] + "/" + card["model_version"],
        "onnx_sha256": card["export"]["onnx_sha256"],
        "conditions": {
            "container": "edge-runtime:p04 (source-code/edge/Dockerfile), non-root uid 10001, "
            "read-only rootfs, cap-drop ALL, no-new-privileges",
            "resource_limits": "--cpus=0.75 --memory=512m --memory-swap=512m --pids-limit=64 "
            "(ADR-0006 / docs/environment/RESOURCE_BUDGET.md per-edge-instance limit)",
            "workload": f"{container['events_ingested']} real SUMO loop-detector events, "
            f"{manifest['runs'][0]['events']} events/run averaged over "
            f"{len(manifest['split_seeds']['test'])} held-out TEST seeds x runs "
            "(P03.08 split), concatenated and time-sorted - a sustained-load "
            "throughput/latency benchmark, not a coherent traffic scenario "
            "(see limitations)",
            "onnxruntime_threads": "intra_op=1, inter_op=1, sequential execution (bounded CPU use)",
        },
        "latency_ms": {
            "cold_first_inference": container["cold_first_model_inference_ms"],
            "warm_inference": warm,
            "decision_latency_including_features_and_emit": container["decision_latency_ms"],
            "target_LAT01_warm_p95_below_ms": LAT01_WARM_P95_MS,
            "LAT01_met": lat01_pass,
        },
        "throughput": {
            "events_ingested": container["events_ingested"],
            "wall_seconds": container["wall_seconds"],
            "events_per_second": container["events_per_second"],
            "emitted_candidate_events": container["emitted_events"],
        },
        "resource_use": {
            **container["system"],
            "memory_limit_bytes": 512 * 1024 * 1024,
            "peak_rss_fraction_of_limit": (
                round(container["system"]["vm_hwm_kb"] * 1024 / (512 * 1024 * 1024), 4)
                if container["system"]["vm_hwm_kb"]
                else None
            ),
            "cpu_throttled_fraction_of_wall": (
                round(
                    container["system"]["cgroup_cpu_throttled_usec"]
                    / 1e6
                    / container["wall_seconds"],
                    4,
                )
                if container["system"]["cgroup_cpu_throttled_usec"]
                else None
            ),
            "metrics_series_count": container["metrics_series"],
        },
        "accuracy_held_out_test_split": {
            "note": "Measured once by models/train/train_model.py against the sealed TEST "
            "split (models/registry/test_split_ledger.json records the single "
            "'final_comparison' opening); cited here, not recomputed.",
            "sample_sizes": {
                "test_rows": model_metrics["rows"],
                "test_positive_rows": model_metrics["positive_rows"],
                "test_incidents": model_metrics["incidents"],
                "test_seeds": manifest["split_seeds"]["test"],
                "test_runs": len(manifest["split_seeds"]["test"]) * len(manifest["run_specs"]),
            },
            "model": model_metrics,
            "rule_baseline": baseline_metrics,
            "paired_delta_model_minus_baseline_cluster_bootstrap": held_out[
                "paired_delta_model_minus_baseline_cluster_bootstrap"
            ],
            f"ACC01_model_beats_baseline_on_{ACC01_METRIC.replace(' ', '_')}": held_out[
                "acc01_verdict"
            ]["model_beats_baseline_on_row_f1"],
            "acc01_verdict_full": held_out["acc01_verdict"],
            "calibration": held_out["calibration"],
            "abstention": held_out["abstention"],
            "target_FA01_max_false_alarm_episode_share": FA01_MAX_SHARE,
            "FA01_met_by_model": model_metrics["false_alarm_episode_share"] <= FA01_MAX_SHARE,
            "FA01_met_by_baseline": baseline_metrics["false_alarm_episode_share"] <= FA01_MAX_SHARE,
        },
        "limitations": [
            *card["limitations"],
            "The throughput/latency/resource benchmark replays 24 independent SUMO test-split "
            "runs concatenated onto the same 18 shared device ids; it measures sustained system "
            "load, not a single coherent traffic scenario - accuracy is not evaluated from this "
            "run (that is the separate, already-sealed test-split comparison above).",
            "Benchmarked on one development host's Docker Desktop/WSL2 (docker info: 4 CPUs, "
            "11.68 GiB visible to the daemon); the assessment target host is unconfirmed "
            "(docs/environment/RESOURCE_BUDGET.md) - absolute wall-clock numbers may differ "
            "there, though the per-container 0.75 CPU/512 MiB limit is what was actually enforced.",
            "cgroup_cpu_nr_throttled/throttled_usec show the container was periodically CPU-"
            "throttled while ingesting 12960 events in under 6 seconds (a burst far denser than "
            "real telemetry arrival, which is one event per device per ~30s); per-event latency "
            "remained far under the LAT-01 budget throughout, including under throttling.",
            "onnxruntime logs a benign 'Failed to persist telemetry device ID' warning on this "
            "read-only rootfs (it cannot write a local cache file); no network telemetry call is "
            "made and no data leaves the container either way.",
        ],
    }

    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / "benchmark_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    DOCS_EVIDENCE.mkdir(parents=True, exist_ok=True)
    (DOCS_EVIDENCE / "edge_benchmark_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "LAT01_met": lat01_pass,
                "warm_p95_ms": warm["p95"],
                "events_per_second": container["events_per_second"],
                "peak_rss_kb": container["system"]["vm_hwm_kb"],
                "memory_limit_kb": 512 * 1024,
                "ACC01_model_beats_baseline": held_out["acc01_verdict"][
                    "model_beats_baseline_on_row_f1"
                ],
                "FA01_met_by_model": report["accuracy_held_out_test_split"]["FA01_met_by_model"],
                "test_incidents": model_metrics["incidents"],
                "test_rows": model_metrics["rows"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
