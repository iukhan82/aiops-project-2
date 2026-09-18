# Delivery Roadmap

## Phase 0 — Scope, safety, and success measures

- Confirm demonstration geography, traffic modes, agencies, and assessment needs.
- Define the safety boundary, prohibited automation, privacy rules, and approval
  model.
- Select measurable operational, emergency, accessibility, fairness, and AIOps
  outcomes.
- Create the requirement traceability matrix, decision log, risk register, and
  evidence strategy.

Exit: each proposed feature has an owner, priority, safety class, evidence method,
and explicit simulated/real status.

## Phase 1 — Reproducible traffic digital twin

- Build a SUMO district with 12 intersections, three corridors, crossings,
  cyclists, transit, parking, and emergency units.
- Create deterministic demand, weather, incident, controller, and platform-fault
  scenarios with separate ground truth.
- Version road geometry and sensor/device definitions.

Exit: runs reproduce from seed and generate valid time-aligned telemetry.

## Phase 2 — Contracts and secure edge ingestion

- Define event, signal, incident, forecast, emergency, command, and outcome
  contracts.
- Implement edge adapters, validation, local inference hooks, health, buffering,
  and replay.
- Add device identity, mTLS, topic/stream authorization, and deduplication.

Exit: a network outage loses no accepted events and replay creates no duplicates.

## Phase 3 — Network state, storage, and live map

- Implement PostGIS topology and time-series storage.
- Build network-state aggregation and freshness tracking.
- Deliver the control-room shell, real-time map, layer controls, incident queue,
  intersection/corridor details, replay, and accessible table alternative.

Exit: operators can trace a simulated observation from source to map and history.

## Phase 4 — Traffic and safety intelligence

- Establish transparent rule/statistical baselines.
- Train and evaluate traffic forecast and incident candidate models using disjoint
  runs.
- Add queue spillback, stalled vehicle, collision, wrong-way, flooding, signal
  failure, and data-quality detection.
- Correlate sources into explainable incidents.

Exit: performance, uncertainty, latency, limitations, and failure cases are
documented against held-out scenarios.

## Phase 5 — Emergency coordination

- Add CAD/AVL simulator, unit capability/availability, assignment, routing, ETA,
  staging, and shared timelines.
- Implement ambulance, fire, and police workflows.
- Add green-corridor recommendations with pedestrian/signal safety constraints.

Exit: three emergency scenarios complete end-to-end with measured ETA and safety
outcomes.

## Phase 6 — Governed traffic actions

- Add simulation-only signal plans, transit priority, emergency pre-emption,
  closure, diversion, and message-sign adapters.
- Implement request/approve/execute/acknowledge/verify/rollback lifecycle.
- Enforce expiry, idempotency, cooldown, two-person approval, and safe fallback.

Exit: allowed actions improve a measured scenario; stale, unsafe, duplicate, and
unauthorized actions are denied and audited.

## Phase 7 — AIOps and resilience

- Instrument edge and central services with metrics, logs, traces, and topology.
- Create SLOs, platform dashboards, alert rules, root-cause correlation, and
  bounded remediation.
- Inject device, network, stream, storage, model, identity, policy, and service
  failures.
- Verify backup, restore, restart, replay, rollback, and disaster procedures.

Exit: an operational fault produces one correlated incident, one authorized
remediation, and independently verified recovery.

## Phase 8 — Security, privacy, and accessibility acceptance

- Test every role and denied path.
- Review video/trajectory retention, redaction, export, and privacy zones.
- Run dependency, container, secret, policy, and runtime-security checks.
- Complete keyboard, screen-reader, contrast, reduced-motion, responsive, and
  high-pressure operator workflow tests.

Exit: critical findings are resolved or explicitly accepted by the accountable
owner with expiry.

## Phase 9 — Deployment, evidence, and presentation

- Build versioned images and deploy on supported Linux/K3s.
- Run nominal, peak, failure, recovery, capacity, and soak tests.
- Package diagrams, model cards, runbooks, evidence, repository, demo, and
  presentation.
- Rehearse incident triage, emergency routing, action approval, and live design
  modification.

Exit: every mandatory claim links to reproducible evidence or a clearly stated
gap.

## Recommended release slices

| Release | Demonstrable outcome |
|---|---|
| R0 | Reproducible traffic simulation and contracts |
| R1 | Secure sensor-to-map path with history and replay |
| R2 | Congestion and six incident families with operator workflow |
| R3 | Emergency dispatch, routing, and guarded green corridor |
| R4 | Governed traffic actions with verified outcomes |
| R5 | AIOps, resilience, security, accessibility, and deployment evidence |

## First implementation backlog

1. Confirm the road-network scope and import/build the 12-intersection SUMO map.
2. Define device, event, geometry, signal, and emergency schemas.
3. Implement deterministic normal and peak traffic scenarios.
4. Generate loop/radar/signal/weather telemetry and ground truth.
5. Build an edge gateway with validation and durable replay.
6. Persist events and topology in PostgreSQL/PostGIS.
7. Build the operator shell and live map against real APIs.
8. Add collision, stalled vehicle, congestion, flooding, signal failure, and
   sensor-health incidents.
9. Add simulated emergency units, dispatch, route, and ETA.
10. Implement guarded simulator-only diversion and signal actions.
