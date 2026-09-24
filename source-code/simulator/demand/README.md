# P03.02: deterministic demand and clock

Given `(seed, run_id, anchor_utc, sim_end)`, `generate_demand.py` produces the
full road-user population for the P03.01 district - vehicles, cyclists,
pedestrians and transit - and a manifest mapping simulation time to UTC
(`observation_time = anchor_utc + depart_seconds`). Pure Python, no SUMO
dependency; it only needs the corridor/cross-street edge IDs from
`source-code/simulator/network/plain/district.edg.xml`.

Cyclists are restricted to the 6 single-corridor routes: cross-street edges
carry no dedicated bike lane (`source-code/simulator/network/README.md`), so
no bicycle-legal junction connection exists onto them. Using the full
vehicle route pool for cyclists produces "no valid route" errors at
simulation time; `CYCLIST_ROUTES` in `generate_demand.py` is the fix, not a
workaround to route around later.

## Reproduction

```bash
bash source-code/simulator/demand/run_container.sh
```

Requires `source-code/simulator/network/output/district.net.xml` to already
exist (run `network/run_container.sh` first). `build_and_verify.py`:

1. Calls `generate()` twice with identical arguments into separate
   directories and asserts the route file and manifest are byte-identical
   (generator determinism, independent of SUMO).
2. Asserts all four road-user classes are present (count > 0 each).
3. Independently recomputes `anchor_utc + depart_seconds` for two sample
   entities and asserts it matches the manifest's `observation_time`.
4. Runs the generated demand through SUMO twice with a fixed seed and
   compares semantic hashes of `fcd-output` and `stop-output` (excluding
   SUMO's non-deterministic generated-at header and, deliberately, the
   statistics file's wall-clock `<performance>` block, which differs between
   runs by design and is not part of the simulation result).
5. Asserts zero teleports, zero collisions, and that all four transit stops
   (two lines) were completed.

Last verified run (2026-09-18), `seed=20260918`, `run_id=p03-02-verify`,
`anchor_utc=2026-09-18T09:00:00Z`, `sim_end=600`: 40 vehicles, 12 cyclists,
16 pedestrians, 4 transit vehicles; generator and simulation both
byte-identical across repeats; 0 teleports, 0 collisions; all 4 stops
completed; clock samples verified (`depart_s=0` to `09:00:00Z`,
`depart_s=580` to `09:09:40Z`).

## Scope

This proves determinism and road-user-class coverage at a modest scale (72
entities over 600s). Dataset-scale generation, splits and leakage protection
are P03.08; sensor telemetry conforming to
`source-code/contracts/observation-envelope/v1/` is P03.03; run manifests
and replay-equivalence infrastructure are P03.07.
