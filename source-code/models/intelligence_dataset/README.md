# Traffic-intelligence dataset (Phase 06 shared input)

Real SUMO runs built because nothing long enough for 5/15/30-minute
forecasting, or with edge-wide ground truth for KPI validation and congestion
labels, existed: P03's runs are 600 s and P04's 900 s.

- 9 seeds, reusing P03.08's seed->split assignment (train 5 / validation 2 /
  test 2), x 4 runs per seed = **36 runs of 180 minutes** (233,280 loop events).
- Runs: `am-peak`, `pm-double`, `midday-steady` (incident-free, time-varying
  Poisson demand, 60-90 vehicles/min at peak) and `am-peak-blockage` (the
  am-peak shape plus three physical two-lane blockages, labels from SUMO's
  own stop-output). 0 teleports, 0 collisions in all 36.
- Per run: `events.jsonl` (30 s loop observation-envelope events - model and
  service input), `truth.jsonl` (SUMO edgeData per edge and 30 s - ground
  truth, never a feature), `labels.jsonl`/`incidents.jsonl` (blockage runs),
  `demand_summary.json`. Plus `segments.json` (from the real net file) and a
  `dataset_manifest.json` with a sha256 per file.
- Deterministic: one seed's four runs rebuilt independently are byte-identical
  (16 of 16 files).

Passenger vehicles only (no VRUs/transit); loops instrument one of each
corridor edge's two general lanes; demand shapes are synthetic - nothing here
supports a field-accuracy claim (`docs/PROJECT_CONTEXT.md`).

```bash
bash run_container.sh                        # full build, ~90 s (4 workers)
bash run_container.sh 20260918 --only am-peak  # smoke run
```
