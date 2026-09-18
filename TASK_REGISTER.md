# Project task register

Last updated: 2026-09-18 (Asia/Karachi). This is the authoritative source for
task status, ownership, dependencies, acceptance and evidence. `Memory.md` is the
session handover; `PROJECT_PLAN.md` defines scope and phase gates.

## Status rules

- **TODO:** not started; ordinary dependency waiting remains TODO.
- **IN_PROGRESS:** claimed by one active owner; Memory records current action.
- **IN_REVIEW:** implementation exists but acceptance/independent verification is incomplete.
- **BLOCKED:** a concrete obstacle prevents progress; record owner and unblock action.
- **DONE:** acceptance met and inspectable evidence linked.
- **DEFERRED/CANCELLED:** retained with reason and requirement impact.

Runtime tasks require runtime evidence. Keep task IDs stable, add child IDs rather
than renumbering, and append status changes to the log before updating Memory.md.

## Role owners

| Code | Project-local skill |
|---|---|
| LEAD | traffic-aiops-project-lead |
| ARCH | traffic-architecture-docs |
| SIM | traffic-simulation-digital-twin |
| EDGE | traffic-edge-ai-vision |
| DATA | traffic-data-streaming |
| BACKEND | traffic-backend-platform |
| EMERG | traffic-emergency-response |
| CONTROL | traffic-optimization-control |
| UX | traffic-ui-ux |
| UI | traffic-operations-ui |
| OPS | traffic-observability-aiops |
| SEC | traffic-security-engineering |
| GRC | traffic-privacy-compliance |
| DEVOPS | traffic-devsecops-platform |
| QA | traffic-qa-reliability |
| PRESENT | traffic-presentation-coach |

## Phase 00 - Controls and assessment

Exit gate: scope, requirements, safety, roles, folders and handover controls agree.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P00.01 | Inspect assessment PDFs and Project 2 folder | LEAD | High | - | DONE | Both PDFs read completely; confidential sources remain outside repository | `docs/requirements/TRACEABILITY.md` | 2026-09-18 |
| P00.02 | Define product, sensors, features and safety boundary | LEAD | High | P00.01 | DONE | Full feature/sensor baseline and explicit prohibited actions exist | `README.md`; `docs/FEATURE_CATALOG.md`; `docs/SENSOR_AND_DATA_CATALOG.md` | 2026-09-18 |
| P00.03 | Normalize assessment requirements and open questions | LEAD | High | P00.01 | DONE | Topic and exam-guide requirements have stable IDs, planned evidence and gaps | `docs/requirements/TRACEABILITY.md` | 2026-09-18 |
| P00.04 | Establish security, privacy, protocol and compliance baseline | SEC/GRC | High | P00.01 | DONE | Required tools/protocols and scoped control mappings exist without compliance claims | `docs/PROTOCOLS_AND_STANDARDS.md`; `docs/SECURITY_COMPLIANCE_BASELINE.md` | 2026-09-18 |
| P00.05 | Create and validate project-local role skills | LEAD | High | P00.02 | DONE | Sixteen skill packages pass `quick_validate.py`; no placeholders remain | `skills/`; `docs/AGENT_SKILLS.md`; `docs/PROJECT_MANAGEMENT_VALIDATION.md` | 2026-09-18 |
| P00.06 | Establish plan, register, memory and startup protocol | LEAD | High | P00.03, P00.05 | DONE | Authoritative files agree and define resumable handoff | `PROJECT_PLAN.md`; `TASK_REGISTER.md`; `Memory.md`; `AGENTS.md`; `docs/PROJECT_MANAGEMENT_VALIDATION.md` | 2026-09-18 |
| P00.07 | Create canonical repository areas and ownership | LEAD | Normal | P00.06 | DONE | Source, diagrams, docs, presentation, skills, workflows and sanitized files are separate | `docs/FOLDER_STRUCTURE.md` | 2026-09-18 |
| P00.08 | Establish control-room design baseline | UX | Normal | P00.02 | DONE | Persistent design system covers accessible real-time and high-risk action behavior | `design-system/intelligent-traffic-and-emergency-response-platform/MASTER.md` | 2026-09-18 |

## Phase 01 - Environment and DevSecOps foundation

