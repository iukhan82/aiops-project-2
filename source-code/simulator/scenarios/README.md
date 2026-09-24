# P03.05: traffic and safety scenarios with separate ground truth

Nine required scenario types (normal, peak, event, collision, stall,
wrong_way, flood, visibility, signal), each with a ground-truth record kept
separate from the telemetry/simulation output: `scenario_id`,
`scenario_type`, `onset`, `end`, `affected_entities`, `description`,
`truth_label`, `synthetic_overlay`. Ground truth is a plain internal JSON
shape (`ground_truth.jsonl`), not a new versioned contract - Phase 02's
15-contract set is closed, and this is scenario labeling, not a platform
interface.

## Two different mechanisms, by design

| Group | Scenarios | Mechanism |
|---|---|---|
| Physical | normal, peak, event, stall | a real SUMO run with a shaped demand file; ground truth for `stall` comes from SUMO's own measured `stop-output`, not an invented timestamp |
| Overlay | collision, wrong_way, flood, visibility, signal | two labeled `observation-envelope/v1` events (onset anomaly + end recovery) against a real P03.03-catalog device, with no physical SUMO simulation behind them |

**Why the split**: SUMO's car-following/safety model will not produce a real
crash or a real wrong-way maneuver on request - either requires disabling
core safety behavior in ways that make the result an artifact of the hack,
not evidence of anything. Flooding, low visibility and signal hardware
faults aren't things SUMO models physically at all (there is no water/fog/
cabinet-fault simulation). Faking any of these as SUMO physics would be a
worse claim than being explicit that they're a labeled sensor-evidence
overlay for downstream detection/ML work, which is what this platform
actually needs from "scenario data" for these five conditions. `stall`,
conversely, *is* something SUMO does natively and correctly (a scheduled
stop blocking a lane), so it is simulated for real, not overlaid.

## Physical scenarios (`generate_physical_demand.py`)

Reuses P03.02's route/edge tables (`demand/generate_demand.py`) - not its
fixed module-level counts, which none of these four scenarios want unchanged
- so this module writes its own entities and route files.

- **normal**: same demand shape as P03.02's baseline (40/12/16/4).
- **peak**: all counts doubled (documented assumption, not a calibrated
  peak/off-peak ratio).
- **event**: baseline demand plus 20 extra pedestrians walking
  `int-b2_int-b3` in a 200-260s window (a stand-in for "a nearby event let
  out", not a calibrated event-demand model).
- **stall**: baseline demand; the 5th-departing vehicle (deterministic
  selection) gets a scheduled 120s stop on a general lane mid-route. Ground
  truth onset/end are the *measured* `started`/`ended` attributes from
  SUMO's own `stop-output`, read back after the run - not computed in
  advance.

All four are asserted zero-teleport/zero-collision, including `peak` at 2x
demand and `stall` with a deliberately blocked lane.

## Overlay scenarios (`generate_overlay_events.py`)

Each picks one real device from the P03.03 catalog
(`sensors/build_sensor_catalog.py`, imported directly) and emits exactly two
`observation-envelope/v1` events: an onset reading (`quality: "suspect"` or
`"invalid"`) carrying an anomalous value, and an end reading
(`quality: "valid"`) carrying a normal-shaped value. These are illustrative
sensor evidence for one representative device, not a claim about how every
instance of that condition would present.

| Scenario | Device type used | What the onset reading shows |
|---|---|---|
| `collision` | `inductive_loop` | count drops to 0, `stopped_vehicle_flag: true` |
| `wrong_way` | `inductive_loop` (different device) | `direction_conflict: true`, negative signed speed |
| `flood` | `road_condition_sensor` | `surface_state: "flooded"`, friction collapses |
| `visibility` | `weather_station` | `visibility_distance` drops to 80m |
| `signal` | `signal_controller` | `signal_state: "fault"`, `active_phase: -1`, `quality: "invalid"` |

## Reproduction

```bash
bash source-code/simulator/scenarios/run_container.sh
python -m pytest source-code/tests/test_scenario_ground_truth.py -q
```

Requires `source-code/simulator/network/output/district.net.xml` (P03.01).
`build_and_verify.py` (runs inside the same pinned
`ghcr.io/eclipse-sumo/sumo` image as P03.01-P03.04):

1. Runs all 4 physical scenarios through SUMO, `seed=20260918`,
   `run_id=p03-05-scenarios`, `sim_end=600`; asserts zero teleports/collisions
   for each.
2. Builds the 5 overlay scenarios (pure Python, no SUMO).
3. Writes `ground_truth.jsonl` (9 records), `overlay_events.jsonl` (10
   events), `physical_summary.json` (per-physical-scenario SUMO run stats).
4. Runs the whole pipeline twice and asserts all three outputs are
   byte-identical across runs.
5. Asserts all 9 required scenario types are present.

Schema conformance for the overlay events (against
`contracts/observation-envelope/v1`) plus ground-truth field/determinism
checks are on the host pytest venv,
`source-code/tests/test_scenario_ground_truth.py` - same
jsonschema-availability split as P03.03/P03.04.

Last verified run (2026-09-18), `sim_end=600`: normal/event loaded 56
vehicles each, peak loaded 108 (2x, as expected), stall's chosen vehicle
measured stopped 86.0s-206.0s (120s duration); 0 teleports/0 collisions
across all 4 physical scenarios; 9/9 scenario types present; ground truth,
overlay events and physical summary all byte-identical across two runs;
8/8 host-side tests passed.

## Scope

Device/platform fault scenarios (silence, stuck, clock drift, network,
model, service, storage) are P03.06, not this task - `signal` here is a
sensor-reported controller fault, not a device/platform fault in the P03.06
sense (e.g. the controller's own health telemetry going silent). Run
manifests/replay and dataset splits are P03.07/P03.08.
