# Edge model training and evaluation (Phase 04, P04.02-P04.04, P04.09)

Training/evaluation only - the serving path is `source-code/edge/`. Nothing
here runs at the edge; nothing in `edge/` imports from here.

| Directory | Task | What it does |
|---|---|---|
| `dataset/` | - | real SUMO road-blockage dataset (63 runs, 9 seeds) feeding both the baseline and the model; `loader.py` builds features via the *exact same* `edge.features`/`edge.validation` code the runtime uses |
| `evaluation/` | P04.02, P04.09 | shared metrics (row/episode/sliced, cluster-bootstrap CIs), the test-split-opening ledger, and the container benchmark pipeline |
| `baselines/` | P04.02 | `fit_evaluate.py`: grid-search the transparent rule baseline on TRAIN, select on VALIDATION |
| `train/` | P04.03 | `train_model.py`: candidate search (30 configs) on TRAIN, threshold/abstention policy on VALIDATION, the *one* sealed TEST-split comparison against the baseline |
| `export/` | P04.04 | `export_onnx.py`: ONNX conversion, ORT-vs-sklearn parity proof, golden vectors, the hash-pinned package |
| `registry/` | - | committed artifacts: `baseline/`, `traffic-safety-blockage/1.0.0/` (model + `model_card.json`), `test_split_ledger.json` |

## The road-blockage task

Detect an active two-lane blockage on a corridor segment from one loop
detector's last four 30s intervals plus its upstream/downstream neighbor
loops (`edge.features`, `loop-window/2`, 24 features). Labels come from
SUMO's own measured stop-output for two deliberately stalled vehicles - not
from what was planned - so a label is ground truth ("blockage active"), not
"detectable yet": queue physics mean low-flow or long-distance incidents are
inherently seen late or not at all by a single loop (see the model card's
`limitations` and `recall_by_stall_distance_m`).

## Reproduction, in order

```bash
bash dataset/run_container.sh                        # 1. real SUMO dataset (needs Docker)
python baselines/fit_evaluate.py                      # 2. rule baseline (TRAIN fit, VALIDATION select)
python train/train_model.py --dry-run                 # 3. candidate search, test split still sealed
python train/train_model.py                            #    the ONE real test-split opening
python export/export_onnx.py                           # 4. ONNX export + parity + package + model card
python evaluation/prepare_benchmark_run.py              # 5. benchmark
bash evaluation/run_container_benchmark.sh              #    (needs the edge-runtime:p04 image)
python evaluation/build_report.py
```

Each step reads the previous step's committed output and re-verifies it
(dataset manifest hash, estimator hash, model card) rather than trusting
that it was run correctly - a stale or hand-edited artifact fails loudly.

## Why the test split is a ledger, not just a convention

`evaluation/test_gate.py` records every opening of the TEST split.
`"final_comparison"` (the model-vs-baseline decision) is allowed exactly
once per `(dataset_sha256, feature_version)` unless a `reopen_reason` is
explicitly given - a code-enforced version of ADR-0005's "train/validate
discipline before complex ML", not just a documented rule. Non-selecting
uses (e.g. `export_onnx.py`'s ORT-vs-sklearn parity check, which reads
every split's features but does not choose anything) are logged too, just
not rate-limited.

## Evidence

- `registry/traffic-safety-blockage/1.0.0/model_card.json` - provenance,
  held-out test metrics (model vs. baseline, with confidence intervals and
  sample sizes), calibration, and explicit limitations.
- `evaluation/output/benchmark_report.json` (copied to
  `docs/evidence/edge_benchmark_report.json`) - cold/warm latency,
  throughput and resource use under the real 0.75 CPU/512 MiB container
  budget, plus the cited (not recomputed) held-out accuracy.
- `docs/evidence/EDGE_AI_LIMITATIONS.md` - every documented simplification
  across P04.01-P04.09 in one place.
