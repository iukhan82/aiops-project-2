# Phase 04 edge AI verification (P04.01-P04.09)

Machine-readable evidence for Phase 04's exit gate: "real optimized local
inference is measured; edge identity, buffering, replay and model lifecycle
withstand faults." Limitations are catalogued separately in
`EDGE_AI_LIMITATIONS.md`.

## What was built and what proves it

| Task | Built | Evidence |
|---|---|---|
| P04.01 | `edge/validation.py`, `edge/features.py`, `edge/topology.py` | `test_edge_validation.py` (schema/identity/sequence/staleness/clock/masking, incl. a 400-iteration fuzz test that never raises), `test_edge_features.py` + `test_edge_topology_features.py` (past-only, missing/degraded/insufficient, corridor context), `test_edge_real_telemetry.py` grounds both against real P03.03 telemetry - zero rejections across >1000 real events |
| P04.02 | `edge/baseline.py`, `models/baselines/fit_evaluate.py` | fitted on TRAIN, selected on VALIDATION with an explicit false-alarm-episode-share constraint; `models/registry/baseline/baseline_v1_evaluation.json` reports held-out metrics, CIs, and concrete failure cases; `test_edge_baseline.py` |
| P04.03 | `models/train/train_model.py`, `models/evaluation/metrics.py`, `models/evaluation/test_gate.py` | 30-candidate search on TRAIN, threshold/abstention on VALIDATION, one sealed TEST-split comparison (`test_split_ledger.json` has exactly one `final_comparison` entry); reproducible refit is bitwise-identical; `test_models_evaluation.py` |
| P04.04 | `models/export/export_onnx.py`, `edge/model_runtime.py` | ORT-vs-sklearn probability parity <=6.6e-7 (tolerance 1e-5), 0 decision mismatches away from thresholds, on every row of every split; `test_edge_model_runtime.py` (17 tests: golden vectors, 8 load-failure modes, provenance) |
| P04.05 | `edge/runtime.py`, `edge/health.py`, `edge/main.py`, `edge/Dockerfile` | real ONNX inference end to end against synthetic and real telemetry; abstention with reasons; automatic fallback to baseline on missing/tampered/erroring model; bounded Prometheus metrics (cardinality proven bounded under 3000 hostile device ids); `/healthz` `/readyz` `/metrics` HTTP; ran in the real 0.75 CPU/512 MiB non-root read-only container; `test_edge_runtime.py` (18 tests) |
| P04.06 | `edge/vision_privacy.py`, `edge/vision_devices.py` | privacy-zone suppression, per-window ephemeral identity (HMAC, boot-random salt), k-anonymity floor (default 3) - all proven on synthetic data AND grounded against a real recorded pedestrian trajectory from P03.02's SUMO output; `test_edge_vision_privacy.py` (25 tests) |
| P04.07 | `edge/outbox.py` | SQLite WAL, ordered/acknowledged/idempotent/bounded; crash recovery proven by never calling `close()` before reopening; a crash-before-ack resend is proven rejected downstream via the same `duplicate_event_id` check every other event source uses; `test_edge_outbox.py` (18 tests) |
| P04.08 | `edge/activation.py` | verify-then-commit ordering (refusal never touches the pointer), atomic `os.replace` pointer write (simulated crash mid-write leaves the old pointer intact), rollback re-verifies its target and refuses if it has since been corrupted; composes with `EdgeRuntime.swap_model`; `test_edge_activation.py` (17 tests) |
| P04.09 | `models/evaluation/{prepare_benchmark_run,run_container_benchmark.sh,build_report}.py` | cold/warm latency, throughput and resource use measured in the real bounded container against ~13,000 real test-split events; held-out accuracy cited from P04.03's sealed comparison, not recomputed; `test_edge_benchmark_report.py` (8 tests) |

## Headline numbers (last verified run, 2026-09-19)

- **Latency**: warm inference p95 = 0.135 ms (LAT-01 target: < 100 ms - met
  with over 700x margin); cold first inference = 0.174 ms.
- **Throughput**: 12,960 real events in 5.2s (~2,478 events/s) inside the
  container.
- **Resource use**: peak RSS 75.8 MB against a 512 MiB limit (14.5%); the
  container never exceeded its memory limit; CPU throttling occurred under
  the artificially dense benchmark load (see limitations) without any
  latency budget violation.
- **Accuracy (held-out TEST, 48 incidents / 12,528 rows)**: model F1 0.524
  vs. baseline F1 0.480 (ACC-01: model beats baseline on row F1 - met);
  model incident recall 0.60 (29/48) vs. baseline 0.50 (24/48).
  False-alarm-episode share: model 12.1%, baseline 10.7% (FA-01 target
  <=10% - both slightly over on this small held-out sample; see
  `EDGE_AI_LIMITATIONS.md`).
- **Determinism**: model refit from scratch reproduces bitwise-identical
  probabilities; the SUMO dataset (63 runs, 9 seeds) is byte-identical
  across two independent builds (`models/dataset/loader.verify_manifest()`
  passes).

## Reproduction

```bash
python -m pytest source-code/tests/test_edge_validation.py \
  source-code/tests/test_edge_features.py \
  source-code/tests/test_edge_topology_features.py \
  source-code/tests/test_edge_baseline.py \
  source-code/tests/test_edge_model_runtime.py \
  source-code/tests/test_edge_runtime.py \
  source-code/tests/test_edge_vision_privacy.py \
  source-code/tests/test_edge_outbox.py \
  source-code/tests/test_edge_activation.py \
  source-code/tests/test_edge_benchmark_report.py \
  source-code/tests/test_models_evaluation.py -q
```

All of the above run with no Docker/SUMO dependency once
`source-code/models/dataset/run_container.sh` and
`source-code/models/evaluation/run_container_benchmark.sh` have produced
their (git-ignored) output; each test skips cleanly, rather than failing,
when that output is absent.