Exit gate: supported development/target routes and early SUMO, K3s and security
feasibility are measured, with repeatable local checks.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P01.01 | Inventory workstation and target environment | DEVOPS | High | P00.06 | DONE | CPU/RAM/disk/OS/GPU/Python/Node/Git/container/WSL/network facts recorded | `docs/environment/WORKSTATION_INVENTORY.md`; target facts explicitly unknown | 2026-09-18 |
| P01.02 | Select topology and resource budgets | DEVOPS | High | P01.01 | DONE | Dev, acceptance and proposed production profiles have limits and rationale | `docs/environment/TOPOLOGY.md`; `docs/environment/RESOURCE_BUDGET.md` | 2026-09-18 |
| P01.03 | Initialize Git, exclusions and branch/review policy | DEVOPS | High | P00.07 | DONE | Initial commit excludes secrets/private sources/runtime data; review rules documented | Root commit `f775f05160eeb8517488f2089c8083a7588ca6f3`; `docs/environment/GIT_POLICY.md`; ignore and staged-content checks passed | 2026-09-18 |
| P01.04 | Scaffold Python/Node conventions and dependency locks | DEVOPS | Normal | P01.01 | DONE | Reproducible environments, linters, formatters and test entrypoints run cleanly | Python: ruff clean, 2 pytest passed, management validation passed; Node: `npm ci`, TypeScript 7.0.2, Vitest no-tests foundation passed, 0 audit vulnerabilities | 2026-09-18 |
| P01.05 | Build initial DevSecOps workflow | DEVOPS | High | P01.03, P01.04 | TODO | CI runs quality/tests plus secret/dependency/IaC scans with least privileges | - | 2026-09-18 |
| P01.06 | Prove SUMO and map-tool feasibility | SIM | High | P01.01 | TODO | Headless deterministic run and telemetry extraction execute on supported dev route | - | 2026-09-18 |
| P01.07 | Prove container, K3s/MicroK8s and Falco feasibility | DEVOPS | High | P01.01, P01.02 | TODO | Workload reaches Ready and a scoped runtime event is observed on supported Linux | - | 2026-09-18 |
| P01.08 | Write and verify clean-start developer instructions | DEVOPS | Normal | P01.04-P01.07 | TODO | Another clean environment can install and run the documented smoke check | - | 2026-09-18 |

## Phase 02 - Requirements, architecture and contracts

Exit gate: measurable acceptance, decisions, safety/roles, schemas and mandatory
draft diagrams permit independent implementation.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P02.01 | Define measurable acceptance targets | LEAD/QA | High | P01.02 | TODO | Latency, load, accuracy, false alarm, ETA, safety, recovery and UX targets distinguished from results | - | 2026-09-18 |
| P02.02 | Record architecture and technology decisions | ARCH | High | P01.02 | TODO | Options/trade-offs cover simulator, streaming, data, map, AI, edge, central/cloud and deployment | - | 2026-09-18 |
| P02.03 | Define device and observation contracts | LEAD/DATA | High | P00.02 | TODO | Versioned schemas/examples cover identity, time, units, quality, provenance and privacy | - | 2026-09-18 |
| P02.04 | Define road geometry, SPaT/MAP and network-state contracts | LEAD | High | P02.02 | TODO | Versioned intersection/lane/movement/signal/freshness semantics validate | - | 2026-09-18 |
| P02.05 | Define forecast, incident, recommendation, command and outcome contracts | LEAD | High | P02.03, P02.04 | TODO | Separate lifecycle records, uncertainty, idempotency and errors validate | - | 2026-09-18 |
| P02.06 | Define emergency call, unit, route and coordination contracts | EMERG | High | P02.04 | TODO | Patient-free CAD/AVL abstraction with state machine and alternatives validates | - | 2026-09-18 |
| P02.07 | Define observability and policy contracts | OPS/SEC | Normal | P02.03, P02.05 | TODO | OTel attributes, SLO measures and OPA inputs/decisions are versioned/tested | - | 2026-09-18 |
| P02.08 | Finalize roles, safety classes and action authority | SEC/GRC | High | P02.05, P02.06 | TODO | Recommend/request/approve/execute/override/demo/audit separation is explicit | - | 2026-09-18 |
| P02.09 | Draft seven mandatory architecture views | ARCH | High | P02.02, P02.03, P02.04, P02.05, P02.06, P02.07, P02.08 | TODO | Editable system/network/data/workflow/security/hybrid/deployment sources render legibly | - | 2026-09-18 |

## Phase 03 - Simulation and datasets

