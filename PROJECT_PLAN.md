# Project Delivery Plan

## 1. Objective

Design and implement an Intelligent Traffic and Emergency Response Platform that
combines edge AI, real-time traffic analytics, emergency coordination, AIOps,
security, and policy-controlled traffic actions in a reproducible simulated
urban environment.

The project should demonstrate an end-to-end path from sensors to decisions:

```text
Road and system telemetry
        -> edge validation and feature extraction
        -> secure event streaming
        -> network state and prediction
        -> correlated incident
        -> operator decision or guarded automation
        -> controller/notification action
        -> measured outcome and audit evidence
```

## 2. Intended users

| Role | Primary responsibilities |
|---|---|
| Traffic operator | Monitor corridors, validate incidents, apply diversion and signal plans |
| Emergency dispatcher | Assign units, select routes, request traffic priority |
| Duty supervisor | Approve high-impact actions, coordinate agencies, manage escalation |
| Field responder | Receive navigation, closures, hazards, tasks, and status updates |
| Maintenance engineer | Investigate failed sensors, controllers, networks, and edge nodes |
| Transport analyst | Study demand, safety, travel time, interventions, and model performance |
| Auditor/safety reviewer | Inspect commands, approvals, policy decisions, and outcomes |
| Platform administrator | Manage users, devices, integrations, policies, and configuration |

## 3. Platform domains

### 3.1 Traffic operations

- Live network state and geospatial visualization.
- Intersection, corridor, route, and zone health.
- Queue, delay, throughput, occupancy, speed, travel-time, and congestion metrics.
- Signal plan monitoring, coordination, and simulation-only control.
- Roadworks, closures, lane restrictions, parking, transit, and planned events.

### 3.2 Safety and incident intelligence

- Collision, stalled vehicle, wrong-way driver, unsafe speed, red-light risk,
  queue spillback, pedestrian conflict, debris, low visibility, ice, flooding,
  fire/smoke, and infrastructure failure detection.
- Multi-source correlation to avoid one alert per sensor.
- Confidence, severity, location, affected lanes, evidence, and impact radius.
- Incident ownership, timeline, playbook, escalation, resolution, and review.

### 3.3 Emergency response

- Computer-aided-dispatch adapter or simulated dispatch feed.
- Unit availability, location, capability, assignment, and ETA.
- Fastest-safe route with live closures and vehicle constraints.
- Green-corridor recommendation and guarded emergency pre-emption.
- Cross-agency coordination for ambulance, fire, police, traffic, and maintenance.
- Hospital/facility routing interface using capacity categories, without exposing
  patient data in the traffic platform.

### 3.4 AI and decision support

- Short-horizon speed, volume, queue, and travel-time forecasting.
- Congestion and incident-risk prediction.
- Video/radar metadata fusion and duplicate-object suppression.
- Recommended signal, route, diversion, lane, and public-information actions.
- Explainable recommendation factors, uncertainty, and alternatives.
- Model registry, staged activation, shadow evaluation, drift monitoring, and
  rollback.

### 3.5 AIOps and platform reliability

- Metrics, logs, traces, topology, service dependencies, and SLOs.
- Sensor silence, stuck values, time drift, calibration drift, duplicates,
  impossible movement, and data-quality incidents.
- Stream lag, API errors, map-tile failures, database pressure, model latency,
  controller communication loss, certificate expiry, and configuration drift.
- Root-cause hypotheses that remain visibly distinct from verified causes.
- Policy-controlled restart, failover, model rollback, sampling change, and
  connector isolation with post-action health verification.

### 3.6 Public and agency communication

- Variable message sign and traveler-information adapters.
- Public incident and closure feed with accessibility-ready text.
- Agency notifications and acknowledgement tracking.
- Subscription-ready alerts for routes or zones.
- Sanitized open-data exports with delay, aggregation, and privacy controls.

## 4. Safety and ethics requirements

