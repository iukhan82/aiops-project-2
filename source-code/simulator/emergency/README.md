# P03.04: simulated emergency units and CAD/AVL events

Given `(seed, run_id, anchor_utc, sim_end)`, produces a deterministic set of
emergency calls, unit-to-call assignments, CAD/AVL devices and unit-position
telemetry for the P03.01 district network. Self-contained: does not depend on
P03.02/P03.03 output artifacts, only on `network/output/district.net.xml`
(P03.01).

## Scope (documented limitations)

- One station per agency, two units per agency (6 total): fire (`int-a1`),
  ambulance (`int-b1`), police (`int-c1`).
- Each unit's route is a single, real SUMO-router-computed trip (from-edge to
  the call's nearest incoming edge) - not a fabricated route, but also not a
  choice among multiple alternatives: `route_alternatives` always has exactly
  one, real entry. No route constraint modeling (vehicle-height, hazmat,
  bridge-weight limits) - the network carries no such attributes yet.
- Dispatch latency (8s) and acknowledgement latency (5s) are fixed constants,
  not a modeled CAD triage-time distribution.
- Scene/service duration per call type (ambulance 300s, fire 600s, police
  240s) is an illustrative fixed constant, not measured.
- No cross-agency handover or multi-unit staging scenario here - that is
  P03.05's multi-emergency scenario work.
- Deliberately, permanently patient-free: `call_subtype` is an operational
  category only (e.g. `structure_fire`, `medical_emergency`); no field
  anywhere carries patient identity or medical data (AGENTS.md). Verified by
  an explicit generator-side check and a host-side test.

## What's real vs. generated

| Data | Source |
|---|---|
| Call existence, type, subtype, priority, location, timing | seeded deterministic generator (`generate_scenario.py`) |
| Unit-to-call dispatch order | deterministic least-busy-unit assignment |
| Route distance, ETA, arrival time | real SUMO trip (`from`/`to` edge, auto-routed by SUMO's own router) - measured, not invented |
| AVL position/speed/heading | real SUMO FCD trace for that specific vehicle, sampled every 5s |

## Reproduction

```bash
bash source-code/simulator/emergency/run_container.sh
python -m pytest source-code/tests/test_emergency_contracts.py -q
```

Requires `source-code/simulator/network/output/district.net.xml` to already
exist. `build_and_verify.py` (runs inside the same pinned
`ghcr.io/eclipse-sumo/sumo` image as P03.01-P03.03):

1. Generates 9 calls (3 ambulance/3 fire/3 police) and dispatches them to 6
   units, `seed=20260918`, `run_id=p03-04-emergency`, `sim_end=900`.
2. Runs each dispatched unit as a real SUMO trip (auto-routed, `vClass="emergency"`)
   and reads back actual `duration`/`routeLength`/`arrival` from `tripinfo`
   output, and position/speed/heading from `fcd` output.
3. Assembles `emergency-call/v1`, `emergency-unit-assignment/v1`,
   `device/v1` (`emergency_cad_avl_adapter`) and `observation-envelope/v1`
   (`emergency.unit_position.avl`) records.
4. Scans every record for any `patient`-named field or value and fails the
   build if one is found.
5. Runs the whole pipeline twice and asserts calls/assignments/devices/AVL
   events are byte-identical across runs.
6. Asserts zero teleports and zero collisions for the emergency trips.

Schema conformance (against `contracts/emergency-call/v1`,
`contracts/emergency-unit-assignment/v1`, `contracts/device/v1`,
`contracts/observation-envelope/v1`) is checked separately on the host
pytest venv by `source-code/tests/test_emergency_contracts.py`, for the same
reason as P03.03: the pinned SUMO image has no `jsonschema` package.

Last verified run (2026-09-18), `sim_end=900`: 9 calls, 9 assignments, 6 AVL
devices, 238 AVL position events; 0 teleports, 0 collisions; calls,
assignments, devices and AVL events byte-identical across two runs; no
`patient` field/value anywhere; 6/6 host-side contract tests passed.

## Scope boundary

Traffic/safety scenario injection (collision, stall, wrong-way, flood, low
visibility) and multi-emergency scenarios are P03.05. Device/platform fault
injection is P03.06. Actual signal pre-emption / green-corridor control for
these units is out of scope for the simulator layer entirely - it is a
protected, authorized action for the CONTROL/emergency-response backend
services (AGENTS.md: no prototype component controls a public road; this
project's recommendation path cannot directly invoke an action adapter).