Exit gate: deterministic normal/fault/emergency runs and leakage-safe datasets can
be regenerated with protected ground truth.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P03.01 | Build versioned 12-intersection SUMO district | SIM | High | P01.06, P02.04 | TODO | Network has three corridors, crossings, cycle/transit routes and valid signals | - | 2026-09-18 |
| P03.02 | Implement deterministic demand and clock | SIM | High | P03.01 | TODO | Seed/run ID reproduces vehicles, pedestrians, cyclists, transit and timestamps | - | 2026-09-18 |
| P03.03 | Simulate traffic, VRU, signal, weather and road sensors | SIM | High | P02.03, P03.02 | TODO | Catalog measurements have units, identity, location, quality and provenance | - | 2026-09-18 |
| P03.04 | Simulate emergency units and CAD/AVL events | SIM/EMERG | High | P02.06, P03.02 | TODO | Ambulance/fire/police unit states and routes reproduce without patient data | - | 2026-09-18 |
| P03.05 | Implement traffic and safety scenarios | SIM | High | P03.03 | TODO | Normal/peak/event/collision/stall/wrong-way/flood/visibility/signal scenarios have truth | - | 2026-09-18 |
| P03.06 | Implement device/platform fault scenarios | SIM/OPS | Normal | P03.03 | TODO | Silence/stuck/clock/network/model/service/storage faults have onset/end evidence | - | 2026-09-18 |
| P03.07 | Build run manifests, JSON/event output and replay | SIM | High | P03.03, P03.04, P03.05, P03.06 | TODO | Immutable manifest/hashes and replay produce equivalent accepted sequences | - | 2026-09-18 |
| P03.08 | Create disjoint train/validation/test datasets | SIM/QA | High | P03.07 | TODO | Splits differ across seeds/routes/demand/faults and pass leakage checks | - | 2026-09-18 |
| P03.09 | Verify scenario bounds and document limitations | QA | High | P03.08 | TODO | Automated invariants and visual checks pass; synthetic-data limitations are explicit | - | 2026-09-18 |

## Phase 04 - Edge AI and offline operation

Exit gate: real optimized local inference is measured; edge identity, buffering,
replay and model lifecycle withstand faults.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P04.01 | Implement edge validation and past-only preprocessing | EDGE | High | P02.03, P03.08 | TODO | Invalid/stale/missing data behavior and feature lineage are tested | - | 2026-09-18 |
| P04.02 | Build transparent traffic/safety baselines | EDGE | High | P04.01 | TODO | Rules/statistics evaluated on held-out scenarios with failure cases | - | 2026-09-18 |
| P04.03 | Train traffic/safety edge model | EDGE | High | P04.02 | TODO | Train/validation discipline, provenance, uncertainty and comparison recorded | - | 2026-09-18 |
| P04.04 | Export and verify ONNX model | EDGE | High | P04.03 | TODO | Parity, integrity hash, input/output schema and load failure behavior pass | - | 2026-09-18 |
| P04.05 | Implement edge runtime and health | EDGE | High | P04.01, P04.04 | TODO | Real inference, abstention, metrics and readiness run in bounded container | - | 2026-09-18 |
| P04.06 | Implement privacy-preserving track/vision metadata path | EDGE/GRC | Normal | P04.01 | TODO | Edge-derived metadata and privacy zones work without identity recognition/raw retention | - | 2026-09-18 |
| P04.07 | Implement durable offline outbox and acknowledged replay | EDGE/DATA | High | P04.05 | TODO | Crash/restart/uplink loss preserves order and produces no accepted duplicates | - | 2026-09-18 |
| P04.08 | Implement atomic model activation and rollback | EDGE | High | P04.04, P04.05 | TODO | Invalid/degraded artifacts are refused or rolled back without unsafe serving | - | 2026-09-18 |
| P04.09 | Evaluate edge accuracy, latency and resource use | EDGE/QA | High | P04.05, P04.06, P04.07, P04.08 | TODO | Cold/warm benchmarks and held-out metrics include conditions/sample sizes/limits | - | 2026-09-18 |

## Phase 05 - Streaming, geospatial storage and backend

