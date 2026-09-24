# Edge runtime (Phase 04, P04.01-P04.08)

Serving path only: validated intake -> past-only features -> verified ONNX
inference (with transparent-baseline fallback and abstention) -> a durable
offline outbox -> bounded metrics/health. Training/evaluation/export live in
`source-code/models/` (P04.02-P04.04, P04.09); nothing here trains anything.

| Module | Task | What it does |
|---|---|---|
| `timeutil.py`, `contracts.py` | - | strict RFC3339 UTC parsing; loads `contracts/*/v1/schema.json` |
| `validation.py` | P04.01 | `EdgeValidator`: schema, identity, sequence, staleness, clock, masked measurements - never raises, always a `Verdict` |
| `features.py`, `topology.py` | P04.01 | past-only 4x30s windows + corridor-neighbor context (24 features, `loop-window/2`); missing data is counted, never invented |
| `baseline.py` | P04.02 | transparent rule detector (3 variants); the runtime's fallback when no model is verified |
| `model_runtime.py` | P04.04 | loads a hash-pinned ONNX package; refuses on any integrity/schema/golden-vector mismatch |
| `runtime.py` | P04.05 | `EdgeRuntime`: wires validator + features + model/baseline + bounded Prometheus metrics + health |
| `health.py` | P04.05 | dependency-free `/healthz` `/readyz` `/metrics` HTTP server |
| `vision_privacy.py`, `vision_devices.py` | P04.06 | privacy-zone suppression, per-window ephemeral identity, k-anonymity floor for camera-derived metadata |
| `outbox.py` | P04.07 | SQLite WAL durable outbox (ADR-0006): ordered, acknowledged, idempotent, bounded |
| `activation.py` | P04.08 | atomic (`os.replace`) model version pointer + rollback, verify-before-commit |
| `main.py` | - | `python -m edge.main replay --config ... --events ...` - the runtime driver used for verification and benchmarking |
| `Dockerfile`, `requirements-lock.txt` | P04.05 | non-root, read-only-rootfs container; runtime-only pinned deps (no training libraries) |

## Reproduction

```bash
# from source-code/
python -m edge.main replay --config CONFIG.json --events EVENTS.jsonl \
    --out OUT.jsonl --report REPORT.json [--outbox PATH.sqlite3] [--capture-latency]

# or in the real bounded container (built from this Dockerfile):
docker build -f edge/Dockerfile -t edge-runtime:p04 .
docker run --rm --user 10001:10001 --cpus=0.75 --memory=512m --read-only --tmpfs /tmp \
    -v ...:/data/... edge-runtime:p04 replay --config /data/config.json --events /data/events.jsonl
```

CONFIG.json: `{"site_id", "runtime_device_id", "geometry_version",
"registry_path", "baseline_path", "model_dir" | null, "emit_clear",
"boot_id"}`.

## Tests

`source-code/tests/test_edge_*.py` (pure unit/integration, no Docker needed)
plus `test_edge_real_telemetry.py` and `test_edge_vision_privacy.py`'s last
section, which ground the validator/features and the privacy-zone/identity-
rotation guarantees against real SUMO-simulated telemetry (P03.03's
`observations.jsonl`, P03.02's `sim-run-1-fcd.xml`) when that gitignored
output is present, and skip cleanly otherwise.

## Design notes worth knowing before changing this code

- **Fail-safe, never silent.** Invalid input is rejected with a stable
  reason code; degraded input is accepted-with-flags; insufficient evidence
  abstains with a reason. A model that cannot be verified (or that starts
  erroring) drops to the rule baseline automatically
  (`EdgeRuntime._decide` / `disable_model`); if neither is available the
  runtime reports not-ready and abstains explicitly
  (`no_decision_path`) - it never guesses and never goes quiet.
- **Bounded everything.** Fixed-size feature history, a capped duplicate-
  event memory, fixed Prometheus label allow-lists (hostile input collapses
  to `"other"`, never a new series - `RESOURCE_BUDGET.md`'s "vehicle/track
  IDs never become metric labels" rule, generalized), one ONNX thread, a
  quota-enforced outbox.
- **Verify, then commit - never the other way round.** `model_runtime.
  load_package`, `activation.ModelActivator.activate/rollback`, and
  `EdgeRuntime.swap_model` all check first and only touch durable/live state
  on success; a refusal changes nothing.
- **The output events are candidates, not actions.** `truth_label:
  "inferred"`, `event_type: "edge.inference.blockage_candidate"` - no
  actuator exists in this module (AGENTS.md: "no prototype component
  controls a public road").
