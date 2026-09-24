# ADR-0001: Simulator platform

- **Status:** Accepted
- **Date:** 2026-09-18
- **Deciders:** ARCH, with SIM as implementing owner

## Context

The platform needs a reproducible digital twin of a 12-intersection,
three-corridor urban district generating vehicle, pedestrian, cyclist,
transit, and emergency-unit movement, plus signal, weather, and road-sensor
telemetry (`docs/PROJECT_CONTEXT.md`, `docs/SENSOR_AND_DATA_CATALOG.md`).
Task P01.06 must prove headless, deterministic, telemetry-producing operation
on the supported WSL2 development route before P03.01 builds the versioned
baseline network.

## Decision drivers

- Deterministic, seed-reproducible runs for disjoint train/validation/test
  splits (P03.08) and replay equivalence (P03.07).
- Headless operation inside WSL2/K3s without a GPU or licensed workstation
  (`docs/environment/WORKSTATION_INVENTORY.md` records no discrete GPU).
- Traffic-domain fidelity: signal phases, lane-level detectors, multimodal
  demand (vehicles, pedestrians, cyclists, transit).
- No cost or licensing barrier for an academic, reproducible, publishable
  project.
- Scriptable network generation and programmatic control (TraCI/libsumo) for
  scenario and fault injection (P03.05, P03.06).

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Eclipse SUMO** | Open source (EPL-2.0/GPL-2.0), headless CLI, deterministic with an explicit seed, TraCI/libsumo Python control, native pedestrian/cyclist/transit/signal modeling, active DLR maintenance, installable without root via the `eclipse-sumo` PyPI wheel | Microscopic model is an abstraction of real driver behavior; no built-in sensor-noise model (must be added) |
| CARLA | High-fidelity 3D/vision simulation, useful for camera-based perception research | Requires a discrete GPU and Unreal Engine runtime, far exceeds the measured workstation budget, overkill for network-level traffic/signal control |
| PTV VISSIM | Industry-standard microscopic simulation, strong signal-control tooling | Commercial license, not redistributable in a public academic repository, blocks reproducibility for examiners without a license |
| Custom discrete-event simulator | Full control over telemetry shape | Reimplements car-following, lane-changing, and signal logic that SUMO already provides and validates; large unscoped effort with no fidelity benefit |

## Decision

Use Eclipse SUMO (`sumo`, `netconvert`, `netgenerate`, TraCI/libsumo) as the
sole traffic simulator, run headless inside WSL2 for development and inside
the K3s target for the assessment profile, installed via the `eclipse-sumo`
PyPI package pinned to a recorded version. `sumo-gui` remains an optional
local debugging aid only, per `docs/environment/TOPOLOGY.md`.

## Consequences

- All demand, network, and scenario generation scripts live under
  `source-code/simulator/` (SIM ownership, `docs/FOLDER_STRUCTURE.md`).
- Every run fixes a `--seed` and records it in the run manifest (P03.07).
- SUMO's own output includes a non-deterministic wall-clock generation
  timestamp in a leading XML comment; determinism proofs must hash content
  with that header excluded, not the raw file.
- Sensor noise, dropout, and fault behavior (P03.06) must be layered on top
  of SUMO's clean output; SUMO does not simulate device failure.

## Security/privacy/safety effects

SUMO output is synthetic; it carries no real personal or location data. All
downstream records must retain the `simulated` truth label
(`docs/PROJECT_CONTEXT.md`) so simulated telemetry is never presented as
measured.

## Revision triggers

- A supported target host cannot run SUMO headless (contradicts P01.01/P01.06
  evidence).
- The assessment requires vision-realistic sensor simulation beyond feature
  extraction, which would require a supplementary tool alongside SUMO, not a
  replacement.

## Related requirements

RQ10 (system architecture including pipelines), P01.06, P03.01-P03.09.