Exit gate: secure events persist exactly once by meaning, authoritative state and
staff APIs expose real current/history data.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P05.01 | Provision MQTT, Kafka-compatible stream and PostgreSQL/PostGIS | DATA/DEVOPS | High | P01.07, P02.03 | TODO | Pinned services run with health, persistence and bounded resources | - | 2026-09-18 |
| P05.02 | Secure MQTT identities, mTLS and topic ACLs | SEC/DATA | High | P05.01, P02.08 | TODO | Valid device works; cross-device publish/subscribe and bad trust fail | - | 2026-09-18 |
| P05.03 | Implement MQTT-to-stream gateway and acknowledgements | DATA | High | P04.07, P05.01 | TODO | Validation, partitioning, backpressure and application ack/replay pass | - | 2026-09-18 |
| P05.04 | Create migrations for topology, telemetry and control records | DATA/BACKEND | High | P02.03-P02.06, P05.01 | TODO | Ordered checksum migrations/seeds repeat safely with constraints | - | 2026-09-18 |
| P05.05 | Implement validated central ingestion and deduplication | DATA | High | P05.03, P05.04 | TODO | Identity/schema/content conflicts reject; identical replay is idempotent | - | 2026-09-18 |
| P05.06 | Implement network-state and freshness service | BACKEND | High | P05.05 | TODO | Segment/intersection/lane state preserves source time, quality and geometry | - | 2026-09-18 |
| P05.07 | Implement device, traffic, history and live APIs | BACKEND | High | P05.06 | TODO | Versioned paginated APIs/live reconnect expose real persisted data | - | 2026-09-18 |
| P05.08 | Implement incident and command repositories/state machines | BACKEND | High | P02.05, P05.04 | TODO | Valid transitions/concurrency/idempotency/audit references pass | - | 2026-09-18 |
| P05.09 | Implement separate authorized scenario-control API | BACKEND/SIM | Normal | P03.07, P02.08 | TODO | Start/status/reset/replay are bounded, role-separated and audited | - | 2026-09-18 |
| P05.10 | Implement retention, aggregation and storage-pressure handling | DATA/GRC | High | P05.04 | TODO | Policy enforces classes while preserving audit/active control and safe backpressure | - | 2026-09-18 |

## Phase 06 - Traffic intelligence and incident correlation

Exit gate: measured forecasts and detectors create explainable correlated traffic,
safety and data-quality incidents with controlled false alarms.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P06.01 | Implement traffic aggregates and corridor KPIs | BACKEND | High | P05.06 | TODO | Speed/volume/density/queue/travel/delay/throughput/reliability validate | - | 2026-09-18 |
| P06.02 | Build short-horizon forecast baseline | EDGE/BACKEND | High | P03.08, P06.01 | TODO | 5/15/30-minute baseline and uncertainty evaluated on held-out runs | - | 2026-09-18 |
| P06.03 | Train/evaluate network traffic forecast model | EDGE | Normal | P06.02 | TODO | Model beats or is rejected against baseline; drift/failure modes recorded | - | 2026-09-18 |
| P06.04 | Implement congestion and spillback detection | BACKEND | High | P06.01 | TODO | Location, severity, onset/clear and evidence pass scenarios | - | 2026-09-18 |
| P06.05 | Implement collision/stall/wrong-way/hazard candidates | EDGE/BACKEND | High | P04.09, P05.06 | TODO | Multi-source candidates meet held-out metrics and retain uncertainty | - | 2026-09-18 |
| P06.06 | Implement pedestrian/cyclist conflict indicators | EDGE/GRC | Normal | P04.06 | TODO | Trajectory measures avoid identity data and document false-positive/bias limits | - | 2026-09-18 |
| P06.07 | Correlate evidence into traffic/safety incidents | BACKEND | High | P05.08, P06.04-P06.06 | TODO | Duplicate sources group; hypotheses, escalation, resolution and reopen pass | - | 2026-09-18 |
| P06.08 | Evaluate intelligence and incident outcomes | QA | High | P06.02, P06.03, P06.04, P06.05, P06.06, P06.07 | TODO | Detection/forecast/incident latency and errors measured by scenario and road user | - | 2026-09-18 |

## Phase 07 - Emergency coordination and governed traffic control

