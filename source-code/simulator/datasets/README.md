# P03.08: disjoint train/validation/test datasets with leakage checks

Nine seeds, assigned disjointly to three splits by construction:

| Split | Seeds |
|---|---|
| `train` | 20260918, 20260919, 20260920, 20260921, 20260922 |
| `validation` | 20260923, 20260924 |
| `test` | 20260925, 20260926 |

Pure Python, no SUMO/Docker needed - reuses P03.05's seed-parametrized
demand generator and P03.06's fault generator (now also seed-parametrized;
see below):

```bash
python source-code/simulator/datasets/build_and_verify.py
python -m pytest source-code/tests/test_dataset_splits.py -q
```

## What's in a "unit" (one seed)

`generate_dataset_units.py` produces, per seed: a demand-entity bundle
(vehicles/cyclists/pedestrians/transit - route and depart time, via
`scenarios/generate_physical_demand.generate_scaled_entities`) and a
device/platform-fault bundle (`faults/generate_fault_events.build_faults`).
Every unit's `run_id` is `p03-08-<split>-seed-<seed>`, which is what
actually makes every `entity_id`/`event_id` split-unique - disjoint seed
*lists* alone don't guarantee that without the run_id embedding the split.

Deliberately excluded: the SUMO-simulated content (P03.03 sensor telemetry,
P03.04 AVL, P03.05's physically-simulated scenarios). Multiplying real SUMO
runs across 9 seeds is out of scope for this pass - each is a real container
invocation, and this task is about the split/leakage *mechanism*, not about
re-deriving simulation fidelity at 9x the cost. Those stages' existing
single-seed output remains available as reference material; it is simply
not part of this split. A future pass could extend `run_container.sh`-based
regeneration across multiple seeds if a larger physically-simulated dataset
is needed.

## A necessary change to P03.06

P03.06's fault generator originally produced *identical* device selections
and onset/end timing regardless of `run_id` (only `event_id`s differed,
via the run_id string going into a uuid5 hash) - fine for a single
verification run, but useless for "splits differ across seeds" here.
`build_faults` now takes a `seed` parameter that perturbs device selection
and onset timing (duration stays fixed per fault type) via a seeded RNG, so
different seeds produce genuinely different fault records, not just
different labels on identical content. P03.06's own tests
(`source-code/tests/test_fault_ground_truth.py`) were re-run and still pass
unchanged - they check structural properties, not specific device/timing
values.

## What "leakage check" means here

`check_leakage` in `build_and_verify.py` asserts:

1. The three splits' seed sets are pairwise disjoint (by construction, but
   checked).
2. No `entity_id` (demand) and no `event_id` (fault events) appears in more
   than one split's output file.
3. No two splits produced byte-identical `fault_events.jsonl` content
   (seeds actually varied the output, not just relabeled it).

**What it deliberately does not mean**: that route IDs or fault types are
disjoint across splits. There are only 9 shared route templates
(`demand/generate_demand.py`) and exactly 7 required fault types - every
split necessarily draws from the same finite vocabulary, by design (roads
and fault categories are shared infrastructure, not per-split resources).
"Splits differ across seeds/routes/demand/faults" means the *specific
records* (which entity got which route at what time; which device faulted
when, with what values) differ, which the seed-driven generation and the
byte-identical-content check both demonstrate.

## Reproduction

Last verified run (2026-09-18): train 360 demand records / 35 fault ground
truth / 70 fault events (5 seeds); validation and test 144 demand records /
14 fault ground truth / 28 fault events each (2 seeds each); 0 leakage
problems; all three splits' content mutually distinct; every split covers
all 7 fault types; every fault event validates against
`contracts/observation-envelope/v1`; both full runs byte-identical.
7/7 host-side tests passed.

## Scope

Automated scenario-bound/invariant checks across all of P03.01-P03.08 and
documenting synthetic-data limitations in one place is P03.09, the last
Phase 03 task.
