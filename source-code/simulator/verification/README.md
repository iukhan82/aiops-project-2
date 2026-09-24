# P03.09: cross-stage bounds verification and limitations documentation

The last Phase 03 task: automated invariant checks across all of P03.03-
P03.06's output at once (not a re-run of each stage's own
`build_and_verify.py`, which already proves its own claims), a visual
sanity check, and a consolidated limitations document.

```bash
python source-code/simulator/verification/build_and_verify.py
python -m pytest source-code/tests/test_scenario_bounds.py -q
```

Requires P03.03/P03.04/P03.05's `run_container.sh` and P03.06's
`build_and_verify.py` to have already run (their `output/run-a/` is what
gets checked). Pure Python itself, no SUMO/Docker needed.

## What `verify_bounds.py` checks

Per event, across all 1715 events from `sensors/observations.jsonl`,
`emergency/avl_events.jsonl`, `scenarios/overlay_events.jsonl` and
`faults/fault_events.jsonl`:

1. **Geo-projection round trip**: `inverse_project(latitude, longitude)`
   lands inside the network's real bounding box (read from
   `district.net.xml`'s `convBoundary`, not hardcoded) - the first time
   any check goes forward-project-then-back across all four stages at
   once.
2. **Timestamp window**: `observation_time` falls within a generous
   anchor-relative window.
3. **Confidence range**: every measurement's `confidence` is in [0, 1].
4. **Speed range**: every `m_s-1` measurement is within 40 m/s.
5. **Device referential integrity**: every `device_id` actually exists in
   that stage's own device registry file.

`build_and_verify.py` requires all four stages present (a "skipped" result
for any of them is not a pass) and writes `output/bounds_report.json`.

## Visual check

`plot_device_map.py` renders the P03.03 catalog's 70 devices on the
network's local plane. It needs `matplotlib`, deliberately **not** added to
`source-code/requirements-*.txt` - it's a manual QA aid, not part of
`check.py` or the pytest suite, the same way P02.09's diagram rendering
used an ad hoc `npx @mermaid-js/mermaid-cli` invocation instead of a
committed dependency:

```bash
pip install matplotlib
python source-code/simulator/verification/plot_device_map.py
```

Findings from the last inspection are recorded in
`docs/evidence/PHASE_03_SIMULATION_VERIFICATION.md`.

## Where the rest of the evidence lives

- `docs/evidence/PHASE_03_SIMULATION_VERIFICATION.md` - what was checked,
  the visual-check findings, and copies of the machine-readable reports
  (`bounds_report.json` from here, plus P03.07's `replay_report.json` and
  P03.08's `split_summary.json`).
- `docs/evidence/SIMULATION_LIMITATIONS.md` - every documented
  simplification across P03.01-P03.09 in one place.

## Last verified run

2026-09-18: 1715 events checked across 4 stages, 0 problems, bbox
`(0, 0, 900, 800)`. 3/3 host-side tests passed.