Exit gate: three emergency scenarios and traffic actions complete the authorized
request-to-outcome path in simulation with safety and rollback evidence.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P07.01 | Implement CAD/AVL adapter and emergency state | EMERG | High | P03.04, P05.08 | TODO | Calls/units/assignments/status persist with simulated-source labels | - | 2026-09-18 |
| P07.02 | Implement fastest-safe route and ETA alternatives | EMERG | High | P05.06, P07.01 | TODO | Routes honor closure/hazard/vehicle constraints and expose uncertainty | - | 2026-09-18 |
| P07.03 | Implement cross-agency staging and handover | EMERG | Normal | P07.01 | TODO | Ambulance/fire/police timelines, acknowledgements and handovers are consistent | - | 2026-09-18 |
| P07.04 | Implement traffic recommendation/constraint engine | CONTROL | High | P06.01, P02.08 | TODO | Signal/diversion/priority options show alternatives, benefit and safety bounds | - | 2026-09-18 |
| P07.05 | Implement command approval and execution-time policy | CONTROL/SEC | High | P05.08, P07.04 | TODO | Role, second approval, expiry, target, bounds and policy outage denials pass | - | 2026-09-18 |
| P07.06 | Implement simulator signal/diversion/VMS adapters | CONTROL/SIM | High | P07.04, P07.05 | TODO | Only registered idempotent simulator actions execute and acknowledge observed state | - | 2026-09-18 |
| P07.07 | Implement emergency green corridor/pre-emption | EMERG/CONTROL | High | P07.02, P07.06 | TODO | Clearance/conflict rules, abort and safe fallback pass for emergency routes | - | 2026-09-18 |
| P07.08 | Implement transit priority and balanced corridor action | CONTROL | Normal | P07.06 | TODO | Benefits and harms to transit/traffic/pedestrians/neighbors measured | - | 2026-09-18 |
| P07.09 | Implement independent outcome verification and rollback | CONTROL/OPS | High | P07.06-P07.08 | TODO | Pre/post windows classify outcomes and trigger rollback/escalation correctly | - | 2026-09-18 |
| P07.10 | Verify ambulance, fire and police end-to-end scenarios | QA | High | P07.01, P07.02, P07.03, P07.04, P07.05, P07.06, P07.07, P07.08, P07.09 | TODO | Three scenarios measure ETA/safety/traffic outcomes plus failure limits | - | 2026-09-18 |

## Phase 08 - Operations experience

Exit gate: role-specific control-room/field workflows work against real APIs and
meet design, accessibility and action-feedback acceptance.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P08.01 | Define role journeys and screen/state inventory | UX | High | P02.08, P05.07 | TODO | All roles/tasks/errors/freshness/action states map to capabilities | - | 2026-09-18 |
| P08.02 | Create editable map, incident and emergency wireframes | UX | High | P08.01 | TODO | Laptop/projector/field layouts and accessible alternatives are reviewable | - | 2026-09-18 |
| P08.03 | Complete design tokens/components/interaction handoff | UX | High | P08.02 | TODO | Components, focus, responsive, chart/table and API/role mapping are explicit | - | 2026-09-18 |
| P08.04 | Build frontend shell and Keycloak session | UI | High | P08.03, P09.02 | TODO | Protected navigation/login/logout/session/denials work for all roles | - | 2026-09-18 |
| P08.05 | Build live operations map and accessible list | UI | High | P08.04, P05.07 | TODO | Layers, freshness, selection, replay, reconnect and table fallback use real APIs | - | 2026-09-18 |
| P08.06 | Build corridor/intersection/device analytics | UI | Normal | P08.05, P06.01 | TODO | Real current/history/forecast/quality/health views are clear and testable | - | 2026-09-18 |
| P08.07 | Build incident investigation and emergency dispatch | UI | High | P08.05, P07.03 | TODO | Ownership/timeline/evidence/unit/route/status workflows persist through APIs | - | 2026-09-18 |
| P08.08 | Build recommendation, approval, command and outcome views | UI | High | P08.07, P07.09 | TODO | Denied/pending/executing/failed/rollback/verified states are distinct | - | 2026-09-18 |
| P08.09 | Build audit, observability, shift handover and demo controls | UI | Normal | P08.08 | TODO | Role-limited real data works; demo controls remain visibly separate | - | 2026-09-18 |
| P08.10 | Run design, accessibility and real-browser acceptance | UX/QA | High | P08.04, P08.05, P08.06, P08.07, P08.08, P08.09 | TODO | Keyboard/contrast/zoom/reduced-motion/responsive/failure workflows pass | - | 2026-09-18 |

## Phase 09 - Security, privacy and compliance

