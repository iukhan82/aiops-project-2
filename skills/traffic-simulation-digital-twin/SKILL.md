---
name: traffic-simulation-digital-twin
description: Build reproducible SUMO traffic, pedestrian, weather, emergency, sensor, and fault scenarios with separate ground truth. Use for the project digital twin, simulated devices, datasets, and scenario-control behavior.
---

# Traffic Simulation and Digital Twin Engineer

Read project context plus the sensor catalog before work. Own
`source-code/simulator/`, simulation inputs, scenario manifests, ground truth and
dataset generation. Simulation control is separate from operational remediation.

## Workflow

1. Build a versioned 12-intersection/three-corridor SUMO baseline with vehicles,
   pedestrians, cyclists, transit and emergency units.
2. Generate loop/radar/signal/weather/road/controller/emergency telemetry with
   deterministic seed, run ID and clock. Carry units, provenance and quality.
3. Implement normal, peak, event, collision, stalled, wrong-way, flooding, low
   visibility, signal/device/network and multi-emergency scenarios.
4. Keep labels, scenario names and future information out of model inputs. Store
   ground truth separately with onset/end and affected entities.
5. Create disjoint train/validation/test splits across seeds, routes, demand and
   faults; record source hashes and licences.
6. Expose bounded start/status/reset/replay controls with conflicts, authorization
   hooks and audit, clearly labelled as demo functions.

## Verification and handoff

Prove repeatability, physical/temporal bounds, scenario truth, split leakage
protection and replay equivalence. Document simulator limitations; no field-accuracy
claim follows from synthetic data. Update register and memory with exact commands,
hashes, results and next action.
