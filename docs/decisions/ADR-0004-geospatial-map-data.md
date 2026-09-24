# ADR-0004: Geospatial map data source

- **Status:** Provisional - pending user confirmation
- **Date:** 2026-09-18
- **Deciders:** ARCH, with SIM/LEAD as implementing owners

## Context

`Memory.md` records an open question: whether the initial map should remain
a synthetic 12-intersection grid or model a named public area using
redistributable map data. P03.01 needs a concrete answer before building the
versioned baseline network. This ADR records the trade-off so the decision
is defensible either way and can be applied the moment the user answers.

## Decision drivers

- Reproducibility and redistribution: any committed map source must be
  license-clear for a public academic repository (RQ14 GitHub delivery,
  `AGENTS.md` publication rules).
- No real traffic controller, camera feed, or physical sensor is authorized
  (`AGENTS.md` product-truth rules); a real-place map must not imply real
  control authority over that place.
- Demonstration realism versus engineering effort: a named real area adds
  import/cleanup work (OSM tag mapping, signal inference) without changing
  what P02-P12 actually verify (edge inference, streaming, incidents,
  emergency coordination, security, AIOps).
- Assessment traceability: RQ08/RQ10 diagrams and the presentation (P14) read
  more concretely with a recognizable area, but only if that realism does not
  create a false impression of real deployment.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Synthetic grid network (current baseline, `netgenerate`-built)** | Fully deterministic and license-free, generation parameters are versioned in `source-code/simulator/`, no risk of misrepresenting a real location as controlled, fastest to iterate | Visually generic; does not showcase real-world road geometry irregularity in the presentation |
| Named public area from OpenStreetMap extract (via `netconvert --osm`) | Recognizable, more convincing demonstration geometry, OSM data is redistributable under ODbL with attribution | Requires cleanup of imported signal/lane data, an explicit ODbL attribution file under `files/`, and unambiguous labeling everywhere that the platform does not control real infrastructure at that location |
| Licensed commercial map/traffic dataset | Highest fidelity | Not redistributable in a public repository; fails RQ14 and the project's no-secrets/no-restricted-data publication rule |

## Decision

Default (unchanged until the user answers): keep the synthetic grid network
as the baseline for P03.01, because it is unblocked today, fully
license-clear, and sufficient for every verifiable acceptance criterion in
Phases 03-12. If the user later selects a named public area, the
implementing action is to import an OSM extract via `netconvert --osm`,
record its ODbL attribution under `files/`, and add explicit "simulated
network derived from public map data; no real infrastructure is controlled"
labeling to every diagram and UI surface that displays it - not to change any
other architectural decision in this set.

## Consequences

- P03.01 may proceed now on the synthetic grid without waiting on this
  question, preserving schedule.
- If the answer changes later, only the network-generation step and its
  attribution/labeling are affected; contracts, services, and diagrams keyed
  on intersection/lane IDs are unaffected because those IDs are already
  treated as versioned, not hardcoded to a specific geography.

## Security/privacy/safety effects

Either option carries zero real personal or operational data. A named-area
option must not be presented, in any diagram, UI, or presentation slide, as
platform control over real infrastructure at that location.

## Revision triggers

- User answers the open question in `Memory.md`. Update this ADR's Status to
  Accepted and record the answer and date here rather than opening a new ADR.

## Related requirements

RQ08, RQ10, RQ14, P03.01.