Exit gate: identity, policy, transport, runtime and privacy controls are enforced
and tested; risk/control gaps are explicit.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P09.01 | Complete threat model and security architecture | SEC | High | P02.09 | TODO | Assets/trust/abuse/safety threats and treatments cover every platform boundary | - | 2026-09-18 |
| P09.02 | Provision Keycloak realm, clients, roles and demo identities | SEC | High | P02.08, P05.01 | TODO | OIDC validation, PKCE and role claims work without committed secrets | - | 2026-09-18 |
| P09.03 | Enforce OPA authorization at API and worker | SEC | High | P02.07, P07.05 | TODO | Default denial, policy outage and cross-role/target negative tests pass | - | 2026-09-18 |
| P09.04 | Protect service traffic, workloads and secrets | SEC/DEVOPS | High | P05.02, P09.02 | TODO | TLS/workload identity/RBAC/network policy/secrets/rotation evidence exists | - | 2026-09-18 |
| P09.05 | Protect audit and sensitive data | SEC/GRC | High | P05.10 | TODO | Recursive redaction, append protection and role-limited evidence access pass | - | 2026-09-18 |
| P09.06 | Complete data inventory, retention and DPIA-style review | GRC | High | P03.03, P04.06 | TODO | Purpose/minimization/access/retention/rights/risks/residual owners documented | - | 2026-09-18 |
| P09.07 | Map ISO/NIST/ETSI/IEC/OWASP/AI controls | GRC | Normal | P09.01, P09.02, P09.03, P09.04, P09.05, P09.06 | TODO | Evidence/gaps/control owners mapped without certification or legal claims | - | 2026-09-18 |
| P09.08 | Implement DevSecOps supply-chain controls | DEVOPS/SEC | High | P01.05 | TODO | Scans, SBOM, provenance, signatures and admission checks gate release artifacts | - | 2026-09-18 |
| P09.09 | Configure and verify Falco/runtime detections | SEC/DEVOPS | High | P01.07 | TODO | Scoped malicious/unauthorized behaviors generate actionable target alerts | - | 2026-09-18 |
| P09.10 | Run role, transport, policy and abuse-case acceptance | QA/SEC | High | P09.02, P09.03, P09.04, P09.05, P09.06, P09.07, P09.08, P09.09 | TODO | Positive/negative runtime evidence covers controls; findings owned and retested | - | 2026-09-18 |

## Phase 10 - Observability and AIOps

Exit gate: real telemetry detects operational degradation, creates one scoped
incident, performs bounded remediation and verifies sustained recovery.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P10.01 | Instrument services/devices/models with OpenTelemetry | OPS | High | P04.05, P05.07, P07.09 | TODO | Correlated traces/metrics/logs cover edge-to-action path with bounded labels | - | 2026-09-18 |
| P10.02 | Define SLOs and platform dependency topology | OPS | High | P10.01 | TODO | Availability/latency/error/saturation/freshness/ack/ETA objectives are measurable | - | 2026-09-18 |
| P10.03 | Provision Prometheus, Grafana, Tempo and logs | OPS/DEVOPS | High | P10.01 | TODO | Datasources/dashboards/traces/log links run with retention/resource bounds | - | 2026-09-18 |
| P10.04 | Create and test operational alert rules | OPS | High | P10.02, P10.03 | TODO | Device/stream/API/model/storage/cert/config alerts pass rule tests and live checks | - | 2026-09-18 |
| P10.05 | Capture normal/degraded operational datasets | OPS/SIM | High | P03.06, P10.01 | TODO | Disjoint measured runs have truth, manifests, hashes and clean splits | - | 2026-09-18 |
| P10.06 | Train/evaluate operational anomaly detector | OPS | High | P10.05 | TODO | Model/baseline metrics, threshold, provenance, latency and limitations recorded | - | 2026-09-18 |
| P10.07 | Correlate platform signals into incidents | OPS/BACKEND | High | P10.04, P10.06 | TODO | Duplicate symptoms group and unverified causal hypotheses remain labelled | - | 2026-09-18 |
| P10.08 | Implement policy-controlled remediation workers | OPS/SEC | High | P09.03, P10.07 | TODO | Registered restart/failover/quarantine/rollback/sampling/scale actions are bounded | - | 2026-09-18 |
| P10.09 | Verify AIOps recovery and failure limits | QA/OPS | High | P10.08 | TODO | Controlled faults prove causally linked action, sustained health or safe escalation | - | 2026-09-18 |

## Phase 11 - K3s deployment, integrations and resilience

