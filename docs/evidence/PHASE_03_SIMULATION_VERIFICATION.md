# Phase 03 simulation verification (P03.09)

Machine-readable evidence for Phase 03's exit gate: "deterministic
normal/fault/emergency runs and leakage-safe datasets can be regenerated
with protected ground truth." Synthetic-data limitations are catalogued
separately in `SIMULATION_LIMITATIONS.md`.

## What was checked

`source-code/simulator/verification/verify_bounds.py` is a *new*
cross-stage check, not a re-run of each stage's own `build_and_verify.py`
(P03.03-P03.08 already each prove their own determinism/coverage claims in
their own READMEs). It checks properties that only make sense once you look
across all stages at once, over every event from P03.03/P03.04/P03.05/
P03.06 (1715 events, last verified run):

- **Geo-projection round trip**: every event's `latitude`/`longitude`,
  projected back to the network's local plane, lands inside the network's
  real bounding box (`convBoundary` from `district.net.xml`: `(0, 0, 900,
  800)`) - each stage only ever checked its own forward projection before
  this, never an end-to-end round trip.
- **Timestamp window**: every `observation_time` falls within the expected
  anchor-relative window.
- **Confidence range**: every measurement's `confidence` is within [0, 1]
  (also schema-enforced, checked again explicitly here as a bounds
  invariant in its own right).
- **Speed range**: every `m_s-1` measurement is within a generous sanity
  bound (40 m/s).
- **Device referential integrity**: every event's `device_id` actually
  exists in that stage's own device registry - files that are otherwise
  never cross-checked against each other.

Result: `bounds_report.json` (copied here) - **0 problems across all 1715
checked events, all 4 stages checked (none skipped)**.

## Visual check

`source-code/simulator/verification/plot_device_map.py` plots the P03.03
device catalog's 70 devices on the network's local plane, colored by
`device_type`. Visually inspected (2026-09-18): all device clusters land
exactly on the 12 expected intersections (x in {0, 300, 600, 900}, y in
{0, 400, 800}), matching the network's known layout; device-type counts in
the legend match the catalog's documented counts (18/18/16/12/3/3). Markers
overlap tightly at this zoom level where multiple device types share an
intersection (e.g. signal controller and road-condition sensor at the same
point) - expected, not a defect; disambiguating that would need a
per-intersection zoomed view, not necessary for this sanity check's
purpose.

## Other Phase 03 verification evidence (already proven by their own stages)

- `replay_report.json` (copied here, from P03.07): manifest self-verifies
  clean against disk; replay of 1715 merged events is deterministic across
  two independent runs and duplicate-safe under full self-duplication
  (same accepted hash/count, every duplicate rejected).
- `split_summary.json` (copied here, from P03.08): 9 seeds disjointly split
  5/2/2 across train/validation/test; 0 leakage problems (seed sets
  disjoint, no entity_id/event_id crosses a split boundary, no two splits
  byte-identical).

## Reproduction

```bash
python source-code/simulator/verification/build_and_verify.py
python -m pytest source-code/tests/test_scenario_bounds.py -q
```

Requires P03.03/P03.04/P03.05's `run_container.sh` and P03.06's
`build_and_verify.py` to have already produced their `output/run-a/`
(git-ignored; the JSON files in this folder are point-in-time committed
snapshots of a verified run, not regenerated on every check).
