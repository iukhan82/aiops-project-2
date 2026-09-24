# Phase 03 simulation limitations

Consolidated from `source-code/simulator/*/README.md`. Every item here is a
scope decision made deliberately, and every artifact these limitations
apply to carries `"truth_label": "simulated"`. No claim of field accuracy
follows from any of it (`docs/PROJECT_CONTEXT.md`: "Synthetic evaluation
cannot support field-accuracy claims").

## Network and demand (P03.01, P03.02)

- Synthetic 12-intersection, three-corridor grid, not a named real place.
  Confirming whether the platform's real target is this synthetic grid or a
  named public area with redistributable map data is an open decision
  (`Memory.md`).
- Cyclists are restricted to 6 single-corridor routes; cross-street edges
  carry no dedicated bike lane, so no bicycle-legal junction connection
  exists onto them.
- Demonstrated at modest scale (72 baseline entities over 600s); dataset-
  scale generation is P03.08 (also modest: 9 seeds).

## Sensor catalog and telemetry (P03.03)

- Traffic loop detectors and cycle counters sit on the 18 corridor edges
  only; the 8 cross-street edges carry no sensor in this catalog.
- Latitude/longitude are an equirectangular (flat-earth) projection of
  SUMO's local plane onto an arbitrary anchor point - acceptable for a
  ~900m x 800m synthetic district, not a geodetic claim about any real
  place.
- Pedestrian crossing demand is a seeded Poisson-like arrival process per
  crossing site, **not** derived from individual FCD pedestrian
  trajectories - there is no attempt to geometrically link a specific
  simulated pedestrian's walk to a specific crosswalk.
- Weather and road-condition readings are a seeded synthetic series
  (bounded sinusoid + noise), not a physical weather model.
- All telemetry is nominal/healthy by design; fault/anomaly injection is
  scoped separately to P03.06.

## Emergency units and CAD/AVL (P03.04)

- One station per agency, two units per agency (6 total) - not a realistic
  fleet size or station-coverage model.
- Each unit's route is a single, real SUMO-router-computed trip, not a
  choice among multiple alternatives - `route_alternatives` always has
  exactly one, real entry.
- No route-constraint modeling (vehicle-height, hazmat, bridge-weight
  limits) - the network carries no such attributes yet.
- Dispatch latency (8s) and acknowledgement latency (5s) are fixed
  constants, not a modeled CAD triage-time distribution.
- Scene/service duration per call type is an illustrative fixed constant,
  not measured.
- No cross-agency handover or multi-unit staging scenario.
- Route distance/ETA/AVL position *are* real, measured SUMO simulation
  outcomes - not invented.

## Traffic and safety scenarios (P03.05)

- Four scenario types (normal, peak, event, stall) are real SUMO runs with
  shaped demand: `peak`'s 2x multiplier and `event`'s 20-pedestrian surge
  are documented assumptions, not calibrated real-world ratios.
- Five scenario types (collision, wrong_way, flood, visibility, signal) are
  labeled two-event telemetry overlays against a real catalog device, with
  **no physical SUMO simulation** behind them: SUMO's car-following/safety
  model cannot produce a real crash or a real wrong-way maneuver without
  disabling core safety behavior (which would make the result an artifact
  of the hack, not evidence of anything), and SUMO has no flood/fog/
  cabinet-fault physics at all. These five are illustrative sensor
  evidence for one representative device, not a claim about how every
  instance of that condition would present.

## Device/platform faults (P03.06)

- All 7 fault types (silence, stuck, clock, network, model, service,
  storage) are the same labeled two-event overlay mechanism as P03.05's
  overlays - pure Python, no platform actually running yet to fault (Phase
  04+ builds the edge/backend services this would eventually monitor for
  real).
- `model`/`service`/`storage` faults attach to one synthetic `aiops_agent`
  device representing a not-yet-built self-observability agent, not a real
  running service.

## Run manifest and replay (P03.07)

- Replay is a simulator-layer proof of the ordered/idempotent/duplicate-
  safe *mechanism*, not the platform's real ingestion path (that is Phase
  05/07 backend work) - it operates on already-recorded files, not a live
  stream.
- The manifest indexes whichever `output/run-a/` each upstream stage most
  recently produced; it is not itself a trigger to regenerate them.

## Dataset splits (P03.08)

- Splits cover only the pure-Python-generated content (demand entities,
  device/platform faults) across 9 seeds. The SUMO-simulated content
  (P03.03 sensor telemetry, P03.04 AVL, P03.05's physical scenarios) is
  **not** multiplied across seeds and is **not** part of the split -
  multiplying real SUMO runs 9x was judged out of scope for this pass. That
  existing single-seed content remains available as reference material
  only, not as leakage-checked train/validation/test data.
- "Splits differ across routes/faults" means the specific records differ,
  not that route IDs or fault types are disjoint vocabulary - there are
  only 9 shared route templates and 7 required fault types, used by every
  split by design.

## Cross-stage verification (P03.09)

- Automated bounds checks (`source-code/simulator/verification/verify_bounds.py`)
  cover geo-projection round-trip, timestamp window, confidence range,
  speed range, and device referential integrity - not an exhaustive list of
  every possible data-quality invariant docs/SENSOR_AND_DATA_CATALOG.md
  section 10 names (e.g. calibration expiry, geometry-version mismatch
  after a network change are not separately exercised here, since nothing
  in Phase 03 changes the network version after generation).
- The device-map visual check (`plot_device_map.py`) needs `matplotlib`,
  installed ad hoc for this one diagnostic - not a committed runtime or
  test dependency, the same way P02.09's diagram rendering used an ad hoc
  `npx @mermaid-js/mermaid-cli` invocation.

## What is verified vs. simulated, in one line

Everything under `source-code/simulator/` is `"truth_label": "simulated"`.
Where a number is described above as "real" or "measured", it means SUMO's
own physics engine produced it (not that it was invented by hand) - it is
still a simulation output, not a field measurement, and cannot be used to
argue field accuracy for the eventual real deployment.