Exit gate: a repeatable supported-Linux deployment survives tested failures,
backup/restore and nominal/peak load with security/observability active.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P11.01 | Build pinned versioned service images | DEVOPS | High | P04.09, P05.10, P06.08, P07.10, P08.10, P09.10, P10.09 | TODO | Non-root images build reproducibly, scan, carry SBOM/provenance and identify version | - | 2026-09-18 |
| P11.02 | Create least-privilege K3s manifests | DEVOPS/SEC | High | P11.01 | TODO | Resources, probes, PVCs, secrets, RBAC and network policy validate | - | 2026-09-18 |
| P11.03 | Deploy edge and central components on real target | DEVOPS | High | P11.02 | TODO | Observed Ready workloads and end-to-end events match recorded topology | - | 2026-09-18 |
| P11.04 | Verify target networking, identity, policy and runtime alerts | SEC/QA | High | P11.03 | TODO | Native positive/negative TLS/OIDC/OPA/network/Falco checks pass | - | 2026-09-18 |
| P11.05 | Exercise backup, restore, restart and rollout/rollback | DEVOPS/QA | High | P11.03 | TODO | Data/control/audit integrity and documented recovery objectives are observed | - | 2026-09-18 |
| P11.06 | Run load, capacity, soak and degraded-mode tests | QA | High | P11.03 | TODO | Nominal/peak/ceiling behavior and safe degradation are measured | - | 2026-09-18 |
| P11.07 | Validate optional transport/external adapters | ARCH/QA | Normal | P11.03 | TODO | Only scoped NTCIP/GTFS/CAP/V2X/CAD adapters claim tested compatibility | - | 2026-09-18 |
| P11.08 | Verify repeatable deployment and recovery runbook | DEVOPS | High | P11.04-P11.07 | TODO | Clean/reconcile deployment and operator-performed recovery follow current runbook | - | 2026-09-18 |

## Phase 12 - System acceptance and evidence

Exit gate: mandatory scenarios, roles and assessment requirements have passing,
traceable evidence or documented assessor-approved exceptions.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P12.01 | Run complete traffic and role acceptance matrix | QA | High | P08.10, P09.10, P11.08 | TODO | Nominal/peak/safety/emergency/action/security workflows pass for permitted roles | - | 2026-09-18 |
| P12.02 | Run failure, recovery and disaster matrix | QA | High | P10.09, P11.08 | TODO | Edge/network/stream/service/model/policy/storage faults and recovery are evidenced | - | 2026-09-18 |
| P12.03 | Validate model/data integrity and measured claims | QA/EDGE | High | P04.09, P06.08, P10.06 | TODO | Artifacts/hashes/splits/metrics/benchmarks reproduce with limitations | - | 2026-09-18 |
| P12.04 | Validate safety, privacy, accessibility and fairness | QA/GRC | High | P08.10, P09.10 | TODO | Critical workflows and geographic/modal outcome checks pass or record gaps | - | 2026-09-18 |
| P12.05 | Resolve acceptance defects and rerun affected checks | QA | High | P12.01, P12.02, P12.03, P12.04 | TODO | Stable defects are fixed/retested; unresolved impact and owner remain explicit | - | 2026-09-18 |
| P12.06 | Build reproducible evidence bundle | QA | High | P12.05 | TODO | Indexed artifacts include commands, versions, conditions, failures and requirement links | - | 2026-09-18 |
| P12.07 | Complete assessment coverage review | LEAD/QA | High | P12.06 | TODO | RQ01-RQ30 status is evidence-backed; gate result and gaps are explicit | - | 2026-09-18 |

## Phase 13 - Documentation and repository release

Exit gate: documentation and diagrams match the tested system; a reproducible,
sanitized release is accessible under authorized conditions.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P13.01 | Update mandatory diagrams to tested implementation | ARCH | High | P12.07 | TODO | Sources/exports match manifests/contracts/evidence and render legibly | - | 2026-09-18 |
| P13.02 | Produce separate proposed production reference views | ARCH | Normal | P13.01 | TODO | Proposed multi-zone/cloud/real-controller architecture is unmistakably not deployed | - | 2026-09-18 |
| P13.03 | Complete README, model/data cards and API docs | ARCH/LEAD | High | P12.07 | TODO | Clean reader can understand setup, operation, limitations and evidence | - | 2026-09-18 |
| P13.04 | Complete operational/security/privacy runbooks | OPS/SEC/GRC | High | P12.07 | TODO | Incident, rollback, backup, access, breach and recovery procedures were exercised | - | 2026-09-18 |
| P13.05 | Review attribution, licences, secrets and personal data | GRC/SEC | High | P13.03, P13.04 | TODO | Publication scan passes; all assets/dependencies/data have provenance and permission | - | 2026-09-18 |
| P13.06 | Prepare versioned reproducible local release | DEVOPS | High | P13.01, P13.02, P13.03, P13.04, P13.05 | TODO | Commit/tree/image/model/dependency identity and clean reproduction record exist | - | 2026-09-18 |
| P13.07 | Publish GitHub repository when authorized | DEVOPS | High | P13.06 | TODO | Authorized remote push, examiner access and clean-clone instructions verified | - | 2026-09-18 |