- No autonomous life-critical decision without a predefined safety case.
- Default-deny authorization for signal, closure, pre-emption, and public-alert
  actions.
- Two-person approval for city-wide or multi-corridor changes.
- Command expiry, idempotency, cooldown, maximum duration, bounded retries, and
  safe-plan rollback.
- Stale or low-confidence inputs cannot trigger automated control.
- Emergency priority must preserve pedestrian clearance and conflicting-phase
  safety timings.
- Video is processed at the edge where possible; store event metadata rather
  than identifiable footage by default.
- Do not use facial recognition, protected-attribute inference, or automated
  enforcement in the baseline.
- Measure geographic and modal fairness; do not optimize vehicle throughput at
  the expense of pedestrian, cyclist, transit, or emergency safety.
- Clearly label simulated, inferred, predicted, operator-entered, and verified
  data.

## 5. Functional scope

Detailed capabilities are maintained in [the feature catalog](docs/FEATURE_CATALOG.md).
The initial release must include:

1. Reproducible road-network and emergency scenario simulation.
2. Versioned telemetry, incident, prediction, command, and outcome contracts.
3. Edge event processing with offline buffering and secure replay.
4. A geospatial network state service and real-time operator map.
5. At least six incident families and three emergency response scenarios.
6. One measured traffic forecast and one operational anomaly model.
7. Policy-controlled actions operating on the simulator only.
8. Outcome verification comparing pre-action and post-action measurements.
9. Identity, role policy, transport protection, audit, and secret management.
10. Observability, fault injection, backup/restore, load testing, and deployment
    evidence.

## 6. Non-functional requirements

| Area | Initial target |
|---|---|
| Availability | Demonstrate graceful degradation and documented recovery; production HA is a later phase |
| Event latency | Critical simulated road events visible to operators within 3 seconds at nominal load |
| Emergency update latency | Unit location and route state visible within 2 seconds at nominal load |
| Data integrity | Stable event IDs, ordering metadata, deduplication, replay, schema validation |
| Auditability | Every command linked to user/service identity, policy, evidence, acknowledgement, and outcome |
| Accessibility | Keyboard operation, visible focus, 4.5:1 text contrast, non-color status encoding, reduced motion |
| Privacy | Data minimization, configurable retention, aggregation, redaction, access logging |
| Security | OIDC, least privilege, mTLS/service identity, default-deny policy, encrypted secrets |
| Resilience | Edge buffering, stale-state indicators, safe local signal-plan fallback, bounded retries |
| Performance | Load profile defined in events/sec, intersections, concurrent operators, and history window |
| Portability | Containerized services with repeatable Linux/K3s deployment |
| Reproducibility | Seeded scenarios, immutable run manifests, ground truth, versioned models and contracts |

## 7. Proposed technology baseline

This is a recommendation to validate during architecture decisions, not a locked
implementation choice.

| Layer | Recommended options |
|---|---|
| Simulation | SUMO plus a deterministic scenario/fault orchestrator |
| Edge analytics | Python, OpenCV where needed, ONNX Runtime, local durable outbox |
| Edge transport | MQTT over mTLS |
| Central event backbone | Redpanda/Kafka for high-volume streams; MQTT bridge for edge events |
| APIs and workflows | FastAPI services with OpenAPI and versioned JSON/Protobuf contracts |
| Operational data | PostgreSQL + PostGIS; TimescaleDB if its extension constraints are acceptable |
| Object/archive data | S3-compatible storage such as MinIO for approved evidence and model artifacts |
| Cache and ephemeral state | Redis, only where persistence semantics are explicit |
| Web application | React + TypeScript; MapLibre GL JS for the operations map |
| Identity and policy | Keycloak, OPA, workload/service certificates |
| ML lifecycle | ONNX, versioned model registry, evaluation artifacts; MLflow optional |
| Observability | OpenTelemetry, Prometheus, Grafana, Tempo, Loki |
| Deployment | Docker Compose for development; K3s for integrated acceptance |

