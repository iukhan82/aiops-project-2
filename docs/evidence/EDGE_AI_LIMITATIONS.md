# Phase 04 edge AI limitations

Consolidated from `source-code/edge/README.md`, `source-code/models/README.md`
and the committed model card/benchmark report. Every artifact this applies
to carries `"truth_label": "simulated"` or `"inferred"`; no field-accuracy
claim follows (`docs/PROJECT_CONTEXT.md`).

## Dataset (P04.02-P04.03's training data)

- One physical mechanism only: a two-lane blockage 25/45/70m past a loop
  detector, on the synthetic 12-intersection network. Generalization to
  other blockage geometries, network layouts, or a real deployment is
  untested.
- Labels are ground truth ("blockage active", from SUMO's own measured
  stop-output), not "detectable yet": at low flow or long blockage-to-loop
  distance, the queue never physically reaches the detector and no
  loop-based method - baseline or model - can see the incident. This is
  reflected directly in recall-by-distance/by-demand-scale figures, not
  hidden behind an aggregate number.
- 63 SUMO runs across 9 seeds (train 5/validation 2/test 2, reusing P03.08's
  split) is a modest sample; the held-out test set has 48 incidents across
  24 runs, so confidence intervals on test metrics are wide.

## Baseline and model (P04.02, P04.03)

- The rule baseline's grid search and the model's candidate search both
  optimize under a shared operating constraint (alarm-episode false share
  <= 10%, mirroring ACCEPTANCE_TARGETS.md's FA-01) measured on VALIDATION;
  on the held-out TEST split both detectors ended slightly *above* that
  target (model 12.1%, baseline 10.7%) - reported as measured, not adjusted
  to look better, per the small-sample point above.
- Calibration (Brier score, ECE) and the abstention band are fitted once on
  VALIDATION and applied as-is to TEST; they are not re-tuned per condition.
- The comparison against the rule baseline is reported whichever way it
  goes (ACCEPTANCE_TARGETS.md ACC-01); see `model_card.json`'s
  `held_out_test_opened_once.acc01_verdict`.

## Edge runtime (P04.01, P04.05)

- Feature windows use only this one edge site's own corridor-neighbor
  topology (immediate upstream/downstream loops); no city-wide or
  multi-corridor context exists.
- The decision-tick semantics (ingest everything at one `observation_time`,
  then evaluate) assume events for one timestamp arrive together; a sensor
  clock far out of sync would evaluate that device's tick late, not
  incorrectly - `edge.validation`'s clock/staleness flags surface this
  rather than hide it.

## Privacy-preserving vision path (P04.06)

- Implemented and tested (including against a real recorded pedestrian
  trajectory from P03.02's SUMO output) as a mechanism: privacy-zone
  suppression, per-window ephemeral identity, and a k-anonymity floor. No
  camera hardware, real vision model, or real camera feed exists or is
  assumed - `edge_camera` devices and their placement
  (`edge/vision_devices.py`) are illustrative, not a real siting.
- The k-anonymity floor (default 3) and zone geometry are chosen for
  demonstration, not calibrated against any privacy-risk analysis.

## Durable outbox (P04.07)

- Proven ordered/acknowledged/idempotent/bounded, and that a crash-then-
  resend is rejected downstream as a duplicate (via the same
  `duplicate_event_id` check every other event source in this project goes
  through) - but the outbox itself cannot prevent a duplicate *delivery* to
  a downstream system if the crash happens between successful delivery and
  the local ack being recorded; it guarantees the resend is *detectable* as
  a duplicate by carrying the same `event_id`, not that no resend is ever
  attempted.
- Single-writer/single-reader SQLite file; not a multi-writer queue (not
  needed - one process per logical edge instance).

## Atomic activation (P04.08)

- Atomicity is proven for the local pointer file (`os.replace`) and for
  refuse-before-commit; it does not cover a distributed multi-edge rollout
  (e.g. "activate version X on all edges atomically together") - each edge
  instance's activation is independent.

## Benchmark (P04.09)

- The throughput/latency/resource benchmark replays 24 independent
  SUMO test-split runs concatenated onto the same 18 shared device ids to
  get a sustained load; it measures system performance, not detection
  accuracy in one coherent scenario (accuracy is the separate, already-
  sealed test-split comparison, cited not recomputed).
- Measured on one development host's Docker Desktop/WSL2 (4 CPUs, 11.68 GiB
  visible to the daemon); the assessment target host is unconfirmed
  (`docs/environment/RESOURCE_BUDGET.md`). The per-container 0.75 CPU/512 MiB
  limit was actually enforced (cgroup-verified); absolute wall-clock numbers
  may differ on the eventual target.
- The benchmark ingests ~13,000 events in under 6 seconds - far denser than
  real telemetry arrival (about one event per device per 30s) - which
  causes visible cgroup CPU throttling; per-event latency stayed far under
  the LAT-01 budget throughout, including while throttled, but this is a
  stress condition, not steady-state operation.