## Phase 14 - Presentation, interview and submission

Exit gate: student can independently present, demonstrate, defend and modify the
work; the authorized submission is complete and recorded.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P14.01 | Create evidence-led 15-20 minute storyboard | PRESENT | High | P12.07 | TODO | Timing covers mandatory architecture, edge AI, AIOps, security, metrics and limits | - | 2026-09-18 |
| P14.02 | Create editable PowerPoint and speaker notes | PRESENT | High | P14.01, P13.01 | TODO | Claims link to evidence; simulation/AI assistance/limitations are transparent | - | 2026-09-18 |
| P14.03 | Render and visually inspect submission PDF | PRESENT | High | P14.02 | TODO | Every slide is legible, unclipped and consistent with editable deck | - | 2026-09-18 |
| P14.04 | Prepare live demo and offline backup recording | PRESENT/QA | High | P13.06 | TODO | Timed preflight and incident-to-recovery demo work without external internet | - | 2026-09-18 |
| P14.05 | Prepare 25+ question bank and design exercises | PRESENT | High | P12.07 | TODO | Covers concepts, trade-offs, failures, security, scale, deployment and Level 6 reasoning | - | 2026-09-18 |
| P14.06 | Conduct M2 edge-model walkthrough with student | PRESENT/EDGE | High | P04.09 | TODO | Actual questions/answers/weaknesses/follow-up recorded; not inferred from notes | - | 2026-09-18 |
| P14.07 | Conduct M3 streaming/incident walkthrough with student | PRESENT/DATA | High | P06.08 | TODO | Student traces event, replay, state, incident and failures with recorded feedback | - | 2026-09-18 |
| P14.08 | Conduct M4 emergency/security/action walkthrough | PRESENT | High | P07.10, P09.10, P10.09 | TODO | Student explains policy, safety, outcomes, AIOps and limitations with feedback | - | 2026-09-18 |
| P14.09 | Rehearse presentation, 30-40 minute interview and live redesign | PRESENT | High | P14.03, P14.04, P14.05, P14.06, P14.07, P14.08 | TODO | Real timings, at least ten questions, architecture modification and improvement loop recorded | - | 2026-09-18 |
| P14.10 | Finalize package/access and submit when authorized | LEAD | High | P13.07, P14.09 | TODO | Format/access/version checks pass; user-authorized submission receipt recorded | - | 2026-09-18 |

## Change log

- 2026-09-18 - DEVOPS - P01.04 IN_PROGRESS to DONE. Added exact Python and Node locks, clean check entrypoints and two foundation tests; Python and Node checks pass.
- 2026-09-18 - DEVOPS - P01.04 TODO to IN_PROGRESS. Started pinned Python/Node tooling, checks and foundation tests.
- 2026-09-18 - DEVOPS - P01.03 IN_PROGRESS to DONE. Initialized `main`, verified publication exclusions and staged content, documented review policy, and created root commit `f775f05`.
- 2026-09-18 - DEVOPS - P01.03 TODO to IN_PROGRESS. Started local Git baseline, exclusion review and branch/review policy.
- 2026-09-18 - DEVOPS - P01.02 IN_PROGRESS to DONE. Selected WSL2/Compose development, native Linux/K3s assessment and proposed multi-zone production profiles; assigned bounded planning budgets and capacity gates.
- 2026-09-18 - DEVOPS - P01.02 TODO to IN_PROGRESS. Started topology and resource-budget decision from measured inventory.
- 2026-09-18 - DEVOPS - P01.01 IN_PROGRESS to DONE. Measured Windows/WSL resources, tool versions, Docker/Kubernetes state, relevant ports and missing tools without changing existing shared containers. Target host remains unavailable and explicit.
- 2026-09-18 - DEVOPS - P01.01 TODO to IN_PROGRESS. Started read-only workstation and target-environment inventory.
- 2026-09-18 - LEAD - Created 132-task, 15-phase register from both assessment
  PDFs and the full-platform design. Marked only the eight completed governance
  tasks DONE; all implementation, evidence, participation and submission tasks
  remain TODO. Exact next action is P01.01.