## 8. Project outputs

- Working simulator and scenario library.
- Edge and central service source code.
- Operator web interface and field-responsive views.
- Versioned schemas, APIs, policies, and migrations.
- Trained models, baselines, model cards, and reproducible evaluation.
- Deployment manifests and operational runbooks.
- Architecture, network, data-flow, security, emergency-workflow, and deployment
  diagrams.
- Test suites and an evidence bundle.
- Presentation, live demonstration, and backup recording.

## 9. Explicit non-goals for the first release

- Direct control of public-road signal controllers.
- Facial recognition or identity tracking from cameras.
- Automated traffic-law enforcement.
- Replacement of certified emergency dispatch or signal safety systems.
- Claims of field accuracy based only on synthetic data.
- City-wide scale, production high availability, or certified regulatory
  compliance.

## 10. Acceptance scenarios

1. **Peak congestion:** predict queue spillback, recommend a corridor plan,
   approve it, apply it to the simulator, and measure improvement.
2. **Collision response:** correlate video/radar/call evidence, close the affected
   lane, dispatch units, issue a diversion, and verify traffic recovery.
3. **Ambulance priority:** calculate a route, safely pre-empt simulated signals,
   preserve pedestrian clearance, and compare arrival time against baseline.
4. **Road flooding:** combine rainfall, water depth, speed reduction, and reports;
   close a segment and publish a warning.
5. **Signal failure:** detect inconsistent phase/controller telemetry, enter safe
   plan, create maintenance work, and verify restoration.
6. **Communications outage:** buffer edge events, show stale state, preserve safe
   local operation, reconnect, replay without duplicates, and reconcile state.
7. **Bad model/configuration:** detect degraded predictions, block unsafe action,
   roll back the model/configuration, and verify health.

## 11. Management baseline

- Baseline date: 2026-09-18.
- `TASK_REGISTER.md` is authoritative for task state and evidence.
- `Memory.md` is the resumable handover and exact next action.
- The assessment requirement matrix is
  `docs/requirements/TRACEABILITY.md`.
- Technical progress and student oral-assessment readiness are tracked separately.
- A phase number organizes work; dependencies, not phase order alone, determine
  what is ready. Security, privacy, UX, observability and presentation walkthroughs
  begin before their final consolidation phases.

## 12. Phase roadmap and exit gates

| Phase | Outcome and exit gate |
|---|---|
| 00 - Controls and assessment | Scope, roles, traceability, safety and continuity records agree with both assessment documents |
| 01 - Environment and DevSecOps foundation | Reproducible development setup, Git/CI skeleton and early SUMO/K3s/Falco feasibility work |
| 02 - Requirements, architecture and contracts | Decisions, acceptance targets, versioned contracts and mandatory draft diagrams permit independent work |
| 03 - Simulation and datasets | Reproducible district, sensors, emergency units, faults, ground truth and leakage-safe splits |
| 04 - Edge AI and offline operation | Real optimized edge inference, privacy-preserving features, buffering, replay and model lifecycle evidence |
| 05 - Streaming, geospatial storage and platform backend | Secure edge-to-central data, authoritative state and real staff/emergency APIs |
| 06 - Traffic intelligence and incident correlation | Measured forecasts/detectors create explainable correlated incidents |
| 07 - Emergency coordination and governed control | Dispatch/routing plus safe simulator-only actions with verified outcomes |
| 08 - Operations experience | Designed and tested role-specific control-room and field workflows against real APIs |
| 09 - Security, privacy and compliance | Enforced identity/policy/transport/runtime controls plus risk/privacy/control evidence and explicit gaps |
| 10 - Observability and AIOps | Platform telemetry produces operational incidents, bounded remediation and independent recovery evidence |
| 11 - K3s deployment and resilience | Repeatable Linux deployment, integrations, backup/restore, capacity, failure and recovery evidence |
| 12 - System acceptance and evidence | Mandatory scenarios, roles and requirements pass or have assessor-approved exceptions |
| 13 - Documentation and repository release | Current diagrams/runbooks/model cards/attribution plus reproducible versioned repository release |
| 14 - Presentation, interview and submission | Inspected 15-20 minute deck, offline demo, 30-40 minute interview readiness, live redesign and authorized submission |

## 13. Milestones

| Milestone | Required evidence |
|---|---|
| M0 - Governed start | Phase 00 exit gate |
| M1 - Design ready | Environment profile, decisions, contracts, safety model and draft diagrams |
| M2 - Reproducible edge | Simulator/datasets plus real measured edge inference and offline replay |
| M3 - Live network state | Secure streaming, persistence, map and incident path |
| M4 - Emergency and action workflow | Dispatch-to-route-to-guarded-action-to-outcome evidence |
| M5 - Observable secure deployment | Identity/policy/runtime controls, AIOps recovery and K3s evidence |
| M6 - Assessment acceptance | Requirement coverage and evidence bundle reviewed |
| M7 - Release and oral package | Published/accessible repository, deck, PDF, demo backup and student rehearsal |
| M8 - Submission recorded | User-authorized submission or user-confirmed receipt |

Student walkthroughs occur at M2, M3 and M4. Prepared notes are not evidence of
student participation.

## 14. Initial risks, assumptions and open information

| ID | Type | Item and response |
|---|---|---|
| R01 | Risk | Full platform scope may exceed assessment time. Deliver vertical release slices and protect the mandatory path before differentiators. |
| R02 | Risk | Synthetic traffic may make models unrealistically accurate. Hold out seeds, routes, demand and faults; compare transparent baselines and disclose no field validation. |
| R03 | Risk | Video/trajectory processing creates privacy risk. Prefer edge-derived metadata, privacy zones, short retention and a DPIA-style assessment; no facial recognition. |
| R04 | Risk | Traffic optimization can move congestion or risk to pedestrians/transit/other areas. Use multi-objective constraints, fairness measures, simulation preview and safe rollback. |
| R05 | Risk | Emergency priority may create conflicting movements. Preserve certified timing constraints in the simulator and require explicit policy/approval. |
| R06 | Risk | Real-time map and stream scale may overload a small target. Establish budgets, aggregate/downsample, load test and degrade visibly. |
| R07 | Risk | Controller/CAD/V2X standards are complex and profile-specific. Use explicit simulated adapters and never claim vendor/agency conformance without tests. |
| R08 | Risk | Cloud requirement may not be met by a local central tier. Obtain assessor clarification and keep deployed versus proposed topology explicit. |
| R09 | Risk | AI-generated work may exceed student understanding. Schedule milestone walkthroughs, design defense, live modifications and transparent AI-use records. |
| R10 | Risk | Security tooling/configuration may exist without enforcement. Require negative runtime tests and target evidence for protected controls. |
| A01 | Assumption | A 12-intersection, three-corridor synthetic district is sufficient for the first demonstrable scope. |
| A02 | Assumption | SUMO, ONNX Runtime/OpenCV, MQTT, Kafka-compatible streaming, PostGIS, React/MapLibre, Keycloak/OPA and OTel stack are feasible pending profiling. |
| I01 | Open | Deadline, upload/access rules, target machine, cloud account, physical integrations and student availability. |

## 15. Decision rules

- Record architecture and scope decisions under `docs/decisions/` with context,
  options, choice, consequences and evidence needed to revisit them.
- Prefer a small working vertical slice over broad disconnected scaffolding.
- Do not introduce complex ML until a baseline, dataset provenance and held-out
  evaluation exist.
- Do not treat a drawing as deployed infrastructure, a configuration as a passing
  runtime, or a recommendation as an executed or effective action.
- External publication, spending, submission, public alerts and real control need
  explicit user authorization.
