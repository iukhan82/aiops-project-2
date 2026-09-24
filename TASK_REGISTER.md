# Project task register

Last updated: 2026-09-23 (Asia/Karachi). This is the authoritative source for
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
| P01.05 | Build initial DevSecOps workflow | DEVOPS | High | P01.03, P01.04 | IN_REVIEW | CI runs quality/tests plus secret/dependency/IaC scans with least privileges | `.github/workflows/check.yml`; `workflows/SECURITY_GATES.md`; 4 pytest checks, Ruff and Node checks pass; Trivy 0.74.0 local scan exits 0; first hosted GitHub run remains pending | 2026-09-18 |
| P01.06 | Prove SUMO and map-tool feasibility | SIM | High | P01.01 | DONE | Headless deterministic run and telemetry extraction execute on supported dev route | `source-code/simulator/feasibility/`; `docs/environment/SUMO_FEASIBILITY.md`; SUMO 1.27.1, two wrapper invocations exit 0, 15,426 identical records and matching semantic hashes | 2026-09-18 |
| P01.07 | Prove container, K3s/MicroK8s and Falco feasibility | DEVOPS | High | P01.01, P01.02 | DONE | Workload reaches Ready and a scoped runtime event is observed on supported Linux | `docs/environment/CONTAINER_K3S_FALCO_FEASIBILITY.md`; container/K3s workload Ready proven on WSL2 and on the real Ubuntu LTS host (`kind` v0.33.0, node Ready); Falco live syscall capture proven on the Ubuntu LTS host with Falco >=0.44.1's `modern_ebpf` driver (0.39.2 kmod incompatible with kernel 6.4+ `class_create()`; verified with a live triggered event carrying the exact SSH-session process ancestry) | 2026-09-19 |
| P01.08 | Write and verify clean-start developer instructions | DEVOPS | Normal | P01.04-P01.07 | TODO | Another clean environment can install and run the documented smoke check | - | 2026-09-18 |

## Phase 02 - Requirements, architecture and contracts

Exit gate: measurable acceptance, decisions, safety/roles, schemas and mandatory
draft diagrams permit independent implementation.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P02.01 | Define measurable acceptance targets | LEAD/QA | High | P01.02 | DONE | Latency, load, accuracy, false alarm, ETA, safety, recovery and UX targets distinguished from results | `docs/requirements/ACCEPTANCE_TARGETS.md` (30 targets across 8 categories, each with conditions and a Result column held at "Not yet measured") | 2026-09-18 |
| P02.02 | Record architecture and technology decisions | ARCH | High | P01.02 | DONE | Options/trade-offs cover simulator, streaming, data, map, AI, edge, central/cloud and deployment | `docs/decisions/` (ADR-0001 to ADR-0008); ADR-0004 and ADR-0007 are Provisional pending user answers already tracked in `Memory.md` | 2026-09-18 |
| P02.03 | Define device and observation contracts | LEAD/DATA | High | P00.02 | DONE | Versioned schemas/examples cover identity, time, units, quality, provenance and privacy | `source-code/contracts/device/v1/`; `source-code/contracts/observation-envelope/v1/`; `source-code/tests/test_contracts.py`, 6 pytest checks pass | 2026-09-18 |
| P02.04 | Define road geometry, SPaT/MAP and network-state contracts | LEAD | High | P02.02 | DONE | Versioned intersection/lane/movement/signal/freshness semantics validate | `source-code/contracts/road-geometry/v1/`; `source-code/contracts/signal-state/v1/`; `source-code/contracts/network-state/v1/`; 9 new pytest checks (19 total) pass | 2026-09-18 |
| P02.05 | Define forecast, incident, recommendation, command and outcome contracts | LEAD | High | P02.03, P02.04 | DONE | Separate lifecycle records, uncertainty, idempotency and errors validate | `source-code/contracts/{forecast,incident,recommendation,command,outcome}/v1/`; 15 new pytest checks pass | 2026-09-18 |
| P02.06 | Define emergency call, unit, route and coordination contracts | EMERG | High | P02.04 | DONE | Patient-free CAD/AVL abstraction with state machine and alternatives validates | `source-code/contracts/emergency-call/v1/`; `source-code/contracts/emergency-unit-assignment/v1/`; 6 new pytest checks pass | 2026-09-18 |
| P02.07 | Define observability and policy contracts | OPS/SEC | Normal | P02.03, P02.05 | DONE | OTel attributes, SLO measures and OPA inputs/decisions are versioned/tested | `source-code/contracts/{metric-label-set,slo-definition,policy-decision}/v1/`; 9 new pytest checks pass | 2026-09-18 |
| P02.08 | Finalize roles, safety classes and action authority | SEC/GRC | High | P02.05, P02.06 | DONE | Recommend/request/approve/execute/override/demo/audit separation is explicit | `docs/security/ROLES_AND_ACTION_AUTHORITY.md` | 2026-09-18 |
| P02.09 | Draft seven mandatory architecture views | ARCH | High | P02.02, P02.03, P02.04, P02.05, P02.06, P02.07, P02.08 | DONE | Editable system/network/data/workflow/security/hybrid/deployment sources render legibly | `diagrams/sources/01-*.mmd` through `07-*.mmd`; `diagrams/exports/*.svg` rendered via `@mermaid-js/mermaid-cli`, all 7 render without error, 4 visually inspected; `diagrams/README.md` index | 2026-09-18 |

## Phase 03 - Simulation and datasets

Exit gate: deterministic normal/fault/emergency runs and leakage-safe datasets can
be regenerated with protected ground truth.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P03.01 | Build versioned 12-intersection SUMO district | SIM | High | P01.06, P02.04 | DONE | Network has three corridors, crossings, cycle/transit routes and valid signals | `source-code/simulator/network/`; `run_container.sh` output: 12 traffic lights, 16 crossings, 18/18 corridor edges bicycle-isolated, demo run 0 teleports/0 collisions, bus completed 2/2 stops, cyclist confined to dedicated lane | 2026-09-18 |
| P03.02 | Implement deterministic demand and clock | SIM | High | P03.01 | DONE | Seed/run ID reproduces vehicles, pedestrians, cyclists, transit and timestamps | `source-code/simulator/demand/`; `run_container.sh` output: generator byte-identical across repeats (40 vehicles/12 cyclists/16 pedestrians/4 transit), sim byte-identical across repeats, 0 teleports/0 collisions, 4/4 stops completed, clock mapping independently verified | 2026-09-18 |
| P03.03 | Simulate traffic, VRU, signal, weather and road sensors | SIM | High | P02.03, P03.02 | DONE | Catalog measurements have units, identity, location, quality and provenance | `source-code/simulator/sensors/`; `run_container.sh` output: 70 devices (18 loop/18 cycle/16 crossing/12 signal/3 weather/3 road), 1453 observation events, device catalog and event stream byte-identical across two runs; `source-code/tests/test_sensor_catalog.py` (6 tests): schema conformance vs `contracts/device/v1` and `contracts/observation-envelope/v1`, all 5 categories (traffic/vru/signal/weather/road) present, every measurement has unit/quality/confidence, every event has identity/location/provenance | 2026-09-19 |
| P03.04 | Simulate emergency units and CAD/AVL events | SIM/EMERG | High | P02.06, P03.02 | DONE | Ambulance/fire/police unit states and routes reproduce without patient data | `source-code/simulator/emergency/`; `run_container.sh` output: 9 calls (3 ambulance/3 fire/3 police), 9 assignments, 6 CAD/AVL devices, 238 AVL position events, 0 teleports/0 collisions, all byte-identical across two runs; `source-code/tests/test_emergency_contracts.py` (6 tests): schema conformance vs `contracts/emergency-call/v1`, `contracts/emergency-unit-assignment/v1`, `contracts/device/v1`, `contracts/observation-envelope/v1`, no `patient` field/value anywhere, cross-run determinism | 2026-09-19 |
| P03.05 | Implement traffic and safety scenarios | SIM | High | P03.03 | DONE | Normal/peak/event/collision/stall/wrong-way/flood/visibility/signal scenarios have truth | `source-code/simulator/scenarios/`; `run_container.sh` output: 9/9 scenario types present (4 physically simulated via real SUMO runs: normal/peak/event/stall, 0 teleports/0 collisions each incl. peak at 2x demand; 5 labeled telemetry overlays: collision/wrong_way/flood/visibility/signal), stall ground truth from SUMO's own measured stop-output (86.0s-206.0s), all outputs byte-identical across two runs; `source-code/tests/test_scenario_ground_truth.py` (8 tests): all scenario types present, required ground-truth fields, overlay events vs `contracts/observation-envelope/v1`, cross-run determinism | 2026-09-19 |
| P03.06 | Implement device/platform fault scenarios | SIM/OPS | Normal | P03.03 | DONE | Silence/stuck/clock/network/model/service/storage faults have onset/end evidence | `source-code/simulator/faults/`; pure Python, no SUMO/container needed; output: 7/7 fault types, 14 events (2 per fault: onset + recovery), 1 new `aiops_agent` device for model/service/storage, all outputs byte-identical across two runs; `source-code/tests/test_fault_ground_truth.py` (7 tests): all fault types present, required ground-truth fields, events vs `contracts/observation-envelope/v1`, device vs `contracts/device/v1`, cross-run determinism | 2026-09-19 |
| P03.07 | Build run manifests, JSON/event output and replay | SIM | High | P03.03, P03.04, P03.05, P03.06 | DONE | Immutable manifest/hashes and replay produce equivalent accepted sequences | `source-code/simulator/manifest/`; pure Python, no SUMO/container needed; output: 12 source files indexed (4 event streams, 1715 events), manifest verifies clean against disk twice (fresh + reloaded), replay deterministic across two independent runs and duplicate-safe under full self-duplication (same accepted_sha256/count, every duplicate rejected); caught and fixed a real cross-fault event_id collision bug in P03.06; `source-code/tests/test_run_manifest.py` (6 tests) | 2026-09-19 |
| P03.08 | Create disjoint train/validation/test datasets | SIM/QA | High | P03.07 | DONE | Splits differ across seeds/routes/demand/faults and pass leakage checks | `source-code/simulator/datasets/`; pure Python, no SUMO/container needed; 9 seeds disjointly assigned (train 5/validation 2/test 2); output: train 360 demand/35 fault-ground-truth/70 fault-events, validation and test 144/14/28 each; 0 leakage problems (seed sets disjoint, no entity_id/event_id crosses split boundaries, no two splits byte-identical); required P03.06's fault generator to become seed-parametrized (was previously seed-independent); `source-code/tests/test_dataset_splits.py` (7 tests) | 2026-09-19 |
| P03.09 | Verify scenario bounds and document limitations | QA | High | P03.08 | DONE | Automated invariants and visual checks pass; synthetic-data limitations are explicit | `source-code/simulator/verification/`; pure Python, no SUMO/container needed for the checks themselves; 1715 events checked across all 4 stages (sensors/emergency/scenarios/faults), 0 problems (geo-projection round trip, timestamp window, confidence range, speed range, device referential integrity); device-map visual check inspected (all 70 devices land on the 12 expected intersections); `docs/evidence/SIMULATION_LIMITATIONS.md` (consolidated) and `docs/evidence/PHASE_03_SIMULATION_VERIFICATION.md` + 3 committed JSON evidence snapshots; `source-code/tests/test_scenario_bounds.py` (3 tests). **Phase 03 exit gate met: P03.01-P03.09 all DONE.** | 2026-09-19 |

## Phase 04 - Edge AI and offline operation

Exit gate: real optimized local inference is measured; edge identity, buffering,
replay and model lifecycle withstand faults.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P04.01 | Implement edge validation and past-only preprocessing | EDGE | High | P02.03, P03.08 | DONE | Invalid/stale/missing data behavior and feature lineage are tested | `source-code/edge/validation.py`, `features.py`, `topology.py`; `test_edge_validation.py` (incl. 400-iter fuzz test, never raises), `test_edge_features.py`, `test_edge_topology_features.py`, `test_edge_real_telemetry.py` (0 rejections across 1453 real P03.03 events) | 2026-09-19 |
| P04.02 | Build transparent traffic/safety baselines | EDGE | High | P04.01 | DONE | Rules/statistics evaluated on held-out scenarios with failure cases | `source-code/edge/baseline.py`, `models/baselines/fit_evaluate.py`; fit TRAIN/select VALIDATION under a false-alarm-episode-share constraint; `models/registry/baseline/baseline_v1_evaluation.json` (CIs, failure cases); `test_edge_baseline.py` | 2026-09-19 |
| P04.03 | Train traffic/safety edge model | EDGE | High | P04.02 | DONE | Train/validation discipline, provenance, uncertainty and comparison recorded | `source-code/models/train/train_model.py`; 30-candidate search TRAIN, threshold/abstention VALIDATION, one sealed TEST opening (`test_split_ledger.json`); reproducible refit bitwise-identical; model F1 0.524 vs baseline 0.480 on held-out test (48 incidents); `test_models_evaluation.py` | 2026-09-19 |
| P04.04 | Export and verify ONNX model | EDGE | High | P04.03 | DONE | Parity, integrity hash, input/output schema and load failure behavior pass | `source-code/models/export/export_onnx.py`, `edge/model_runtime.py`; ORT-vs-sklearn max diff 6.6e-7 (tol 1e-5), 0 decision mismatches; 8 load-failure modes refused (missing/corrupt/schema-drift/golden-mismatch); `test_edge_model_runtime.py` (17 tests) | 2026-09-19 |
| P04.05 | Implement edge runtime and health | EDGE | High | P04.01, P04.04 | DONE | Real inference, abstention, metrics and readiness run in bounded container | `source-code/edge/runtime.py`, `health.py`, `main.py`, `Dockerfile`; ran in real 0.75 CPU/512 MiB non-root read-only container (warm p95 0.135ms, RSS 75.8MB); bounded Prometheus metrics proven under 3000 hostile device ids; `test_edge_runtime.py` (18 tests) | 2026-09-19 |
| P04.06 | Implement privacy-preserving track/vision metadata path | EDGE/GRC | Normal | P04.01 | DONE | Edge-derived metadata and privacy zones work without identity recognition/raw retention | `source-code/edge/vision_privacy.py`, `vision_devices.py`; privacy-zone suppression, per-window ephemeral HMAC identity, k-anonymity floor (min 3), grounded against a real P03.02 pedestrian trajectory; `test_edge_vision_privacy.py` (25 tests) | 2026-09-19 |
| P04.07 | Implement durable offline outbox and acknowledged replay | EDGE/DATA | High | P04.05 | DONE | Crash/restart/uplink loss preserves order and produces no accepted duplicates | `source-code/edge/outbox.py` (SQLite WAL, ADR-0006); crash recovery proven without `close()`; crash-before-ack resend rejected downstream via `duplicate_event_id`; ordered/idempotent/bounded-quota drain; `test_edge_outbox.py` (18 tests) | 2026-09-19 |
| P04.08 | Implement atomic model activation and rollback | EDGE | High | P04.04, P04.05 | DONE | Invalid/degraded artifacts are refused or rolled back without unsafe serving | `source-code/edge/activation.py`; verify-then-commit, atomic `os.replace` pointer (simulated crash mid-write leaves old pointer intact), rollback re-verifies and refuses a since-corrupted target; composes with `EdgeRuntime.swap_model`; `test_edge_activation.py` (17 tests) | 2026-09-19 |
| P04.09 | Evaluate edge accuracy, latency and resource use | EDGE/QA | High | P04.05, P04.06, P04.07, P04.08 | DONE | Cold/warm benchmarks and held-out metrics include conditions/sample sizes/limits | `source-code/models/evaluation/{prepare_benchmark_run,run_container_benchmark.sh,build_report}.py`; 12,960 real events in real bounded container, warm p95 0.135ms (LAT-01 met), RSS 14.5% of limit; cites (not recomputes) sealed test-split accuracy; `docs/evidence/PHASE_04_EDGE_AI_VERIFICATION.md` + `EDGE_AI_LIMITATIONS.md`; `test_edge_benchmark_report.py` (8 tests). **Phase 04 all 9 tasks DONE.** | 2026-09-19 |

## Phase 05 - Streaming, geospatial storage and backend

Exit gate: secure events persist exactly once by meaning, authoritative state and
staff APIs expose real current/history data.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P05.01 | Provision MQTT, Kafka-compatible stream and PostgreSQL/PostGIS | DATA/DEVOPS | High | P01.07, P02.03 | DONE | Pinned services run with health, persistence and bounded resources | `source-code/infra/platform/` (docker-compose.yml, up/down/verify.sh); Mosquitto 2.0.18, Redpanda v24.2.18 (Kafka-compatible, verified via real `rpk` produce/consume round trip), PostGIS 16-3.4, all digest-pinned; `docker inspect` confirms resource limits match config exactly; functional smoke test + persistence-across-restart proof for all 3 services; `docs/evidence/p05_01_platform_provisioning.json` | 2026-09-19 |
| P05.02 | Secure MQTT identities, mTLS and topic ACLs | SEC/DATA | High | P05.01, P02.08 | DONE | Valid device works; cross-device publish/subscribe and bad trust fail | `source-code/infra/platform/mosquitto/` (gen-certs.sh, acl.conf, verify-mtls.sh); mTLS listener 8883 with per-device client-cert identity (CN=device_id) and `acl_file` pattern-scoped topic ACL; verified against the real broker: valid device round trip OK, cross-device publish/subscribe both confirmed blocked at delivery time, untrusted-CA client cert fails the TLS handshake; `docs/evidence/p05_02_mqtt_mtls_acl.json` | 2026-09-19 |
| P05.03 | Implement MQTT-to-stream gateway and acknowledgements | DATA | High | P04.07, P05.01 | DONE | Validation, partitioning, backpressure and application ack/replay pass | `source-code/backend/gateway/` (gateway.py, verify_gateway.py); reuses `edge.outbox.DurableOutbox` (P04.07) as the gateway's own Kafka-outage buffer; verified against the real stack incl. a genuine Kafka container stop/start: invalid event rejected (never reaches Kafka), valid events partitioned consistently by device_id, events buffered/drained/acked correctly across the outage, replay from earliest offset reproducible; `docs/evidence/p05_03_gateway.json`; `source-code/tests/test_gateway.py` (6 tests) | 2026-09-19 |
| P05.04 | Create migrations for topology, telemetry and control records | DATA/BACKEND | High | P02.03-P02.06, P05.01 | DONE | Ordered checksum migrations/seeds repeat safely with constraints | `source-code/database/` (migrate.py, 6 numbered migrations, seeds/seed_topology.py); verified against the real Postgres/PostGIS: all tables created, re-run is a true no-op, bad enum/dangling FK/invalid date-range all rejected by real constraints, hand-edited applied migration detected via checksum mismatch, topology seed loads the real 12-intersection SUMO network idempotently; `docs/evidence/p05_04_migrations.json`; `source-code/tests/test_database_migrate.py` (4 tests) | 2026-09-19 |
| P05.05 | Implement validated central ingestion and deduplication | DATA | High | P05.03, P05.04 | DONE | Identity/schema/content conflicts reject; identical replay is idempotent | `source-code/backend/ingestion/` (ingest.py, verify_ingest.py); Kafka offset committed only after the DB write commits; verified against the real stack: schema/identity/content conflicts all correctly rejected and recorded in `ingestion_rejections`, identical replay accepted as an idempotent no-op both per-message and for a fresh consumer group replaying the whole topic, original content never overwritten by a conflicting resend; `docs/evidence/p05_05_ingestion.json`; `source-code/tests/test_ingestion.py` (3 tests) | 2026-09-19 |
| P05.06 | Implement network-state and freshness service | BACKEND | High | P05.05 | DONE | Segment/intersection/lane state preserves source time, quality and geometry | `source-code/backend/state/network_state.py` (compute_state, lane/intersection/corridor; `segment` documented `NotImplementedError` - no segment topology exists yet); verified against the real Postgres incl. schema-validating a produced record against the real `contracts/network-state/v1/schema.json`: windowed averaging, worst-quality-wins, carry-forward on empty windows (sample_count=0), fresh/stale freshness, geometry_version preserved, never-observed returns None; `docs/evidence/p05_06_network_state.json`; `source-code/tests/test_network_state.py` (5 tests) | 2026-09-19 |
| P05.07 | Implement device, traffic, history and live APIs | BACKEND | High | P05.06 | DONE | Versioned paginated APIs/live reconnect expose real persisted data | `source-code/backend/api/` (FastAPI+uvicorn, no prior ADR fixed a framework - documented rationale in README); verified against a real running server on a real socket (not TestClient): cursor pagination exact/no-repeat, network-state endpoint round-trips through P05.06, unknown-device 404, `segment` 501 not faked, live feed sends correct backlog, disconnect/reconnect receives exactly the missed event with no re-delivery, device-scoping excludes other devices; `docs/evidence/p05_07_api.json`; `source-code/tests/test_api.py` (3 tests) | 2026-09-19 |
| P05.08 | Implement incident and command repositories/state machines | BACKEND | High | P02.05, P05.04 | DONE | Valid transitions/concurrency/idempotency/audit references pass | `source-code/backend/repositories/` (incidents.py, commands.py); `database/migrations/0008_audit_trails.sql` (append-only transition history); verified against the real Postgres: valid transition chain + audit trail, invalid transitions rejected, idempotent command creation (resubmission returns same id, no duplicate row), missing-audit-reference refused for approve/fail, and two threads racing a transition on the same row via separate connections where exactly one wins (real row-lock proof, not a mock); `docs/evidence/p05_08_repositories.json`; `source-code/tests/test_repositories.py` (7 tests) | 2026-09-19 |
| P05.09 | Implement separate authorized scenario-control API | BACKEND/SIM | Normal | P03.07, P02.08 | DONE | Start/status/reset/replay are bounded, role-separated and audited | `source-code/backend/scenario_control/` - separate FastAPI app (not a router on P05.07's API), wraps P03.07's real `manifest/replay.py`; role check uses the real P02.08 role name (`demo_operator`, DEMO authority only - fixed from a placeholder `SIM`/`DEVOPS` pair); bounded via `pg_advisory_xact_lock` (a plain `FOR UPDATE` count cannot stop a new concurrent insert); verified with genuinely concurrent requests (`asyncio.gather`): role refusal audited, real 1715-event manifest replay, deterministic replay hash, bound holds under real concurrency (429 past the limit); `docs/evidence/p05_09_scenario_control.json`; `source-code/tests/test_scenario_control.py` (3 tests) | 2026-09-19 |
| P05.10 | Implement retention, aggregation and storage-pressure handling | DATA/GRC | High | P05.04 | DONE | Policy enforces classes while preserving audit/active control and safe backpressure | `source-code/database/retention.py`; `database/migrations/0010_retention.sql`, `0011_retention_cascades.sql`; verified against the real Postgres: per-retention_class windows enforced, `audit` class never purged, aggregation rollup created before raw-row deletion, terminal commands/incidents purged past window (cascading their own audit trail) while active/non-terminal ones survive regardless of projected age, storage-pressure forcibly shortens the effective window on the same data (real before/after, not a config check); `docs/evidence/p05_10_retention.json`; `source-code/tests/test_retention.py` (5 tests). **Phase 05 exit gate met: P05.01-P05.10 all DONE.** | 2026-09-19 |

## Phase 06 - Traffic intelligence and incident correlation

Exit gate: measured forecasts and detectors create explainable correlated traffic,
safety and data-quality incidents with controlled false alarms.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P06.01 | Implement traffic aggregates and corridor KPIs | BACKEND | High | P05.06 | DONE | Speed/volume/density/queue/travel/delay/throughput/reliability validate | `source-code/backend/analytics/` (kpis, topology, calibration, kpi_service, truth_validation); built the shared Phase 06 SUMO dataset first (`source-code/models/intelligence_dataset/`: 36 runs x 180 min, 233,280 loop events, SUMO edge-wide ground truth, 0 teleports/collisions, twin rebuild byte-identical); migration 0012 (`network_segments`, `corridor_kpis`); held-out (val+test, 3,456 windows) vs SUMO truth: volume MAPE 23.7%/bias 1.6%/r 0.96, density r 0.85, travel-time MAPE 7.3%, speed MAPE 8.5%; congested-window error (41% travel-time MAPE) and the weak queue proxy reported not hidden; lane share is the one fitted parameter, TRAIN-only; DB-backed KPIs equal file-computed KPIs, idempotent, every KPI valid `network-state/v1`, historical windows served `stale`; `GET /api/v1/kpis/corridors`; `compute_state` now supports `segment`; `docs/evidence/p06_01_kpis.json`; `source-code/tests/test_analytics_kpis.py` (10 tests) | 2026-09-20 |
| P06.02 | Build short-horizon forecast baseline | EDGE/BACKEND | High | P03.08, P06.01 | DONE | 5/15/30-minute baseline and uncertainty evaluated on held-out runs | `source-code/backend/analytics/{forecast_data,forecast_baselines,evaluate_baselines}.py`; four transparent baselines (persistence, moving average, linear trend, TRAIN-fitted time-of-day mean) selected per (horizon, target) on VALIDATION, split-conformal 80% intervals calibrated on validation residuals, TEST scored once and ledgered as non-selecting `baseline_report`; held-out 80% coverage 0.78-0.81; persistence MAE 58 veh/h on steady vs 218-324 on ramping windows (recorded failure mode); `models/registry/traffic-forecast/baseline_evaluation.json`, `docs/evidence/p06_02_baselines.json`; `source-code/tests/test_forecast_baselines.py` (7 tests: no feature leakage, baseline arithmetic, conformal coverage, bootstrap) | 2026-09-20 |
| P06.03 | Train/evaluate network traffic forecast model | EDGE | Normal | P06.02 | DONE | Model beats or is rejected against baseline; drift/failure modes recorded | `source-code/backend/analytics/{forecast_model,train_forecast,forecast_service,verify_forecasts}.py`; migration 0013 (`forecasts`); 7 candidates x 9 (horizon, target) pairs fitted on TRAIN, accepted only if >=3% better than the P06.02 baseline on VALIDATION, split-conformal intervals; TEST opened exactly once (ledger refuses a reopen - verified); held-out: model significantly better for 5-min volume and 15-min volume/density, no significant difference for 5-min density and 30-min volume, rejected on validation for all three travel-time pairs (baseline served, labelled); adverse finding recorded, not hidden: 30-min density accepted on validation but significantly WORSE on TEST; interval coverage 0.71-0.80 vs 0.80 nominal; PSI max 0.055; drift monitor 11/48 false alarms vs 26/48 detections on an injected demand step (coarse); serving equals offline evaluation to 5e-5, 144 emitted forecasts valid `forecast/v1`, abstains on stale/gap/invalid/out-of-range, sha256-verified package; `GET /api/v1/forecasts/corridors`; `models/registry/traffic-forecast/1.0.0/model_card.json`, `docs/evidence/p06_03_forecast_model.json`; `source-code/tests/test_forecast_model.py` (6 tests) | 2026-09-20 |
| P06.04 | Implement congestion and spillback detection | BACKEND | High | P06.01 | DONE | Location, severity, onset/clear and evidence pass scenarios | `source-code/backend/analytics/{congestion,evaluate_congestion,congestion_service,verify_congestion}.py`; migration 0014 (`detection_candidates`, shared by P06.04-06), `GET /api/v1/candidates`; rule detector + observed/inferred spillback, params chosen on validation (18+12 grid), TEST scored once (ledgered non-selecting); held-out per class: blockage precision 1.00 (Wilson 0.65-1.00) recall 0.37, median delay 90 s, spillback 3/4 matched; no false alarms in steady classes or the P03 normal run; recall bounded by loop placement (reported by truth severity), severity agreement weak (29% within one level) - reported not hidden; fixed `detected_at` overwrite bug that inflated delay; DB detector == file detector, evidence ids real and on-segment, open episodes closed in place; `models/registry/congestion-detector/evaluation.json`, `docs/evidence/p06_04_congestion.json`; `source-code/tests/test_congestion.py` (13 tests) | 2026-09-20 |
| P06.05 | Implement collision/stall/wrong-way/hazard candidates | EDGE/BACKEND | High | P04.09, P05.06 | DONE | Multi-source candidates meet held-out metrics and retain uncertainty | `source-code/backend/analytics/{stall_candidates,evaluate_stall,overlay_candidates,evaluate_overlays,safety_service,verify_safety_candidates}.py`; stalled vehicle = real EdgeRuntime + ONNX blockage model fused with P06.04 congestion (noisy-OR, per-source parts kept, edge abstain never raises a candidate), 9 settings scored on TRAIN/VALIDATION, TEST scored once (ledgered non-selecting): recall 5/6 (Wilson 0.44-0.97), candidate precision 5/13 = 0.38 (0.18-0.65, reported not gated), 0.17 false alarms per incident-free hour, median delay 139 s; blockages 70 m past the loop missed. Collision/wrong-way/flooding/low-visibility = event-driven overlay state machines evaluated as a SPECIFICATION TEST (216/216 injected exact, 0/270 hard negatives, 0 from normal run and fault streams, stuck-sensor guard, flood/friction contradiction), not a skill claim (SUMO cannot simulate them). 19 real-Postgres checks (DB path == file path, evidence ids exist, idempotent, API), 12 unit tests; evidence `docs/evidence/p06_05_safety.json` | 2026-09-20 |
| P06.06 | Implement pedestrian/cyclist conflict indicators | EDGE/GRC | Normal | P04.06 | DONE | Trajectory measures avoid identity data and document false-positive/bias limits | `source-code/edge/vru_conflict.py`, `source-code/models/vru_dataset/` (18 real SUMO runs with pedestrians crossing at marked crossings; twin rebuild byte-identical), `source-code/backend/analytics/{vru_data,evaluate_vru,vru_service,verify_vru_conflicts}.py`; truth = realized PET from clean 4 Hz trajectories (conflict = PET <= 3 s at vehicle speed >= 4 m/s, threshold fixed after inspecting validation and disclosed), detector sees only a degraded 1 Hz noisy anonymous tracker view; predicted-PET geometry + 11-feature logistic scorer chosen on validation, TEST scored once (ledgered non-selecting). HONEST RESULT: weak site-level indicator, not an alarm - TEST event precision 0.14 (0.09-0.22), recall 0.13 (0.08-0.21), window-level Spearman 0.41, window precision 0.50; gradient boosting ceiling equal (limit is information in 1 Hz tracks); off-peak recall 0/12, slow pedestrians 0/6, sigma 0.8-1.2 m noise floods false alarms. Cyclists NOT validated (SUMO bike lanes give no cyclist-vehicle conflict: 968 visits, 0 alerts, 0 interactions) so cyclist candidates are off. Privacy: privacy-zone drop before computation, per-window HMAC ids (results identical under two salts), k>=3 floor (cost measured: 0/106 true conflicts lost on TEST), aggregates only leave the edge, no id/position in any stored event; 21 real-Postgres checks + 14 unit tests; evidence `docs/evidence/p06_06_vru_conflicts.json`, `p06_06_vru_evaluation.json` | 2026-09-20 |
| P06.07 | Correlate evidence into traffic/safety incidents | BACKEND | High | P05.08, P06.04-P06.06 | DONE | Duplicate sources group; hypotheses, escalation, resolution and reopen pass | `source-code/backend/analytics/{correlation,incident_service,calibrate_incident_policy,verify_incidents}.py`, migration 0015 (`incident_candidates`, `incident_hypotheses`, `duplicate_of`, `evidence_cleared_at`, `evidence_sources`), `GET /api/v1/incidents[/{id}]` (incident/v1-valid), P05.08 repository extended with explicit-time transitions; graph-aware grouping (duplicates, upstream consequences up to 3 hops, related causes), CALIBRATED confidence from measured TRAIN+VALIDATION precision (congestion 0.85, spillback 0.50, stall 0.63 corroborated / 0.46 alone, pedestrian window 0.50; overlay kinds are ASSUMED priors, marked as such), only independent sensor modalities add up, open threshold 0.6 keeps single-source weak candidates on a watch list; ranked hypotheses never a verified cause; merge/escalate (unacknowledged 600 s, critical, 2 modalities)/auto-resolve (open/reopened only, 120 s hysteresis)/reopen via P05.08 state machine. 33 real-Postgres checks: held-out blockage run replayed in 5-min ticks (21 candidates -> 7 incidents, idempotent re-sync hash-verified) + labelled synthetic candidates for merge/reopen/escalation/operator ownership/watch list; 15 unit tests; evidence `docs/evidence/p06_07_incidents.json`. Incident-level FA-01/LAT-03 measured in P06.08 | 2026-09-20 |
| P06.08 | Evaluate intelligence and incident outcomes | QA | High | P06.02, P06.03, P06.04, P06.05, P06.06, P06.07 | DONE | Detection/forecast/incident latency and errors measured by scenario and road user | `source-code/backend/analytics/{evaluate_intelligence,evaluate_p06_08}.py`; incident-level evaluation (detectors -> candidates -> correlation -> incidents vs SUMO truth) plus every P06.02-07 held-out number assembled, never recomputed. FA-01 MET (0/7 = 0%, Wilson 0.0-0.35, small sample); LAT-03 MET (P95 0.0 s, event-driven correlation - a periodic-poll deployment must trigger on candidate write); ACC-02/ACC-03 reported per (horizon,target)/scenario class, unblended, including where the model loses to baseline and is rejected. FA-02 and SAFE-03 explicitly NOT closed here and said so: FA-02 needs P10.07s data-quality incident detector (not yet built; P03.06 fault catalog held ready), SAFE-03 needs P07.05s command policy to act on `freshness_status` (currently only reported, not enforced) - both carried forward rather than fabricated. ACCEPTANCE_TARGETS.md rows updated. Evidence `docs/evidence/p06_08_acceptance.json`, `p06_08_incident_evaluation.json`. **Phase 06 all 8 tasks DONE.** | 2026-09-20 |

## Phase 07 - Emergency coordination and governed traffic control

Exit gate: three emergency scenarios and traffic actions complete the authorized
request-to-outcome path in simulation with safety and rollback evidence.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P07.01 | Implement CAD/AVL adapter and emergency state | EMERG | High | P03.04, P05.08 | DONE | Calls/units/assignments/status persist with simulated-source labels | `source-code/backend/emergency/{cad_avl_adapter,verify_emergency}.py`, `source-code/backend/repositories/emergency.py`, migrations 0016/0017 (call/assignment transition tables, contract-complete columns, retention cascade), `GET /api/v1/emergency/calls[/{id}]`; two explicit-time row-locked state machines (call: received..cleared/cancelled; assignment: assigned..clear/unavailable, matching the contracts` own enums) mirroring P05.08s incident pattern; `cad_avl_adapter.plan_replay` derives each calls/assignments timeline from P03.04s real one-final-record CAD export and replays all 9 calls/9 assignments interleaved in true chronological order (not call-by-call) through the real repository; AVL telemetry (238 events, 6 devices) is real observation-envelope/v1 device telemetry through P05.05s real ingestion, not a position column. 19 real-Postgres + API checks: full real dataset (all 3 call types) reaches terminal status, re-loading already-ingested CAD data is rejected not silently duplicated, plus labelled synthetic records prove cancelled/unavailable/reassignment/handover (paths the real dataset never exercises); 7 unit tests; evidence `docs/evidence/p07_01_emergency.json` | 2026-09-20 |
| P07.02 | Implement fastest-safe route and ETA alternatives | EMERG | High | P05.06, P07.01 | DONE | Routes honor closure/hazard/vehicle constraints and expose uncertainty | `source-code/backend/routing/{graph,live_state,router,route_service,verify_routing}.py`, `GET /api/v1/routes`; Dijkstra over the real 26-segment P03.01 network graph, live state from REAL incidents (P06.07) and REAL corridor KPIs (P06.01, <15 min live budget matching SAFE-03s never-use-stale discipline) - a high/critical-severity collision/stall/wrong-way/flooding incident CLOSES its segment (never traversed), a lower-severity or congestion/spillback/conflict incident is a HAZARD (heavily penalized, still passable, labelled `through_hazard:...`); ETA always reports real (un-inflated) travel time; uncertainty uses measured buffer_index when a live KPI exists else a stated 15% default margin, never a fabricated precise number; alternatives via penalize-and-resolve, deduped by Jaccard overlap. 11 real-Postgres checks (real high-severity incident closes its segment and the grid reroutes; medium-severity is a hazard not a closure; synthetic recent KPI measurably changes travel time/uncertainty, a stale one does not; API contract-shaped) + 11 unit tests; evidence `docs/evidence/p07_02_routing.json` | 2026-09-20 |
| P07.03 | Implement cross-agency staging and handover | EMERG | Normal | P07.01 | DONE | Ambulance/fire/police timelines, acknowledgements and handovers are consistent | `source-code/backend/emergency/{cross_agency,verify_cross_agency}.py`; staging is a dispatch-policy layer on P07.01s state machines (not baked into them) - `StagingStep.after` names the prerequisite UNIT, `advance_to_scene` genuinely blocks (raises StagingViolation, staged transition recorded in the audit trail, not skipped) until that unit has actually arrived, `record_handover` attaches from_agency/to_agency/acknowledged_by to the departing units own assignment. 10 real-Postgres checks: real 3-agency collision call (police -> fire staged behind police -> ambulance staged behind fire), fire AND ambulance both attempted early and genuinely rejected then released once their real prerequisite arrived, handover chain police->fire and EMS->fire with every required field, no patient/medical field anywhere (grepped), P07.01s call-timeline derivation verified correct for a multi-unit call; 5 unit tests (pure dependency-ordering, no DB); evidence `docs/evidence/p07_03_cross_agency.json` | 2026-09-20 |
| P07.04 | Implement traffic recommendation/constraint engine | CONTROL | High | P06.01, P02.08 | DONE | Signal/diversion/priority options show alternatives, benefit and safety bounds | `source-code/backend/control/{engine,diversion,signal_plan,recommendation_service,verify_recommendations}.py`, `source-code/backend/repositories/recommendations.py`, migration 0018, `GET /api/v1/recommendations`; shared Alternative/SafetyBounds shape (contracts/recommendation/v1) with HARD enforcement - an over-bound alternative is DROPPED not clipped, all-unsafe raises UnsafeAlternative - this is the first of two independent safety checks (P07.05s execution-time policy is the second); diversion reuses P07.02s real router (real ETA delta x real corridor throughput); signal-plan-change is a bounded green-time reallocation using real measured corridor delay/queue_fraction (honestly scoped: no per-movement signal-plan model exists yet, stated as an assumption); superseding (fresher recommendation replaces prior live one for same incident) and expiry persist. 17 real-Postgres checks (real closed segment -> real diversion route, real KPI -> real signal alternatives, over-bound alternative verified dropped not merely flagged, contract-schema-valid API) + 7 unit tests; evidence `docs/evidence/p07_04_recommendations.json` | 2026-09-20 |
| P07.05 | Implement command approval and execution-time policy | CONTROL/SEC | High | P05.08, P07.04 | DONE | Role, second approval, expiry, target, bounds and policy outage denials pass | `source-code/backend/control/{policy,command_service,verify_commands}.py`, `source-code/backend/repositories/commands.py`, `GET /api/v1/commands[/{id}]`; second, independent safety check (P07.04s enforce() is the first, at generation time) - pure evaluate() decision function over a PolicyContext snapshot, checked in order: expiry, four-eyes (requester != approver, structural not UI), role (ALLOWED_APPROVER_ROLES per action_type), target validity (real adapter + real currently-registered network element), recommendation bounds linkage (must exist, match action_type, still proposed/requested), SAFE-03 fresh-evidence gate (reuses P07.02s live-KPI/telemetry sources). Policy outage (build_context DB failure, real or injected) fails CLOSED - policy_unavailable, command stays requested, never silently approved. idempotency_key UNIQUE at the DB level, not just convention - a concurrent duplicate resolves to the existing command. 21 real-Postgres checks (every denial reason forced for real on the live network incl. genuine stale-evidence and a genuine outage-then-recovery) + 14 unit tests; evidence `docs/evidence/p07_05_commands.json` | 2026-09-20 |
| P07.06 | Implement simulator signal/diversion/VMS adapters | CONTROL/SIM | High | P07.04, P07.05 | DONE | Only registered idempotent simulator actions execute and acknowledge observed state | `source-code/simulator/control_adapters/{adapters,run_action,run_container.sh}`, `source-code/backend/control/{simulator_adapters,verify_simulator_adapters}.py`; real TraCI against the pinned SUMO image (not mocked) - signal_controller_adapter extends a real actuated traffic lights current phase (traci.trafficlight.setPhaseDuration, clamped to max_deviation_s independently of P07.04/05s own checks), diversion_adapter closes real general-traffic lanes (traci.lane.setDisallowed, observed back from SUMO), vms_adapter has NO real SUMO actuation and says so (no native VMS API - a stated, honest limitation). One fresh isolated container per action (this projects real field boundary); idempotency enforced by a real ledger file on the host-mounted volume - a repeat idempotency_key never even starts SUMO. Only executes an already-approved command (P07.05s gate), only a KNOWN_ADAPTERS-registered target (re-checked here independent of policy). 10 real-Postgres + real-Docker + real-TraCI checks (signal light genuinely moves, lanes genuinely close, unrecognized target genuinely fails the command, unapproved/unregistered genuinely refused before dispatch, idempotent replay byte-identical) + 7 unit tests (mocked traci); evidence `docs/evidence/p07_06_simulator_adapters.json` | 2026-09-20 |
| P07.07 | Implement emergency green corridor/pre-emption | EMERG/CONTROL | High | P07.02, P07.06 | DONE | Clearance/conflict rules, abort and safe fallback pass for emergency routes | `source-code/backend/control/{preemption,preemption_service,verify_preemption}.py`, `source-code/simulator/control_adapters/preemption_run.py`; clearance is safe BY CONSTRUCTION not a bolted-on check - pre-emption only ever shortens the CURRENT phase and lets the compiled programs own next-sequence choose what follows (never jumps to an arbitrary phase), so every yellow/all-red interval the network designer encoded (P03.01) is respected. REWORKED during P07.10: the first version drove every intersection to a fixed phase 0, which made pre-emption slower than no action on a turning route, and shortened yellow/pedestrian phases along with actuated ones (passed the old phase-order check while genuinely cutting a clearance interval short). Fixed with `simulator/control_adapters/phase_control.MovementPriority` (targets whichever phase gives the vehicle's actual movement - from TraCI's own controlled-link table - a green; only shortens actuated service phases down to their own minDur, never a fixed yellow/all-red/pedestrian phase) plus an independent runtime `signal_safety.SignalSafetyMonitor` (reads raw signal state every step, checks it against the network's own compiled design: conflicting greens, missing/short yellow, short pedestrian green/clearance) that found both defects and now gates every P07.07/P07.08/P07.10 run at zero violations. A negative control (`preempt_naive`, the original mechanism, explicitly never used by the platform) is run on the same scenario in `verify_preemption.py` to show it passes the old check while the monitor catches it for real. Real paired same-seed comparison now measures a real 6% travel-time reduction (81s->76s) on this scenario; P07.10's fifteen-trial evaluation is the properly powered ETA-03 measurement. Session holds exactly one intersection at a time from the vehicle's real route (P07.02); abort/completion restores every touched intersection to its REAL original program id (confirmed by reading it back from TraCI), never reported as success. Roles reworked to the real P02.08 model (`backend/roles.py`): `dispatcher` REQUEST, `incident_commander`/`supervisor` APPROVE with four-eyes, `system:command-executor` the only EXECUTE identity. 12 real-Postgres+Docker+TraCI checks (real call->real route->real policy approval->real SUMO execution, movement-served-a-green check, safety-monitor-zero-violations check, negative control, paired ETA comparison, abort+restore) + 8 unit tests (`test_preemption.py`) + 13 unit tests (`test_signal_safety.py`); evidence `docs/evidence/p07_07_preemption.json` | 2026-09-20 |
| P07.08 | Implement transit priority and balanced corridor action | CONTROL | Normal | P07.06 | DONE | Benefits and harms to transit/traffic/pedestrians/neighbors measured | `source-code/simulator/control_adapters/transit_priority_run.py` (reuses P07.07/P07.10's movement-aware `phase_control` + independent `signal_safety` monitor, not duplicated), `source-code/backend/control/{transit_priority_service,verify_transit_priority}.py`; int-b1 used deliberately - confirmed via real TraCI getControlledLinks to be a genuine multi-approach intersection with real vehicular cross traffic (a plain through-intersection here has a pedestrian-only minor phase, not a competing vehicle movement - documented, not guessed). Real paired same-seed comparison, both runs sampled over the SAME fixed 150s window after the bus departs (fixed from an earlier version where the priority run's own earlier arrival shortened its own sampling window, which could bias the harm number for free), measured BOTH sides honestly: 28% real transit travel-time reduction (109s->79s) AND cross-traffic mean wait (5.62s->2.09s). Independent signal-safety monitor reports zero violations in both runs; target phase independently confirmed to give the bus's actual movement a green. Role reworked to real P02.08 `supervisor` approval. 10 real-Postgres+Docker+TraCI checks (real command->real supervisor-role policy approval->real SUMO execution, balanced benefit/harm measurement over a matched window, movement-served-a-green check, safety-monitor-zero-violations check, phase-transition-vs-program-definition check) + 3 unit tests; evidence `docs/evidence/p07_08_transit_priority.json` | 2026-09-20 |
| P07.09 | Implement independent outcome verification and rollback | CONTROL/OPS | High | P07.06-P07.08 | DONE | Pre/post windows classify outcomes and trigger rollback/escalation correctly | `source-code/backend/control/{outcome_verification,live_session,verify_outcomes}.py`, `source-code/backend/repositories/outcomes.py`, `source-code/simulator/control_adapters/session_server.py`, API `GET /api/v1/outcomes[/{id}]`. A live lock-step SUMO session (P07.06's per-action container cannot be measured around or undone) gives a real before window, the action, a real after window and a physical undo read back from TraCI. Thresholds calibrated on separate no-action runs (noise band 7.39 -> 15 vehicle-s). Real results: closing busy edge int-b2_int-b3 raised waiting 10.0 -> 459.3 = unsafe, command executed->rolled_back by an independent verifier, lanes read back closed then reopened, waiting recovered to 89.0; a 15 s signal extension is ineffective and a same-seed no-action control is identical (before/after cannot separate an action from drift below the noise band - stated); paired transit priority 109 s -> 79 s = effective; post-action telemetry gap = unknown + escalated (never defaulted to effective); an unavailable/failed undo leaves the command executed + escalated (a rollback is claimed only when it verifiably happened); verifier must differ from requester/approver/executor (service and repository). Non-actuating/gap cases use labelled fixture KPI rows on a coherent replay timeline. LAT-04 measured: warm live session P95 0.057 s (n=24), cold container-per-action P95 2.70 s (n=5, thin margin). 31 real-Postgres+Docker+TraCI checks + 10 unit tests; evidence `docs/evidence/p07_09_outcomes.json` | 2026-09-20 |
| P07.10 | Verify ambulance, fire and police end-to-end scenarios | QA | High | P07.01, P07.02, P07.03, P07.04, P07.05, P07.06, P07.07, P07.08, P07.09 | DONE | Three scenarios measure ETA/safety/traffic outcomes plus failure limits | `source-code/backend/control/{emergency_flow,verify_scenarios}.py`, `source-code/simulator/control_adapters/signal_safety.py` (independent runtime monitor, shared with P07.07/P07.08). Every dispatch runs the real platform path: live-state-aware ETA from a real no-vehicle simulator snapshot published as real `corridor_kpis`, real assignment, real pre-emption request->policy->execution, real P07.09 outcome verification, paired same-seed baseline. Building this suite's independent signal-safety monitor found the pre-emption mechanism's first version unsafe (wrong-phase-target and shortened-clearance defects - see P07.07); every run here uses the fixed mechanism. ETA-01 met: pooled MAE 8.4% (n=15, 3 scenarios x 5 trials, normal traffic). ETA-02 met: pooled MAE 10.55% (n=15) under a real segment-closing incident with a genuine reroute. ETA-03 met: 30 paired dispatches, mean reduction 8.13s/6.1%, 19 improved/9 tied/2 worse, sign test p=0.0001. SAFE-01 met: 0 violations over 162,072 real intersection-state observations across every run including failure-limit runs. Traffic outcomes measured and reported honestly (network wait +0.77s mean, cross-street +0.30s mean, paired). Failure limits each run with its measured consequence: heavy congestion (6.57% MAE, still completes), no route (0 commands created), policy outage (fails closed, later expires), adapter failure (retryable, unit continues unassisted), stale evidence (denied outright). Real staged cross-agency response (police->fire->EMS, P07.03) and a recorded emergency timeline that matches the simulated travel time to the second. 18 real-Postgres+Docker+TraCI checks; evidence `docs/evidence/p07_10_scenarios.json` | 2026-09-20 |

## Phase 08 - Operations experience

Exit gate: role-specific control-room/field workflows work against real APIs and
meet design, accessibility and action-feedback acceptance.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P08.01 | Define role journeys and screen/state inventory | UX | High | P02.08, P05.07 | DONE | All roles/tasks/errors/freshness/action states map to capabilities | `docs/ux/ROLE_JOURNEYS_AND_SCREEN_INVENTORY.md`, single machine-readable source `source-code/frontend/src/config/inventory.json` (7 roles, 17 capabilities, 21 screens, 43 API endpoints, 7 role journeys each with designed failure cases) that the backend and frontend will both read - no second copy to drift - and `source-code/frontend/tools/inventory.py` proving it against `backend/roles.py` (P02.08) and against the endpoints the real FastAPI apps serve (an `implemented` endpoint must exist, a served endpoint must be listed, a `planned:<task>` endpoint is counted so a later task cannot quietly leave one unbuilt); found and fixed 3 real design errors while building it (screens the demo operator can open calling APIs it may not, handover writes) by splitting each screen's calls into required vs optional-by-capability. Freshness/truth-label display states, session/authorization states, command/recommendation/outcome/incident/call lifecycles, an error taxonomy mapped from the real backend error codes, and what is deliberately outside this UI (override -> P09.05, etc.) all documented; generated tables checked current. 6 real checks + 7 unit tests; evidence `docs/evidence/p08_01_inventory.json` | 2026-09-21 |
| P08.02 | Create editable map, incident and emergency wireframes | UX | High | P08.01 | DONE | Laptop/projector/field layouts and accessible alternatives are reviewable | `docs/ux/WIREFRAMES.md` + 7 editable SVG wireframes in `docs/ux/wireframes/` (live map, accessible list alternative, projector wall display, mobile field view, incident investigation, emergency dispatch, critical-action approval dialog) generated by `build_wireframes.py` so header/nav/status shapes are shared and numbered callouts single-sourced; navigation and identity in every wireframe derived from the same `inventory.json` so a wireframe cannot show a role a menu entry it does not hold; PNG previews rendered with real headless Chrome in `docs/ux/wireframes/previews/`; keyboard/focus order per screen, layout classes (laptop 1366x768, projector 1920x1080, field 390x844), accessible-alternative map. `source-code/frontend/tools/verify_wireframes.py` proves declared sizes, required regions, contiguous documented callouts, role-matching navigation, the field and projector views carry NO control and the field has 44px touch targets, and each SVG renders to a non-blank image in real Chrome. 9 real checks; evidence `docs/evidence/p08_02_wireframes.json` | 2026-09-21 |
| P08.03 | Complete design tokens/components/interaction handoff | UX | High | P08.02 | DONE | Components, focus, responsive, chart/table and API/role mapping are explicit | `docs/ux/DESIGN_SYSTEM.md` (extends the existing `design-system/.../MASTER.md`), single-sourced `source-code/frontend/src/design/{tokens.json,status.json}` with generated `tokens.css`, and `source-code/frontend/tools/design.py`. 26 components each with purpose/anatomy/states/keyboard/ARIA/tokens/used-by, a 21-screen composition table, interaction rules (focus, keyboard, live regions, forms, critical-action confirmation, loading/error, formatting, motion, zoom), chart+table pairing rules, responsive classes, and decisions recorded (React+TS+Vite, plain CSS, SVG schematic map instead of MapLibre - the network is 12 intersections/26 segments with no offline tiles and SVG is natively focusable/testable - fonts self-hosted, keycloak-js). Contrast is COMPUTED not asserted: 30 pairs, lowest 3.19:1, each departure from MASTER.md re-measured to really fail (border 2.07:1 on cards, destructive-as-text 4.16:1, a lone white ring 2.28:1 on the accent button -> new border-strong, danger-text and a two-tone focus ring). Status vocabulary: 58 states across 11 domains, every backend enumeration read from the real repositories/migrations (not retyped) covered, no two states of a lifecycle share a shape or label so command/outcome/incident lifecycles are distinct in greyscale (UX-03). Screen composition and component used-by lists agree in both directions. 10 real checks + 9 unit tests; evidence `docs/evidence/p08_03_design.json` | 2026-09-21 |
| P08.04 | Build frontend shell and Keycloak session | UI | High | P08.03, P09.02 | DONE | Protected navigation/login/logout/session/denials work for all roles | `source-code/frontend/` (React 19 + TypeScript strict + Vite, react-router 7, keycloak-js, plain CSS on the P08.03 tokens): AppShell with skip link, role-filtered navigation, SIMULATED tag and one `main`; route guards derived from `inventory.json`; sign-in/callback/session-ended/not-permitted/not-found screens; the token lives in memory only (a reload re-checks the identity provider once instead of asking again and the destination survives sign-in); one `api()` client with a single forced refresh on 401 then session end. `backend/api/{auth,authz,verify_auth}.py`: every request validated strictly (RS256/JWKS, issuer, audience `aiops-api`, `exp`/`iat`/`sub`, authorised party, operating role), the endpoint's capability read from the same inventory, an unlisted route denied by default, 403s audited, WebSocket by `?access_token=`. 17 real API checks with real tokens (wrong audience/issuer, expired, tampered, `alg=none`, HS256 key confusion, unknown client, no role, identity provider unreachable = 503 fail closed, every role x every endpoint, logout kills the refresh token) + 16 real-Chrome tests (each of the 7 roles lands on its home screen with exactly the navigation its capabilities allow, deep link kept, session ended elsewhere, sign-out ends the provider session). `docs/evidence/p08_04_auth_api.json`, `p08_04_ui_auth.json` | 2026-09-21 |
| P08.05 | Build live operations map and accessible list | UI | High | P08.04, P05.07 | DONE | Layers, freshness, selection, replay, reconnect and table fallback use real APIs | `src/pages/MapPage.tsx`, `src/features/map/`: a hand-built SVG schematic of the real 12-intersection/26-segment network (both directions of a road drawn side by side; one-way roads centred), layers (traffic, devices, incidents, emergency), selection panel with value, unit, observed time, truth label and freshness, the live WebSocket feed with reconnect that resumes from the last event it received (no gap; proven by proxying the real socket and dropping it), pause/resume, replay of recent history that is labelled not-live everywhere and only replays what history supports, a critical-incident queue for roles that may see it, a wall-display variant with no controls, and the accessible list (every element with status, freshness and truth as columns, sortable, filterable, selection kept). Every fact on the map is also in the list. 13 real-Chrome tests incl. keyboard (zoom/pan/Tab/Enter/Escape), axe on map and list. `docs/evidence/p08_05_ui_map.json` | 2026-09-21 |
| P08.06 | Build corridor/intersection/device analytics | UI | Normal | P08.05, P06.01 | DONE | Real current/history/forecast/quality/health views are clear and testable | `src/pages/{CorridorAnalytics,IntersectionAnalytics,DeviceHealth}Page.tsx`, `src/features/analytics/`, `src/components/LineChart.tsx`: corridor KPIs (latest complete window with units, truth label and freshness; measured and forecast series with the 80% interval and the model named; a chart is always paired with its table), detection candidates, intersection tabs (movements, devices, observations; deep link kept when the intersection changes; says plainly when no detector has ever reported), and device health (reporting/stale/never-reported counts, filters, sortable by keyboard, a device's registration and latest observations). 19 real-Chrome tests + axe. `docs/evidence/p08_06_ui_analytics.json` | 2026-09-21 |
| P08.07 | Build incident investigation and emergency dispatch | UI | High | P08.05, P07.03 | DONE | Ownership/timeline/evidence/unit/route/status workflows persist through APIs | `backend/api/{routes_incidents,routes_emergency}.py`, `src/pages/{Incidents,IncidentDetail,Dispatch,DispatchDetail,Field}Page.tsx`, migrations 0020/0021: incident queue and investigation (evidence, ranked hypotheses, timeline, notes; acknowledge/investigate/escalate/resolve and ownership through an `expected_status` guard so two people cannot silently overwrite each other; resolving needs a written note); emergency dispatch (take a call, assign a unit with route alternatives, ETA and uncertainty, choose one, follow the call to cleared, cancel with a reason) and a read-only field view. Every write is attributed to the token's person and audited to the append-only `operator_audit` (UPDATE/DELETE/TRUNCATE refused by trigger). Demo world for the tests: `backend/demo/{world,feeder,fixtures}.py`, a separate database fed by recorded simulator streams re-timed to the wall clock through the platform's own ingestion and detectors. Found and fixed real defects while building: `assignments.capability` was stored as text, not the array the contract says (0021); a unit with no fresh position or an unknown unit is now refused. 44 real API checks + 18 real-Chrome tests. `docs/evidence/p08_07_operator_actions_api.json`, `p08_07_ui_incident_dispatch.json` | 2026-09-21 |
| P08.08 | Build recommendation, approval, command and outcome views | UI | High | P08.07, P07.09 | DONE | Denied/pending/executing/failed/rollback/verified states are distinct | `backend/api/routes_commands.py`, `backend/control/{executor_worker,verifier_worker,heartbeat}.py`, `src/pages/{Recommendations,Commands,CommandDetail,Outcomes}Page.tsx`, `src/features/actions/`: recommendations with benefit, harm, confidence and safety bounds (never shown as executed; 'take no action' cannot be requested); request from a recommendation or directly (idempotent on a client key); approval by a different person in the role the safety class needs (role and four-eyes are checked BEFORE the policy runs, because a policy denial is recorded against the command); the confirmation restates action, class, target, requester and role, reason, expected benefit and harm, constraints and expiry, defaults to Cancel, and shows the four-eyes and policy facts; execution only by `system:command-executor` (real simulator adapter in the pinned SUMO container) and the outcome by `system:outcome-verifier` (noise-derived thresholds, rollback with the undo confirmed); a policy outage leaves the command requested and labelled policy-unavailable, never approved. Denied/pending/executing/failed/rolled-back/expired/verified are distinct. Seeded histories flag real vs recorded in the data; the verifier's outcome carries 'the replayed traffic in the demo world does not react to commands'. 32 real API checks (real Keycloak, executor and verifier) + 11 real-Chrome tests. `docs/evidence/p08_08_command_workflow_api.json`, `p08_08_ui_actions.json` | 2026-09-21 |
| P08.09 | Build audit, observability, shift handover and demo controls | UI | Normal | P08.08 | DONE | Role-limited real data works; demo controls remain visibly separate | `backend/api/routes_govern.py`, `backend/scenario_control/{app,serve}.py`, migrations 0022/0023, `src/pages/{Audit,Operations,Handover,Demo}Page.tsx`: the audit trail (one newest-first read-only trail over `operator_audit` and every incident/command/call/assignment history with who made each change; filters by actor, entity, result and time; cursor pages proven gap-free; refusals recorded; no endpoint can change it); platform status from probes made at request time (database, identity provider, brokers by TCP connect - stated as open port, not message flow - worker heartbeats, data freshness per source with the same stale-after budgets as the map, ingestion lag and rejections measured from what arrived; a check with no answer is unknown, never healthy); shift handover (open items resolved by the server from live records, acknowledged once by someone other than the author, four simultaneous acknowledgements yield exactly one); demo controls as a separate service with its own identity (`demo_operator`, no operational authority), banner, audit trail and API - the P05.09 `X-Demo-Role` header trust is REPLACED by a verified Keycloak token. Feeder, executor and verifier now reconnect when the database restarts (found when Postgres crash-recovered mid-run). 50 + 23 real API checks + 14 real-Chrome tests. `docs/evidence/p08_09_govern_api.json`, `p08_09_demo_controls.json`, `p08_09_ui_govern.json` | 2026-09-21 |
| P08.10 | Run design, accessibility and real-browser acceptance | UX/QA | High | P08.04, P08.05, P08.06, P08.07, P08.08, P08.09 | DONE | Keyboard/contrast/zoom/reduced-motion/responsive/failure workflows pass | `e2e/{acceptance,failures,latency}.spec.ts`, `src/config/writes.test.ts`; 19/19 real-Chrome tests on the real stack. UX-01: all screens of all 7 roles (87 role-screen visits) axe WCAG 2.2 AA clean, keyboard from the skip link with a visible focus indicator on every stop and no trap, reduced motion (0 animations over 50 ms on 15 screens; the probe does see motion without the preference). UX-04: 85 checks over 1366x768, 1920x1080, 390x844, 200% and 400% zoom, no sideways scroll, no clipped control; it found and fixed a real defect (sticky header and fixed bottom bar covered the whole viewport at 400% zoom) and unwrapped status chips in tables, kept filters mounted while a list reloads (focus was being lost), and added a focus ring for date fields. Failure workflows: 6 (API unreachable: rows stay, header says values may be older, offline after three failures, recovers on its own; a screen that cannot load retries; 30 minutes without data turns every Fresh badge Stale; a token that cannot be renewed because the session ended leads to session-ended with the destination kept; an unreachable identity provider is now told apart from an ended session; a page opened while the provider is down shows the sign-in screen with the reason instead of the browser's error page). LAT-05 P95 0.99 s and LOAD-04 (10 sessions, P95 1.02 s, +33 ms) both met on the fifth attempt (two attempts hit Postgres crash recovery - a backend aborted twice on a glibc malloc assertion - and two a stalling host disk; screen-data requests were about twice as slow under ten sessions, 333 to 687 ms P95) - see `docs/requirements/ACCEPTANCE_TARGETS.md` for method and limits. UX-02/UX-03 are met on the tested scope (the write inventory is a test). No manual screen-reader pass. `docs/evidence/p08_10_ui_acceptance.json` | 2026-09-21 |

## Phase 09 - Security, privacy and compliance

Exit gate: identity, policy, transport, runtime and privacy controls are enforced
and tested; risk/control gaps are explicit.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P09.01 | Complete threat model and security architecture | SEC | High | P02.09 | DONE | Assets/trust/abuse/safety threats and treatments cover every platform boundary | `docs/security/{THREAT_MODEL,RISK_REGISTER,SECURITY_ARCHITECTURE}.md`, machine-readable `source-code/security/{threat_model,controls}.json` and `verify_threat_model.py`. 18 boundaries (every trust zone TZ0-TZ8 of the P02.09 diagram has threats), 14 assets, 76 threats in STRIDE plus safety/AI/supply-chain categories across the 13 required areas (device, edge, broker, stream, API, web, identity, policy, controller adapter, model, operator, supply chain, external integration - each at least 2), safety and security impact scored separately, 46 controls (24 implemented, 9 partial, 13 planned). Residual risk is COMPUTED, not asserted: a control lowers likelihood only when implemented AND its named evidence passes (a file must exist and contain the named check, a UI test title must exist, a cited pytest file must pass - 8 files, 152 tests), partial or planned earns no credit, so a gap never looks like protection. A planned or partial control must name a registered task, the gap and a due date. Result before any P09.03+ control lands: 8 high / 47 medium / 21 low, and the 8 high are listed with owners (database credentials in environment T-20, sensitive audit detail T-21, cross-agency reads T-27, dependency and CI-secret risk T-57/T-59, lateral movement T-71, cluster secrets T-72, telemetry contents T-75); two threats are accepted in writing with rationale (WebSocket token in the URL T-31, simulated integrations T-64/T-65). 6 controls are evidenced by named pytest files. 12 real checks + 30 unit tests; generated tables in the docs are proven current. Not a certification claim. `docs/evidence/p09_01_threat_model.json` | 2026-09-21 |
| P09.02 | Provision Keycloak realm, clients, roles and demo identities | SEC | High | P02.08, P05.01 | DONE | OIDC validation, PKCE and role claims work without committed secrets | PULLED FORWARD from Phase 09 to unblock P08.04 (the UI cannot be built or tested without real sign-in). `source-code/infra/platform/keycloak/{realm/aiops-realm.json,provision.py,verify_keycloak.py,README.md}`: Keycloak 26.0.8 pinned by digest, dev mode on host port 8180; the realm file holds no secret (12 roles, public `aiops-ui` client with PKCE S256 only and exact redirect URIs, `aiops-api` audience mapper, `fullScope` off, 5 confidential service clients, brute-force protection, refresh-token rotation); `provision.py` creates 9 demo people with random passwords into the git-ignored `output/demo_identities.json` (idempotent, recoverable if the file is lost). 33 real checks: the LIVE realm read back through the admin API and compared with the written requirements (not with the file), then the flow attacked - no/plain PKCE, implicit and password grants, an unregistered redirect, a wrong verifier, a replayed code, a replayed refresh token, account guessing - every attack refused. Not done here (P09.04): TLS at the provider, production mode, secret rotation, workload identity. `docs/evidence/p09_02_keycloak.json` | 2026-09-21 |
| P09.03 | Enforce OPA authorization at API and worker | SEC | High | P02.07, P07.05 | DONE | Default denial, policy outage and cross-role/target negative tests pass | Open Policy Agent 1.20.2 as the `aiops-opa` container (image pinned by digest, read-only, no capabilities, loopback only) is the decision point for the operator API, the scenario-control API and the command executor. `source-code/policy/`: `aiops/api/authz.rego` (may these roles call this route), `aiops/command/command.rego` (who may request/review a command of this class; whether a command may be approved or executed - fixed rule order, first failure decides, safety class derived inside the policy, malformed input is the lowest rule and denies), `aiops/model/data.json` GENERATED from `inventory.json` and `backend/roles.py` by `build_data.py` (no role, route or adapter is written in Rego; a stale file fails a test; every decision names the policy version). `backend/pdp.py` is the only client and fails closed: no answer, HTTP error, HTML, an undefined result or a loosely typed permit is `PolicyUnavailable`, never a permit. API: 503 `policy_unavailable` (public health stays up, invalid tokens still 401); approval: command stays requested and labelled policy-unavailable; the executor asks again before driving the adapter (`execution_gate`: recorded approver must be a different person in an approving role, not expired, target real, evidence fresh, caller is the executor) - a refusal moves the command to `denied`, an outage holds it `approved` (asked again after 15 s, expires by its own limit). Every command decision, including an outage, is appended to `policy_decisions` (migration 0024, append-only) with input, policy version and the engine's decision id. CTL-33 in the same task: `backend/api/hardening.py` - security headers on every response, no CORS, no docs/schema served, 64 KiB body limit (also streamed), per-person read/write buckets and a per-address failure allowance that never locks out signed-in people. Evidence: `opa check --strict` clean; 48 Rego unit tests (P09.05 added override authority and its own tests; found and fixed a test written before override existed that used `"kind": "override"` as its example of a malformed kind - once override became real, that example stopped being malformed); a DIFFERENTIAL test of the engine against the Python reference over 1,938 endpoint x role-set cases, 2,212 request/review authority cases and 6,000 generated command contexts (decision, code and message identical; every outcome kind reached: 875 approved, 1,016 expired, 437 invalid target, 177 stale, 3,495 policy denied); real-token negatives (cross-role request and review, six targets aimed outside what an adapter controls, unlisted route denied by the engine even when the enforcement point's table would have allowed it); seven ways an approval can be wrong caught at the executor's gate; engine stopped (every role 503, scenario-control 503, approval waits, executor holds, recovery) and answering nonsense (5 modes, all 503); hardening (headers, 413, 429, no lock-out). Decision latency p50 1.5 ms, p95 2.8 ms. Also fixed: an approval after a policy outage kept showing the outage as the command's error; the executor's `denied` transition (approved -> denied) is new. Found on the way: on this Windows host a POST to the engine takes 44 ms (Windows-to-WSL relay; 1.5 ms inside WSL) so queries use GET with `?input=`. 79 real checks + 38 unit tests; regression: verify_auth, verify_command_workflow (real SUMO execution through the gate), verify_commands, verify_preemption, verify_operator_actions, verify_govern, verify_scenario_control all pass. Not done here: agency scoping of users (T-27, CTL-47, accepted for this single-organisation prototype), override (P09.05). `docs/evidence/p09_03_policy.json`, `source-code/policy/README.md` | 2026-09-21 |
| P09.04 | Protect service traffic, workloads and secrets | SEC/DEVOPS | High | P05.02, P09.02 | TODO | TLS/workload identity/RBAC/network policy/secrets/rotation evidence exists | - | 2026-09-18 |
| P09.05 | Protect audit and sensitive data | SEC/GRC | High | P05.10 | DONE | Recursive redaction, append protection and role-limited evidence access pass | Tamper-evidence beyond the trigger (CTL-15): migration 0026 adds a hash chain (`prev_hash`/`row_hash`) to eight histories - the four audit logs nothing ever deletes from (`operator_audit`, `scenario_control_audit`, `policy_decisions`, `retention_runs`) stay fully append-only (UPDATE/DELETE/TRUNCATE refused); command/incident/emergency-call/emergency-assignment transitions (unprotected since 0008/0016) gain UPDATE/TRUNCATE refusal but deliberately not DELETE, since `database/retention.py` legitimately cascades through them - `retention_runs` records every purge so a gap in those four is accounted for, not indistinguishable from tampering. `source-code/backend/audit_chain.py` independently verifies the chain server-side (recomputing in Python was tried and was wrong - `row_to_json` mixes compact top-level formatting with JSONB's own spaced formatting for a nested jsonb column, and reproducing that client-side is fragile; the fix recomputes via SQL instead). Recursive redaction (CTL-16): `backend/redaction.py` removes secrets by key and by shape (JWT, bearer, PEM key, `password=`, a URL parameter) and masks email addresses, recursively through dicts/lists, applied before `workflow.audit`, `policy.record_decision` and scenario-control's audit write, plus a logging filter for uvicorn's own access log (a WebSocket's token lives in the query string); migration 0026 also adds a database-level CHECK refusing a JWT or PEM key in the three free-form detail columns, as a backstop for a missed call site. Role-limited export (CTL-17): `GET /api/v1/audit/export` (auditor only, capability `audit.export`), required and bounded `since`/`until` (92 days), additionally redacts locations the live trail still shows, capped at 5000 rows and says so, itself an audited, attributed action. Override (CTL-34): `POST /api/v1/commands/{id}/override` (capability `commands.override`, `incident_commander` only in the inventory; the policy engine's `OVERRIDE_ROLES` independently agrees only SC-2 has any override role and it is the same one), needs a 10-1000 char justification, moves an `executed` command to `rolled_back` (migration 0027 lets `policy_decisions.point` record `command_override`), refuses SC-0/SC-1 (422 `override_not_available`, distinct from a role problem), a second override, and a command never executed; honest that this prototype's one-shot adapter holds no session to drive a further physical reversal - the audit entry says so rather than claiming an undo that did not happen. Found and fixed along the way: two pre-existing test-suite bugs unrelated to this task (P07.10's sign test treated `p=0.0` as falsy; P07.06/P07.09's `fresh_signal_evidence` fixture never registered its own device, so `ingest_one` silently rejected it - both now fixed, evidence rerun clean). 31 real checks against the real stack (tamper-simulation via a disabled trigger, a real `retention.py` purge, real Keycloak tokens, a real command through request/approve/execute). `docs/evidence/p09_05_audit_protection.json` | 2026-09-22 |
| P09.06 | Complete data inventory, retention and DPIA-style review | GRC | High | P03.03, P04.06 | DONE | Purpose/minimization/access/retention/rights/risks/residual owners documented | `source-code/security/data_inventory.json` + `verify_data_inventory.py`, `docs/security/DATA_INVENTORY_AND_DPIA.md`: 7 data categories, each with purpose, minimization, access capabilities (checked against the real UX inventory), the data subject, a rights process and a residual risk - CHECKED against reality, not asserted: every `retention_class` is a real key of `database/retention.py`'s own windows with a matching day count (short 7 / standard 90 / extended 365 / audit never-purged), every `privacy_classification` and `device_type` is a real value of the `device/v1` contract enum, every device type is covered by exactly one category, `emergency-call/v1` and `emergency-unit-assignment/v1` are confirmed to carry no caller/crew identifier field by reading the schema (not by claim), and every distinct (device_type, privacy, retention) combination actually present in the live `aiops` and `aiops_demo` databases is covered. DPIA summary: no special-category data anywhere; the one path that could produce identifiable output (edge camera imagery) is structurally prevented before it reaches the platform (P04.06's privacy zones/ephemeral id/k-anonymity floor - 0 edge_camera devices are in fact registered in either live database or the simulated catalog today, noted honestly); the personal data actually held is staff identity at username granularity (audit-class, never purged - an accepted trade-off, not an oversight) plus an override's free-text justification. CTL-39 (device lifecycle) updated to `partial`: registration/identity/ACL/lifecycle-status are real and evidenced, but certificate revocation has no CRL/OCSP-equivalent at the broker - an honest, registered gap pointing at P11.02. 9 real checks + 14 unit tests. `docs/evidence/p09_06_data_inventory.json` | 2026-09-22 |
| P09.07 | Map ISO/NIST/ETSI/IEC/OWASP/AI controls | GRC | Normal | P09.01, P09.02, P09.03, P09.04, P09.05, P09.06 | TODO | Evidence/gaps/control owners mapped without certification or legal claims | - | 2026-09-18 |
| P09.08 | Implement DevSecOps supply-chain controls | DEVOPS/SEC | High | P01.05 | DONE | Scans, SBOM, provenance, signatures and admission checks gate release artifacts | `source-code/security/supply_chain/{build_release.py,admission-policy.yaml,README.md}`; real WSL/Linux Docker build of the edge image, syft SBOMs (107 image components, 35 frontend Node components), Trivy vulnerability scan (44 HIGH findings, all Debian OS packages, all `FixedVersion: none` - gate is on fixable findings, 0 fixable, documented not hidden), bandit SAST clean (0 findings/2932 lines), a cosign-signed SLSA-style provenance attestation binding the image digest to a recomputed content hash of its real build context (git HEAD `f7e1950` is dirty/95 uncommitted paths, so provenance uses the content hash, not commit state, as source identity) plus both signed SBOMs, and a real admission gate proven twice with the identical signature file: genuine artifact admitted, one-byte-tampered copy rejected; `admission-policy.yaml` (Kyverno) carries the same check to P11.02's cluster, not yet applied (no cluster exists). CI `.github/workflows/check.yml` gained a `supply-chain` job running the same script (hosted run pending, same as P01.05/CTL-28). `source-code/requirements-lock.txt`/`requirements-dev.txt` gained `bandit==1.9.4` + transitive deps. CTL-27 extended to the edge image, CTL-29 moved planned to implemented (`source-code/security/controls.json`); `verify_threat_model.py --render` recomputed residual risk: high 8 to 5. `docs/evidence/p09_08_supply_chain.json` | 2026-09-23 |
| P09.09 | Configure and verify Falco/runtime detections | SEC/DEVOPS | High | P01.07 | TODO | Scoped malicious/unauthorized behaviors generate actionable target alerts | - | 2026-09-18 |
| P09.10 | Run role, transport, policy and abuse-case acceptance | QA/SEC | High | P09.02, P09.03, P09.04, P09.05, P09.06, P09.07, P09.08, P09.09 | TODO | Positive/negative runtime evidence covers controls; findings owned and retested | - | 2026-09-18 |

## Phase 10 - Observability and AIOps

Exit gate: real telemetry detects operational degradation, creates one scoped
incident, performs bounded remediation and verifies sustained recovery.

| ID | Task | Owner | Pri | Dependencies | Status | Acceptance | Evidence | Updated |
|---|---|---|---|---|---|---|---|---|
| P10.01 | Instrument services/devices/models with OpenTelemetry | OPS | High | P04.05, P05.07, P07.09 | DONE | Correlated traces/metrics/logs cover edge-to-action path with bounded labels | `source-code/backend/observability.py` (real `opentelemetry-sdk`, per-process provider instances so `configure()` is safe to call more than once per process - a real gap found via a failing test, since OTel's global registry is set-once-only), `source-code/edge/observability.py` (dependency-free, OTel-shaped spans - ADR-0006's resource budget keeps the edge container's own closure minimal), `HTTPTracingMiddleware` wired into both FastAPI apps (`backend/api/app.py`, `backend/scenario_control/app.py`); a second real bug found by a failing test: this Starlette version (1.6.0) never sets `scope["route"]`, so `http_route` is instead resolved by matching `scope["endpoint"]` back against the app's own routes. Spans wired into the edge inference decision (`edge/runtime.py`), gateway receive/publish, ingestion, network-state aggregation, congestion detection, incident correlation (cycle + per-incident), and the full command path (recommend/request/review/verify_and_rollback) - correlated by the real domain id already on each message (event_id/incident_id/command_id), not by injecting new fields into the versioned wire contracts. Metric labels bounded by loading `contracts/metric-label-set/v1`'s own key enum (no re-typed enum to drift). `source-code/tests/test_observability.py` (10 tests: context propagation, error status, bounded label rejection, a 200-request hostile-path-scan proving `http_route` collapses to one "unmatched" series). `source-code/backend/verify_observability.py` against the real running stack: a real event's `event_id` is the same `correlation_id` on the real gateway's MQTT-callback span and the real Postgres-writing ingestion span; a real incident's id carries onto the recommendation span; a real command's id carries onto request/review/outcome spans (7/7 checks). Also fixed a real, pre-existing, unrelated defect found while verifying in a clean venv: root `requirements-lock.txt` (used by the CI `python` job) was missing most of the dependency closure the 615-test suite actually needs (fastapi/psycopg/numpy/scikit-learn/... - the hosted run would have failed on its very first attempt); rebuilt from a clean-venv-verified freeze, added `bandit==1.9.4` for P09.08's SAST step in the process. CI `check.yml` unchanged by this task (still pending its first hosted run, same as P01.05). `docs/evidence/p10_01_observability.json` | 2026-09-23 |
| P10.02 | Define SLOs and platform dependency topology | OPS | High | P10.01 | DONE | Availability/latency/error/saturation/freshness/ack/ETA objectives are measurable | `source-code/backend/{slos.json,topology.json,verify_slos.py}`, `docs/observability/SLOS_AND_TOPOLOGY.md`: 10 real `contracts/slo-definition/v1` records (all 7 objective types) cross-checked against real sources, not hand-maintained - every metric-backed SLO's referenced metric name is grepped from the real P10.01-instrumented file that emits it, every `related_acceptance_target_id` is a real row in `docs/requirements/ACCEPTANCE_TARGETS.md`, every topology edge's dependency is grepped from the real connecting source (found and corrected one wrong edge during this: `edge-runtime -> mosquitto` does not exist in this codebase - the edge outbox has no drain-to-MQTT wired yet, a real gap now stated in the topology instead of asserted away). LAT-02 (edge-to-central ingest P95 < 2 s) measured for real against the running stack rather than left a target: 27.8 ms (n=20) - `docs/requirements/ACCEPTANCE_TARGETS.md` updated from "Not yet measured". Two SLOs with no live metric yet (command acknowledgement, ETA accuracy) point their `measurement_query_ref` at the real evidence files that already measured them directly (P07.09/P07.10) rather than inventing a metric ahead of P10.03's collector. `docs/evidence/p10_02_slos.json` | 2026-09-23 |
| P10.03 | Provision Prometheus, Grafana, Tempo and logs | OPS/DEVOPS | High | P10.01 | DONE | Datasources/dashboards/traces/log links run with retention/resource bounds | `source-code/infra/platform/observability/` (prometheus/tempo/loki/grafana config, `README.md`), `docker-compose.yml` extended with 4 digest-pinned containers, `verify_observability_stack.py`. Resource limits confirmed via `docker inspect` to match `docs/environment/RESOURCE_BUDGET.md`'s line exactly: 0.75 CPU / 1,536 MiB total. Grafana's 3 datasources (Prometheus/Tempo/Loki) provisioned and read back from the real API, plus trace<->log correlation config. A real metric and trace, pushed via OTLP through `backend/observability.py` exactly as a real service would, both queried back out; a real log line pushed to Loki carries the same trace_id and is queried back out, proving the correlation has real joinable data on both sides. Every panel in the provisioned `platform-overview` dashboard executes against the real Prometheus - metric names confirmed empirically (OTLP-to-Prometheus naming: `_total` for counters, `_<unit>_bucket/_count/_sum` for histograms), not assumed from docs. Found and fixed along the way: `backend/observability.configure()` only checked the generic `OTEL_EXPORTER_OTLP_ENDPOINT` var, so setting the correct per-signal trace/metric endpoints (traces to Tempo, metrics to Prometheus - two different real services) silently did nothing; the 60 s default metrics export interval would have made short-lived verify scripts exit before their first export - both fixed, plus a `flush()` for callers that need certainty. Also found: this project's real `infra/platform/.env` had CRLF line endings, silently corrupting any password sourced under WSL bash (`GRAFANA_ADMIN_PASSWORD` failed Grafana auth with a trailing `\r`) - converted to LF. Honest scope boundary stated, not hidden: no backend/edge service has a long-running container yet (P11.01), so nothing ships real logs into Loki here - the push/query mechanism and correlation are proven with real pushed data instead. `docs/evidence/p10_03_observability_stack.json` | 2026-09-23 |
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

- 2026-09-23 - OPS/DEVOPS (Claude) - P10.03 TODO to DONE. Prometheus,
  Tempo, Loki and Grafana added to the platform stack, digest-pinned,
  resource limits matching `docs/environment/RESOURCE_BUDGET.md`'s line
  exactly (0.75 CPU / 1,536 MiB total, confirmed via `docker inspect`, not
  just written in compose). Grafana provisioned (datasources + one real
  dashboard + trace<->log correlation), not clicked through.
  `verify_observability_stack.py` proves a real round trip: a metric and a
  trace pushed via OTLP through the real `backend/observability.py` are
  both queried back out of Prometheus/Tempo; a real log line pushed to
  Loki carries the same trace_id and is queried back out; every dashboard
  panel executes against the real Prometheus with metric names confirmed
  empirically, not assumed (OTLP-to-Prometheus naming: `_total` for
  counters, `_<unit>_bucket/_count/_sum` for histograms).
  Two real bugs in P10.01's `backend/observability.py` found and fixed
  while proving this against the real stack, not before: `configure()`
  only checked the generic `OTEL_EXPORTER_OTLP_ENDPOINT` var, so the
  correct per-signal setup (traces to Tempo, metrics to Prometheus - two
  different real services) silently exported nothing; the metrics
  reader's 60 s default interval would let a short verify script exit
  before its first export ever fired. Added `observability.flush()` and a
  2 s interval once real OTLP is configured. Also found: the real
  `source-code/infra/platform/.env` had CRLF line endings from earlier
  Windows-side edits, silently corrupting `GRAFANA_ADMIN_PASSWORD` with a
  trailing `\r` the first time it was sourced under real WSL bash (every
  prior verify script that needed `.env` had been run from the Windows
  venv instead, where this never surfaced) - converted to LF.
  Honest, stated scope boundary: no backend/edge service has a
  long-running container yet (P11.01), so nothing ships real service logs
  into Loki here - the push/query mechanism and Grafana's correlation
  config are proven with real pushed data instead, not with a real
  service's own output. `source-code/infra/platform/observability/`
  (config + `README.md`), `docs/evidence/p10_03_observability_stack.json`.
  Full `check.py` clean (625 pytest, ruff, management validation).
- 2026-09-23 - OPS (Claude) - P10.02 TODO to DONE. 10 real SLOs
  (`source-code/backend/slos.json`, all 7 `contracts/slo-definition/v1`
  objective types) and the real 5-process dependency topology
  (`source-code/backend/topology.json`), both cross-checked against real
  sources by `verify_slos.py` rather than hand-maintained: every
  metric-backed SLO's metric name is grepped from the P10.01-instrumented
  file that actually emits it, every topology edge's dependency is grepped
  from the real connecting source. Caught and corrected one wrong edge
  while building this: the topology draft assumed `edge-runtime ->
  mosquitto`, but nothing in `source-code/edge/` opens an MQTT connection -
  the durable outbox (P04.07) has no drain-to-MQTT wired in this
  repository yet, now stated as a real gap in `topology.json` instead of
  silently asserted. LAT-02 (edge-to-central ingest P95 < 2 s) measured
  directly against the real running stack rather than left as a target:
  27.8 ms (n=20) - `docs/requirements/ACCEPTANCE_TARGETS.md`'s LAT-02 row
  updated from "Not yet measured". Two SLOs with no live metric yet
  (command acknowledgement, emergency ETA accuracy) point at the real
  evidence files that already measured them directly (P07.09/P07.10)
  rather than inventing a metric ahead of P10.03's collector.
  `docs/observability/SLOS_AND_TOPOLOGY.md`, `docs/evidence/p10_02_slos.json`.
  Also added `ingestion_ingest_latency` (a real histogram, LAT-02) and
  `network_state_records` (by `freshness_status`, SAFE-03) to P10.01's
  instrumentation while building the SLOs that reference them. Full
  `check.py` clean (625 pytest, ruff, management validation).
- 2026-09-23 - OPS (Claude) - P10.01 TODO to DONE. Real OpenTelemetry
  instrumentation across the edge-to-action path. Two real bugs found and
  fixed by the new tests before this could be called done, not after:
  (1) `trace.set_tracer_provider`/`metrics.set_meter_provider` are
  set-once-per-process in real OTel - `backend/observability.configure()`
  now keeps its own provider instances in `_state` rather than depending
  on the global registry, so more than one service can be configured in
  one process (every real deployed process only ever calls it once; only
  a multi-service test process hit this); (2) Starlette 1.6.0 does not
  populate `scope["route"]` the way older OTel FastAPI instrumentation
  assumes - `http_route` was silently "unmatched" for every real request
  until `_matched_route_template()` was added to resolve it from
  `scope["endpoint"]` against the app's own registered routes. Also found
  and fixed, unrelated to this task: root `requirements-lock.txt` (what
  the CI `python` job installs) was missing most of what the now-615-test
  suite actually imports (fastapi, psycopg, numpy, scikit-learn, onnx,
  confluent-kafka, ...) - confirmed by installing it alone into a clean
  venv, which failed to even collect 26 test files; the hosted run P01.05
  is still waiting on would have failed immediately. Rebuilt from a
  clean-venv-verified freeze (removed unrelated SSH/pickling packages that
  had accumulated in the dev venv from earlier ad hoc work and were never
  imported by anything in `source-code/`).
  - `source-code/backend/observability.py`: real `opentelemetry-sdk`
    tracer/meter per configured service, an in-memory span exporter by
    default (OTLP only if `OTEL_EXPORTER_OTLP_ENDPOINT` is set - P10.03
    supplies a real collector later), `BoundedCounter`/`BoundedHistogram`
    whose label keys are validated against `contracts/metric-label-set/
    v1`'s own enum (loaded from the file, not re-typed), `HTTPTracingMiddleware`
    for both FastAPI apps.
  - `source-code/edge/observability.py`: dependency-free, OTel-shaped spans
    (trace_id/span_id/parent_span_id/status/attributes) - ADR-0006's
    resource budget is why the edge container does not carry the real SDK.
  - Correlation is by domain id (event_id/incident_id/command_id), not by
    injecting new fields into versioned wire contracts (would break every
    existing consumer's `additionalProperties: false` schema check) - the
    observation-envelope's own existing `correlation_id` field already
    anticipated exactly this.
  - Wired into: `edge/runtime.py` (`_evaluate_device`), `backend/gateway/
    gateway.py` (receive + publish), `backend/ingestion/ingest.py`,
    `backend/state/network_state.py`, `backend/analytics/
    congestion_service.py` (`detect_and_store`), `backend/analytics/
    incident_service.py` (`sync`, cycle + per-incident spans), `backend/
    control/{recommendation_service,command_service,outcome_verification}.py`.
  - `source-code/tests/test_observability.py` (10 tests, no infra needed);
    `source-code/backend/verify_observability.py` against the real running
    stack (7 checks, all pass): a real event's `event_id` is the same
    `correlation_id` on the real gateway MQTT-callback span and the real
    Postgres-writing ingestion span; a real incident's id carries onto its
    recommendation span; a real command's id carries onto request/review/
    outcome spans. `docs/evidence/p10_01_observability.json`.
  - `requirements-dev.txt`/`requirements-lock.txt` gained `bandit==1.9.4`
    (P09.08) and the OpenTelemetry packages; `backend/api/`, `backend/
    gateway/`, `backend/scenario_control/` per-component locks updated to
    match.
  - Full `check.py` clean (625 pytest, ruff, management validation).
- 2026-09-23 - DEVOPS (Claude) - P09.08 TODO to DONE. Real WSL/Linux Docker
  build of the edge release image gated end to end: syft SBOMs (image +
  frontend), a Trivy scan gated on fixable CRITICAL/HIGH only (44 HIGH
  findings are all upstream-NOFIX Debian OS packages - reported, not
  hidden, not faked clean), bandit SAST (0 findings), a cosign-signed
  SLSA-style provenance attestation whose source identity is a recomputed
  content hash of the real build context (git HEAD is dirty with 95
  uncommitted paths, so commit SHA alone would be misleading), and a real
  admission gate proven twice against the same signature file: the genuine
  signed provenance is admitted, a one-byte-tampered copy is rejected.
  `admission-policy.yaml` carries the identical cosign check into Kyverno
  for P11.02's future cluster - written now, not yet applied anywhere.
  `source-code/security/supply_chain/{build_release.py,
  admission-policy.yaml,README.md}`; CI gained a `supply-chain` job (hosted
  run pending, same gap as P01.05); `requirements-lock.txt`/
  `requirements-dev.txt` gained `bandit==1.9.4`. `source-code/security/
  controls.json`: CTL-27 extended to the edge image, CTL-29 planned to
  implemented; `verify_threat_model.py --render` recomputed residual risk
  (high 8 to 5) and regenerated `docs/security/{THREAT_MODEL,
  RISK_REGISTER}.md`. `docs/evidence/p09_08_supply_chain.json`. Full
  `check.py` clean (615 pytest, ruff, management validation).
  **Process note:** repository still has only 5 git commits (through
  P01.06); everything from P02 onward, including this task's own files, is
  uncommitted in the working tree - flagged to the user, not something a
  task-level session should silently commit given the earlier standing
  instruction to hold the push until explicitly authorized.
- 2026-09-22 - SEC (Claude) - P09.01, P09.03, P09.05 TODO to DONE.
  **Phase 09 in progress**: threat model, OPA policy enforcement and
  audit/sensitive-data protection are done; P09.02/P09.04/P09.06-P09.10
  remain (P09.04 partly needs a real target host; P09.09 fully does).
  - P09.01: `source-code/security/{controls.json,threat_model.json,
    verify_threat_model.py}` - 76 threats over 18 trust-zone boundaries, 47
    controls, residual risk COMPUTED (a planned/partial control earns no
    credit) not asserted. `docs/security/SECURITY_ARCHITECTURE.md` narrates
    the layers; `THREAT_MODEL.md`/`RISK_REGISTER.md` are generated tables.
  - P09.03: Open Policy Agent (pinned by digest, read-only, no capabilities,
    loopback 8181) becomes the policy decision point for the API, the
    scenario-control API and the command executor, replacing the in-process
    capability check as the thing that actually decides. `source-code/
    policy/`: Rego for API authorization and command authority/decision,
    `build_data.py` generates the engine's data from the UX inventory and
    `backend/roles.py` (no role/route/adapter is written in Rego), a
    differential test against the Python reference over ~10,000 generated
    cases. `backend/pdp.py` fails closed on any malformed or missing
    answer; the executor re-asks before driving an adapter. 79 real checks;
    `docs/evidence/p09_03_policy.json`, `source-code/policy/README.md`.
  - P09.05: a hash chain (migration `0026_audit_hash_chain.sql`) over eight
    histories catches a rewrite that defeats the append-only trigger;
    `backend/audit_chain.py` verifies it server-side via SQL (a first,
    Python-side recompute was tried and was wrong - `row_to_json` mixes
    compact and JSONB-spaced formatting depending on column type - so
    verification asks the same engine that computed the hash, not a
    reimplementation of its formatting rules). Recursive redaction
    (`backend/redaction.py`) wired into every write path plus a DB-level
    CHECK backstop and a log filter. `GET /api/v1/audit/export` (auditor
    only, bounded, capped, attributed) and `POST /api/v1/commands/{id}/
    override` (incident_commander, SC-2 only, honest about no physical
    undo) are new. 31 real checks; `docs/evidence/p09_05_audit_protection
    .json`.
  - Two pre-existing bugs found and fixed along the way, unrelated to this
    task: P07.10's benefit significance check treated a sign-test p-value
    of exactly 0.0 as falsy (`or 1.0` fallback), silently passing on a
    coincidence; P07.06/P07.09's `fresh_signal_evidence` fixture never
    registered the device it fed evidence for, so real ingestion silently
    rejected the event (`REJECTED_UNKNOWN_DEVICE`) and every later
    freshness check failed - not caught before because the device
    happened to already exist in the database from earlier manual runs.
  - Process note for future sessions: a verify script that opens a
    critical incident or drives a command to `executed` as a fixture and
    does not clean it up afterward silently breaks OTHER LATER verify
    scripts that share the same `aiops` database (an open critical
    incident escalates any nearby action to SC-2; a lingering `executed`
    command with no outcome is picked up by `verifier_worker.
    verify_ready`'s system-wide, unscoped scan). This is not new today -
    dozens of `executed`/no-outcome rows from earlier P07.06/P07.09 runs
    this session (timestamped hours before this task) were already sitting
    in `aiops` and hijacked `verify_command_workflow.py`'s own outcome
    check the first time the full regression suite ran back to back; fixed
    two ways: `verify_audit_protection.py` now deletes its own fixtures
    (the incident and every command it created) in a `finally` block, and
    `verify_command_workflow.py` now sweeps away any pre-existing
    executed/no-outcome command before building its own fixtures, so a
    stray row from a completely different script cannot hijack its check.
    Neither P07.06 nor P07.09 were changed to clean up after themselves -
    that remains a latent gap in those two scripts' own fixture hygiene,
    now recorded in `Memory.md` rather than chased further today. A
    second, unrelated finding: this workstation's shell defaults to a
    DIFFERENT project's Python virtual environment unless one explicitly
    activates this project's own - explains several earlier "flaky"
    `confluent_kafka` import failures this session; also now in
    `Memory.md`.

- 2026-09-21 - UI/UX/SEC (Claude) - P08.04-P08.10 and P09.02 TODO to DONE.
  **Phase 08 exit gate met: all 10 tasks (P08.01-P08.10) DONE.** P09.02 was
  pulled forward from Phase 09: the operator UI cannot be built or tested
  without real sign-in, so Keycloak (pinned, host port 8180 because 8080 is
  taken by other projects on this host) and the API's strict token validation
  were built first, with an inventory-derived default-deny authorization
  layer shared by the API, the UI and the wireframes.
  - What now exists: a 21-screen operator UI (`source-code/frontend/`) on the
    real APIs, no mocked data anywhere; a demo world in a separate database
    fed by recorded simulator streams through the platform's own ingestion
    and detectors; real executor and verifier workers; append-only operator
    audit; a scenario-control service that verifies a real identity instead
    of trusting a header.
  - Measured (P08.10, fifth attempt; earlier ones hit two Postgres crash
    recoveries and a stalling host disk): LAT-05 P95 0.99 s; LOAD-04 with 10
    concurrent authenticated sessions P95 1.02 s (screen-data requests P95
    333 to 687 ms); UX-01..UX-04 met on the tested scope (limits recorded in
    ACCEPTANCE_TARGETS.md: no manual screen-reader pass, browser-window
    emulation of zoom, ten sessions is not the P11.06 ceiling).
  - Real defects found and fixed by the tests: unit assignment stored a text
    where the contract says an array; the seeded effective outcome was
    classified with the wrong direction and came out ineffective; a dialog
    lost focus when its content changed; filters unmounted while a list
    reloaded; sticky header and bottom bar covered the viewport at 400% zoom;
    an unreachable identity provider was reported as an ended session and a
    page opened during an outage landed on the browser's error page; the
    feeder and both workers died when Postgres crash-recovered (a backend
    aborted on a glibc malloc assertion, twice; cause not isolated, see
    Memory.md) and now reconnect.
  - Honest limits: the demo world's replayed traffic does not react to
    commands (each outcome says so); the emergency CAD/AVL stream is finite
    so its devices go stale between replays and platform status shows it;
    the scenario-control audience is the shared `aiops-api` (a separate one
    is a P09 item); override and OPA policy are not in this UI (P09.05,
    P09.03).

- 2026-09-20 - CONTROL/QA (Claude) - P07.09, P07.10 TODO to DONE. **Phase
  07 exit gate met: all 10 tasks (P07.01-P07.10) DONE.**
  - P07.09: independent outcome verification and rollback
    (`source-code/backend/control/{outcome_verification,live_session,
    verify_outcomes}.py`, `source-code/backend/repositories/outcomes.py`,
    `source-code/simulator/control_adapters/session_server.py`, API `GET
    /api/v1/outcomes[/{id}]`). A live lock-step SUMO session gives a real
    before window, the action, a real after window and a physical undo in
    one simulated world (P07.06's per-action container cannot be measured
    around or undone). Thresholds calibrated from measured no-action noise,
    never picked to pass a scenario. Real results: closing a busy edge
    raised waiting 10.0->459.3 vehicle-s = unsafe, rolled back by an
    independent verifier, lanes read back from TraCI as closed then
    reopened, waiting recovered; a signal extension is ineffective and a
    same-seed no-action control gives the identical number (before/after
    cannot separate an action from drift below the noise band - stated, not
    hidden); paired transit priority is effective; a post-action telemetry
    gap is unknown+escalated, never defaulted to effective; an
    unavailable/failed undo leaves the command executed+escalated (a
    rollback is claimed only when it verifiably happened). LAT-04 met:
    warm live-session P95 0.057s (n=24); cold per-action path P95 2.70s
    (n=5, thin margin). 31 real-stack checks + 10 unit tests; evidence
    `docs/evidence/p07_09_outcomes.json`.
  - P07.10: three end-to-end scenarios
    (`source-code/backend/control/{emergency_flow,verify_scenarios}.py`,
    `source-code/simulator/control_adapters/signal_safety.py`). Building
    this suite's independent runtime signal-safety monitor (reads raw
    signal state every simulated second, checks it against the network's
    own compiled design - conflicting greens, missing/short yellow, short
    pedestrian clearance) found the pre-emption mechanism's first version
    (P07.07/P07.08) genuinely unsafe: it drove every intersection to a
    fixed phase regardless of the vehicle's actual movement (measured
    pre-emption *slower* than doing nothing on a turning route) and
    shortened yellow/pedestrian phases along with actuated ones (passed the
    old phase-order check while cutting a real clearance interval short).
    Fixed with `phase_control.MovementPriority` (targets whichever phase
    gives the vehicle's real movement - from TraCI's own controlled-link
    table - a green; only ever shortens an actuated phase down to its own
    minDur) plus the monitor itself now gating every P07.07/P07.08/P07.10
    run; a negative control (`preempt_naive`, explicitly marked never used
    by the platform) reproduces the original defect on demand to prove the
    monitor catches it. Every dispatch runs the real platform path end to
    end: a real no-vehicle simulator snapshot published as real
    `corridor_kpis`, a live-state-aware route and ETA, a real pre-emption
    request through real policy and real execution, real P07.09 outcome
    verification, a paired same-seed no-pre-emption baseline. ETA-01 met
    (pooled MAE 8.4%, n=15, normal traffic). ETA-02 met (pooled MAE 10.55%,
    n=15, real segment-closing incident with genuine reroute). ETA-03 met
    (30 paired dispatches, mean reduction 8.13s/6.1%, sign test p=0.0001).
    SAFE-01 met (0 violations over 162,072 real intersection-state
    observations, every run including failure-limit runs). Traffic
    outcomes measured and reported honestly, not hidden. Failure limits
    each run with its measured consequence (heavy congestion, no route,
    policy outage, adapter failure, stale evidence - all fail-closed). A
    real staged cross-agency response (police->fire->EMS) and a recorded
    emergency timeline matching the simulated travel time to the second.
    18 real-stack checks; evidence `docs/evidence/p07_10_scenarios.json`.
  - Command policy (P07.05) reworked from placeholder role strings
    (`CONTROL`/`OPS`/`EMERG`) to the real, binding P02.08 role and
    safety-class model (`source-code/backend/roles.py`): REQUEST/APPROVE
    authority checked per safety class (SC-0/SC-1/SC-2), an active
    *critical* incident on a target raises any action to SC-2, four-eyes
    applies to SC-1/SC-2 only, and EXECUTE/verify-outcome are enforced as
    identity checks (`require_executor`/`require_verifier`) inside every
    adapter and outcome-verification service - refused for any human actor
    including the approver. `source-code/database/migrations/
    0019_command_roles_and_params.sql` adds `requested_by_role`/
    `approved_by_role` and a `command_params` side table (the contract
    deliberately carries no action magnitude). P07.05/P07.06/P07.07/P07.08
    verify scripts and their evidence re-run and re-copied under the new
    model; all pass identically.
  - P05.09 housekeeping: scenario-control's placeholder `SIM`/`DEVOPS` role
    names replaced with the real P02.08 `demo_operator` (DEMO authority
    only); re-verified against the real stack, evidence re-copied.
  - Full `pytest` (494 passed) and `ruff check` (clean) re-run after every
    change in this entry.
- 2026-09-19/20 - DATA/BACKEND/DEVOPS (Claude) - P05.01-P05.10 TODO to
  DONE. **Phase 05 exit gate met: all 10 tasks DONE.** New areas
  `source-code/infra/` (DEVOPS), `source-code/database/` (DATA/BACKEND),
  `source-code/backend/` (BACKEND), each with its own README; every task
  verified against the real running P05.01 stack (Mosquitto, Redpanda,
  PostgreSQL/PostGIS), not mocked. Summary per task:
  - P05.01: pinned MQTT/Kafka-compatible/PostgreSQL-PostGIS services
    (`source-code/infra/platform/`), digest-pinned, resource-limited per
    `RESOURCE_BUDGET.md`; `docker inspect`-verified limits, functional
    smoke tests, persistence proven across a real container restart.
  - P05.02: mTLS listener (8883) with per-device client-cert identity and
    `acl_file` topic ACLs; found and documented a real broker quirk (SUBACK
    is granted optimistically, enforcement is at delivery time); verified
    valid device, cross-device publish/subscribe both blocked at delivery,
    untrusted-CA cert fails the TLS handshake.
  - P05.03: MQTT-to-Kafka gateway (`source-code/backend/gateway/`) reusing
    `edge.outbox.DurableOutbox` (P04.07) as its own Kafka-outage buffer;
    fixed a real cross-thread SQLite bug during development (outbox must be
    owned by exactly one thread); verified validation/partitioning/
    backpressure (real Kafka container stop/start)/application ack/replay.
  - P05.04: 8 ordered, checksummed migrations
    (`source-code/database/migrate.py`) covering topology/devices/
    telemetry/commands/incidents/emergency; checksum-tamper detection and a
    real topology seed from the actual P03.01 SUMO network, both verified.
  - P05.05: Kafka-to-Postgres ingestion (`source-code/backend/ingestion/`)
    - schema/identity/content conflicts all rejected and audited, identical
      replay idempotent per-message and for a fresh consumer group
      replaying the whole topic.
  - P05.06: windowed network-state aggregation
    (`source-code/backend/state/`) - carry-forward on empty windows,
    worst-quality-wins, a produced record verified against the real
    `contracts/network-state/v1` schema via `jsonschema.validate`.
  - P05.07: versioned cursor-paginated device/observation/network-state
    APIs plus a device/corridor-scoped live WebSocket feed
    (`source-code/backend/api/`, FastAPI+uvicorn - no ADR fixed a backend
    framework, documented here); reconnect proven gapless and duplicate-
    free against a real running server, not TestClient.
  - P05.08: incident/command repositories with explicit state machines,
    idempotent command creation, required audit references, and - the part
    needing a real database - two threads racing a transition on the same
    row via separate connections, exactly one winning.
  - P05.09: separate scenario-control API
    (`source-code/backend/scenario_control/`) wrapping P03.07's real
    replay mechanism; fixed a real phantom-read gap in the concurrent-run
    bound (`SELECT COUNT(*) FOR UPDATE` cannot stop a new concurrent
    INSERT; switched to `pg_advisory_xact_lock`); bound proven under
    genuinely concurrent requests, not sequential calls.
  - P05.10: retention/aggregation/storage-pressure
    (`source-code/database/retention.py`) - per-class windows, `audit`
    class never purged, rollup-before-delete, active (non-terminal)
    commands/incidents preserved regardless of age, storage pressure
    proven to shorten the effective window on the same data (real
    before/after). Added `ON DELETE CASCADE` from audit
    trails/outcomes/recommendations to their parent (migration 0011) so a
    terminal-record purge retires its whole case file.
  - Total: 324 pytest passing (up from 288 at Phase 04's close), ruff
    clean across the whole `source-code/` tree.
- 2026-09-19 - DEVOPS (Claude) - P01.07 BLOCKED to DONE. User supplied the
  real Ubuntu LTS host (`192.168.11.108`, Ubuntu 24.04.4 LTS, kernel
  `7.0.0-31-generic`, KVM-virtualized, real hardware not WSL2). Installed
  `kind` v0.33.0 (checksum-verified) and created isolated cluster
  `aiops-test` - node `Ready`, does not disturb the host's pre-existing K3s
  v1.36.4+k3s1 cluster (namespaces `aiops`, `interview-platform` and others,
  not created by this session). Retried Falco 0.39.2 as previously pinned:
  `modern_ebpf` still failed silently; `kmod` now failed with a genuine,
  specific cause (`class_create()` signature change in kernel 6.4+ breaks
  Falco 0.39.2's bundled driver source). Upgrading to Falco 0.44.1's
  `modern_ebpf` driver resolved it: live syscall capture confirmed with a
  deliberate trigger (`sudo cat /etc/shadow`) that produced a matching
  Falco alert carrying the exact SSH-session process ancestry
  (`process=cat parent=sudo gparent=bash ggparent=sshd`) within 3 seconds -
  proof of real-time kernel-level capture, not replay. Full findings and
  reproduction in `docs/environment/CONTAINER_K3S_FALCO_FEASIBILITY.md`.
  **Unblocks Phase 05** (P05.01 and everything chained through it).
  Production Falco deployments for this project must pin `>=0.44.1`, not
  0.39.2. Test container removed after capture; `aiops-test` kind cluster
  left running as the requested deliverable. Touched only
  `docs/environment/CONTAINER_K3S_FALCO_FEASIBILITY.md`, `TASK_REGISTER.md`,
  `Memory.md`.
- 2026-09-19 - EDGE/QA (Claude) - P04.01-P04.09 TODO to DONE. **Phase 04
  exit gate met: all 9 tasks DONE.** Built the full edge serving path
  (`source-code/edge/`) and training/evaluation path
  (`source-code/models/`); see `source-code/edge/README.md`,
  `source-code/models/README.md`,
  `docs/evidence/PHASE_04_EDGE_AI_VERIFICATION.md` and
  `docs/evidence/EDGE_AI_LIMITATIONS.md` for full detail. Summary per task:
  - P04.01: `EdgeValidator` (schema/identity/sequence/staleness/clock/
    masking, never raises - 400-iteration fuzz test) and `FeatureBuilder`
    (past-only 4x30s windows + corridor-neighbor context, 24 features,
    `loop-window/2`; missing data counted not invented). Grounded against
    1453 real P03.03 events (0 rejections).
  - P04.02: transparent rule baseline (3 variants), fit on TRAIN, selected
    on VALIDATION under an explicit false-alarm-episode-share constraint
    (mirrors FA-01); held-out report with CIs and concrete failure cases.
  - P04.03: built `source-code/models/dataset/` first (63 real SUMO runs,
    9 seeds reusing P03.08's split, physically stalled blockage vehicles,
    labels from SUMO's own measured stop-output) since no edge-model
    dataset existed yet; 30-candidate model search on TRAIN, threshold/
    abstention policy on VALIDATION, the test split opened exactly once
    (`models/registry/test_split_ledger.json`) for the final comparison -
    model beat the baseline on row F1 (0.524 vs 0.480) and incident recall
    (29/48 vs 24/48), reported whether or not it would have won (ACC-01).
    Reproducible refit is bitwise-identical.
  - P04.04: ONNX export with ORT-vs-sklearn parity proof (max diff 6.6e-7,
    tolerance 1e-5, 0 decision mismatches away from thresholds, every row
    of every split) and a hash-pinned package (`model.onnx` +
    `io_schema.json` + `golden_vectors.json` + `artifact_manifest.json` +
    `model_card.json`); `edge/model_runtime.py` refuses 8 distinct
    load-failure modes (missing/corrupt/truncated/wrong-hash/garbage-onnx/
    schema-drift/feature-version-mismatch/golden-vector-disagreement).
  - P04.05: `EdgeRuntime` wiring validator+features+model/baseline+bounded
    Prometheus metrics+`/healthz`/`/readyz`; built `edge/Dockerfile` (non-
    root uid 10001, read-only rootfs, pinned runtime-only deps - no
    training libraries) and ran it for real under
    `--cpus=0.75 --memory=512m --cap-drop=ALL --read-only`: warm inference
    p95 0.135ms, peak RSS 75.8MB. Metric cardinality proven bounded under
    3000 hostile device ids.
  - P04.06: privacy-preserving vision/track metadata path
    (`edge/vision_privacy.py`): privacy-zone suppression, per-window
    ephemeral HMAC identity (boot-random salt, never exported, rotates
    every window), k-anonymity floor (min 3 distinct objects or the whole
    window is suppressed). Grounded against a real recorded pedestrian
    trajectory from P03.02's `sim-run-1-fcd.xml`, not just synthetic
    points.
  - P04.07: `edge/outbox.py`, a SQLite WAL durable outbox (ADR-0006):
    ordered (autoincrement FIFO), acknowledged (removed only on confirmed
    ack), idempotent enqueue (upsert by event_id), bounded (256 MiB quota,
    warn/hard thresholds, refuses rather than silently drops). Crash
    recovery proven by never calling `close()` before reopening the same
    file; proved end-to-end that a crash-before-ack resend is rejected
    downstream by the *same* `duplicate_event_id` check every other event
    source in this project already goes through - "no accepted duplicates"
    is a property of the (outbox, receiver) pair, not asserted in
    isolation.
  - P04.08: `edge/activation.py`: verify-then-commit ordering (a refused
    version never touches the durable pointer), atomic `os.replace` for
    the active-version pointer (a simulated crash mid-write leaves the old
    pointer completely intact, proven by monkeypatching `os.replace` to
    raise), and `rollback()` that re-verifies its target rather than
    trusting history blindly - a since-corrupted rollback target is
    refused and the current version keeps serving. Composes with
    `EdgeRuntime.swap_model` for a live, gap-free hot-swap.
  - P04.09: `source-code/models/evaluation/{prepare_benchmark_run,
    run_container_benchmark.sh,build_report}.py` replay 12,960 real
    test-split events through the real bounded container and combine that
    with the P04.03 model card's already-sealed held-out accuracy (cited,
    never recomputed, per the test-split ledger discipline) into one
    report with conditions/sample sizes/limitations named explicitly
    (`docs/evidence/edge_benchmark_report.json`). LAT-01 (warm p95 <
    100ms) met with >700x margin; FA-01 (<=10% false alarm episodes)
    narrowly missed by both detectors on the small held-out sample (model
    12.1%, baseline 10.7%) - reported as measured, not adjusted.

  Full `check.py`: ruff clean, 288 pytest passed, management validation
  passed. `source-code/edge/requirements-lock.txt` is a separate,
  runtime-only pinned dependency set (numpy/onnxruntime/jsonschema) from
  `source-code/requirements-lock.txt` (dev/test tooling) - the edge
  container never ships scikit-learn/skl2onnx/joblib.
- 2026-09-19 - QA (Claude) - P03.09 TODO to DONE. **Phase 03 exit gate
  met.** Built `source-code/simulator/verification/`: a cross-stage bounds
  check (`verify_bounds.py`) that is deliberately *not* a re-run of each
  stage's own `build_and_verify.py` (those already prove their own
  determinism/coverage) - it checks properties that only make sense across
  all stages at once: geo-projection round trip (every event's lat/lon,
  projected back to the network's local plane via `inverse_project`, lands
  inside the network's real `convBoundary`, read from `district.net.xml`
  rather than hardcoded), timestamp window, confidence range, speed range,
  and device referential integrity (every event's `device_id` exists in
  that stage's own registry). Ran clean: 1715 events across all 4 stages
  (sensors/emergency/scenarios/faults), 0 problems.
  `build_and_verify.py` requires all 4 stages present (a "skipped" stage
  does not count as a pass). Added a visual check
  (`plot_device_map.py`, needs `matplotlib`, installed ad hoc and
  deliberately not added to `requirements-*.txt`, same reasoning as P02.09's
  ad hoc `npx @mermaid-js/mermaid-cli`): plotted and visually inspected the
  P03.03 catalog's 70 devices, confirmed all land on the 12 expected
  intersections matching the known network layout. Wrote
  `docs/evidence/SIMULATION_LIMITATIONS.md` (every documented
  simplification across P03.01-P03.09 consolidated in one place - existing
  scattered README limitations were not duplicated, this is a single
  canonical index) and `docs/evidence/PHASE_03_SIMULATION_VERIFICATION.md`
  plus three committed JSON evidence snapshots
  (`bounds_report.json`/`replay_report.json`/`split_summary.json`) under
  the QA-owned `docs/evidence/` path named in `docs/FOLDER_STRUCTURE.md`.
  Added `source-code/tests/test_scenario_bounds.py` (3 tests). Full
  `check.py`: ruff clean, 92 pytest passed (89 prior plus 3 new),
  management validation passed. **All nine Phase 03 tasks (P03.01-P03.09)
  are DONE**; Phase 03's exit gate ("deterministic normal/fault/emergency
  runs and leakage-safe datasets can be regenerated with protected ground
  truth") is met.
- 2026-09-19 - SIM (Claude) - P03.08 TODO to DONE. Built
  `source-code/simulator/datasets/`: disjoint train/validation/test splits
  over 9 seeds (train 5, validation 2, test 2; each seed in exactly one
  split), pure Python, reusing P03.05's seed-parametrized demand generator
  and P03.06's fault generator. Every unit's `run_id` embeds its split and
  seed (`p03-08-<split>-seed-<seed>`), which is what actually makes every
  `entity_id`/`event_id` split-unique. Required a real change to P03.06:
  its fault generator previously produced identical device selections and
  onset/end timing regardless of `run_id` (only `event_id` differed) -
  useless for "splits differ across seeds". `build_faults` now takes a
  `seed` parameter that perturbs device selection and onset timing via a
  seeded RNG; P03.06's own tests were re-run and pass unchanged (they check
  structure, not specific values). `check_leakage` proves: the three
  splits' seed sets are pairwise disjoint; no `entity_id`/`event_id`
  appears in more than one split; no two splits produced byte-identical
  `fault_events.jsonl`. Documented explicitly in README.md what "leakage
  check" does *not* mean here: route IDs and fault types are a shared,
  necessarily-non-disjoint vocabulary (9 route templates, 7 fault types
  total), not a per-split resource - the check is about specific records,
  not vocabulary. Deliberately excludes the SUMO-simulated content
  (P03.03/P03.04/P03.05's physical scenarios) from the split - multiplying
  real SUMO runs across 9 seeds was judged out of scope for this pass;
  that existing single-seed output remains available as reference material
  only. `source-code/tests/test_dataset_splits.py` (7 tests, can
  self-regenerate since no Docker is needed). Full `check.py`: ruff clean,
  89 pytest passed (82 prior plus 7 new), management validation passed.
  **P03.09 is now dependency-ready** (dep P03.08 DONE) - the last Phase 03
  task.
- 2026-09-19 - SIM (Claude) - P03.07 TODO to DONE. Built
  `source-code/simulator/manifest/`: an immutable run manifest
  (`build_manifest.py`) over 12 specific JSON/event output files across
  P03.03-P03.06 (sha256 per file, `manifest_sha256` over the sorted
  path:hash pairs so any change to any file or the file set changes it),
  plus a minimal ordered/idempotent/duplicate-safe replay
  (`replay.py`) over the 4 event-stream files, per `docs/PROJECT_CONTEXT.md`'s
  "Replay is ordered, acknowledged, bounded, idempotent, and duplicate-safe"
  invariant. Pure Python, no SUMO/container needed (reads what those stages
  already wrote). `build_and_verify.py` proves: `verify_manifest` finds zero
  problems against both the freshly-built manifest object and the same
  manifest reloaded from its JSON file; replaying the same 1715 merged
  events twice produces the identical accepted-id sequence and hash
  (deterministic); replaying with every event duplicated produces the
  *same* accepted hash/count with every extra copy rejected as duplicate
  (idempotent, duplicate-safe, not just "ran twice"). Found and fixed a
  real bug at verification time: the manifest's first replay found 4
  `event_id` collisions - P03.06's `model`/`service`/`storage` faults share
  one `aiops_agent` device but each fault's local seq=0/seq=1 numbering
  reset per fault instead of continuing per device, colliding
  `uuid5(namespace, f"{run_id}:{device_id}:{seq}")` across faults;
  `source-code/simulator/faults/generate_fault_events.py` now tracks one
  global per-device sequence counter, the same pattern P03.04 already used.
  Added `source-code/tests/test_run_manifest.py` (6 tests, including a
  regression guard pinning the no-duplicate-event-id fix), which skips
  cleanly if the Docker-generated upstream stages' output isn't present
  rather than trying to regenerate a full SUMO pipeline. Full `check.py`:
  ruff clean, 82 pytest passed (76 prior plus 6 new), management validation
  passed. **P03.08 is now dependency-ready** (dep P03.07 DONE).
- 2026-09-19 - SIM (Claude) - P03.06 TODO to DONE. Built
  `source-code/simulator/faults/` for all 7 required device/platform fault
  types (silence/stuck/clock/network/model/service/storage), same labeled
  two-event overlay mechanism as P03.05's overlay scenarios
  (`generate_fault_events.py`). Unlike P03.01-P03.05 this needs no SUMO or
  container at all - these are platform/AIOps-layer faults (device health,
  clock sync, network link, model/service/storage health), not road
  physics - so it runs directly via `python
  source-code/simulator/faults/build_and_verify.py`. Four faults attach to
  real P03.03 catalog devices; `model`/`service`/`storage` have no matching
  device in that road-sensor catalog, so this registers one new
  `device/v1` record (`device_type: "aiops_agent"`, already in the
  contract's enum) representing the platform's own self-observability
  agent. `build_and_verify.py` proves ground truth, events and the new
  device record are byte-identical across two full runs and that all 7
  fault types are present. Contract conformance is a separate host-venv
  test, `source-code/tests/test_fault_ground_truth.py` (7 tests, same
  jsonschema-availability split as P03.03-P03.05) - which, needing no
  Docker, can regenerate the output itself if missing. Full `check.py`:
  ruff clean, 76 pytest passed (69 prior plus 7 new), management validation
  passed. **P03.07 is now dependency-ready**: P03.03/P03.04/P03.05/P03.06
  are all DONE.
- 2026-09-19 - SIM (Claude) - P03.05 TODO to DONE. Built
  `source-code/simulator/scenarios/` for all 9 required scenario types,
  split deliberately into two mechanisms: normal/peak/event/stall are real
  SUMO runs with shaped demand (`generate_physical_demand.py`, reusing
  P03.02's route tables but not its fixed counts); collision/wrong_way/
  flood/visibility/signal are labeled two-event telemetry overlays against
  real P03.03-catalog devices (`generate_overlay_events.py`), because SUMO
  cannot safely produce a real crash or wrong-way maneuver without
  disabling core safety behavior, and has no flood/fog/cabinet-fault
  physics at all - overlaying labeled sensor evidence is the honest
  approach for those five, argued in full in README.md. `stall`'s ground
  truth onset/end are the *measured* `started`/`ended` timestamps read back
  from SUMO's own stop-output after the run, not computed in advance.
  Ground truth is a plain internal JSON shape (`ground_truth.jsonl`), not a
  new formal contract, since Phase 02's 15-contract set is already closed
  and this is SIM-scoped labeling, not a platform interface (user-approved
  approach before implementation). `build_and_verify.py` proves all 4
  physical scenarios hit zero teleports/zero collisions (including peak at
  2x demand: 108 vehicles loaded vs normal's 56) and that ground truth,
  overlay events and the physical-run summary are all byte-identical across
  two full runs. Schema conformance for the overlay events plus ground-truth
  shape/determinism checks are a separate host-venv test,
  `source-code/tests/test_scenario_ground_truth.py` (8 tests), same
  jsonschema-availability split as P03.03/P03.04. Full `check.py`: ruff
  clean, 69 pytest passed (61 prior plus 8 new), management validation
  passed. This unblocks nothing new on its own (P03.06 was already
  dependency-ready on P03.03); P03.07 still needs P03.06 as well.
- 2026-09-19 - SIM (Claude) - P03.04 TODO to DONE. Built
  `source-code/simulator/emergency/`: a deterministic call/dispatch
  generator (`generate_scenario.py` - 9 calls across 3 agencies, 6 units,
  least-busy-unit dispatch) plus an orchestrator (`build_and_verify.py`)
  that runs each dispatched unit as a real SUMO trip (`vClass="emergency"`,
  auto-routed by SUMO's own router from a from-edge/to-edge pair, not a
  fabricated route) and reads back actual duration/routeLength/arrival from
  `tripinfo` output and position/speed/heading from `fcd` output - so route
  distance, ETA and AVL position are measured, not invented. Assembles
  `emergency-call/v1`, `emergency-unit-assignment/v1`, `device/v1`
  (`emergency_cad_avl_adapter`) and `observation-envelope/v1`
  (`emergency.unit_position.avl`) records; an explicit recursive scan fails
  the build if any record anywhere contains a `patient`-named field or
  value. Found and fixed a real bug at verification time: SUMO silently
  drops (warns, does not error) any trip listed out of departure-time order
  in the route file, and `dispatches` is reported-time-sorted, not
  depart-time-sorted (per-agency queuing can reorder them) - the trips file
  writer now sorts by `acknowledged_at_s` independently of dispatch order,
  and the depart-time output is now explicitly checked against the trip IDs
  requested so a future silent drop fails loudly instead of a bare
  `KeyError`. `build_and_verify.py` proves determinism (byte-identical
  calls/assignments/devices/AVL-events across two runs) and zero
  teleports/collisions for the emergency trips; schema conformance is a
  separate host-venv test, `source-code/tests/test_emergency_contracts.py`
  (6 tests), same jsonschema-availability reasoning as P03.03. Full
  `check.py`: ruff clean, 61 pytest passed (55 prior plus 6 new),
  management validation passed. P03.06 (dep P03.03 DONE) was already
  dependency-ready; this does not change any other task's dependency state.
- 2026-09-19 - SIM (Claude) - P03.03 TODO to DONE. Built
  `source-code/simulator/sensors/`: a pure-function device catalog
  (`build_sensor_catalog.py`, 70 devices across 6 types placed on the P03.01
  network - 18 loop, 18 cycle counter, 16 crossing, 12 signal, 3 weather, 3
  road) plus a deterministic observation-event generator
  (`generate_observations.py`) combining native SUMO induction-loop and
  `SaveTLSStates` output (real simulated counts/occupancy/speed and SPaT
  transitions) with seeded synthetic generators for pedestrian crossing
  demand and weather/road readings (documented as a scoped simplification,
  not FCD-trajectory-linked or a physical weather model).
  `build_and_verify.py` runs inside the pinned SUMO container, builds the
  catalog/events twice from the same seed/run_id and proves both are
  byte-identical, and checks all 5 required categories (traffic/vru/signal/
  weather/road) are present. Schema conformance against
  `contracts/device/v1` and `contracts/observation-envelope/v1` needs
  `jsonschema`, which the pinned SUMO image doesn't have, so it is a
  separate host-venv test, `source-code/tests/test_sensor_catalog.py` (6
  tests: 2x schema conformance, category coverage, unit/quality/confidence
  presence, identity/location/provenance presence, cross-run determinism) -
  reads the git-ignored `output/run-a/` and `output/run-b/` this script
  writes. Full `check.py`: ruff clean, 55 pytest passed (49 prior plus 6
  new), management validation passed. This unblocks P03.05 and P03.06
  (both depend on P03.03) and contributes to P09.06's data inventory.
- 2026-09-18 - SIM (Claude) - P03.02 TODO to DONE. Built
  `source-code/simulator/demand/generate_demand.py`: pure-Python, no SUMO
  dependency, deterministic `(seed, run_id)` to vehicles/cyclists/
  pedestrians/transit plus a `sim_time -> UTC` clock manifest
  (`anchor_utc + depart_seconds`). Fixed a real bug found at verification
  time: cyclists initially drew from the full route pool including
  cross-corridor routes, but cross-street edges carry no bike lane, so
  netconvert generates no bicycle-legal junction connection onto them,
  producing SUMO "no valid route" errors; added a corridor-only
  `CYCLIST_ROUTES` pool. Also caught and excluded the statistics file's
  wall-clock `<performance>` block from the determinism hash comparison (it
  legitimately differs run to run and is not simulation output). Verified:
  generator byte-identical and simulation semantic-hash-identical across two
  independent runs each; 40 vehicles/12 cyclists/16 pedestrians/4 transit;
  0 teleports/0 collisions; all 4 transit stops completed; clock mapping
  independently recomputed and matched. Full `check.py`: ruff clean, 49
  pytest unchanged, management validation passed.
- 2026-09-18 - SIM (Claude) - P03.01 TODO to DONE. Built the versioned
  (`geometry_version: 2026-09-18.1`) 12-intersection/three-corridor district
  as hand-authored plain-XML SUMO input (`source-code/simulator/network/
  plain/`), following P01.06's digest-pinned `ghcr.io/eclipse-sumo/sumo`
  container convention. `netconvert` produces 12 traffic-light intersections
  and 16 pedestrian crossings with zero warnings. Every corridor edge
  isolates a dedicated protected bicycle lane from general traffic (verified
  both from the network XML and from an actual cyclist's FCD trace never
  leaving that lane). A demo simulation (car + cyclist + transit bus with 2
  scheduled stops) completed with 0 teleports and 0 collisions, proving the
  network and signals are actually drivable, not just syntactically valid.
  `build_and_verify.py` + `run_container.sh` reproduce this in one command.
  Full `check.py`: ruff clean (auto-formatted once), 49 pytest unchanged,
  management validation passed. This is the network only; deterministic
  multi-mode demand at scale is P03.02, sensor telemetry is P03.03.
- 2026-09-18 - ARCH (Claude) - P02.09 TODO to DONE. Drafted all seven
  mandatory views as Mermaid sources under `diagrams/sources/` (system
  architecture, network flow, data flow, workflow/command-safety-path as a
  sequence diagram, security/trust boundaries with 9 zones, hybrid/cloud
  placement with TESTED-vs-PROPOSED-not-deployed styling per ADR-0007, K3s
  deployment topology), each rendered to `diagrams/exports/*.svg` via
  `@mermaid-js/mermaid-cli` (real headless-Chromium render, not just syntax
  check). Found and fixed one real defect during rendering: `Opt` collided
  with Mermaid's reserved `opt/end` sequence-block keyword. Four diagrams
  (system architecture, workflow, security/trust boundaries, hybrid
  placement) visually inspected as PNG and confirmed legible. `diagrams/
  README.md` now indexes all seven against their RQ. **Phase 02 exit gate
  met**: all nine tasks (P02.01-P02.09) are DONE. Full `check.py` still
  passes unchanged (49 pytest, ruff clean, management validation).
- 2026-09-18 - SEC/GRC (Claude) - P02.08 TODO to DONE. Wrote
  `docs/security/ROLES_AND_ACTION_AUTHORITY.md`: eleven operational roles
  (human + service identities, distinct from the sixteen project-agent skill
  codes), seven authority classes (RECOMMEND/REQUEST/APPROVE/EXECUTE/
  OVERRIDE/DEMO/AUDIT) with explicit per-class holders, and three safety
  classes (SC-0/SC-1/SC-2) mapping `action_type` to required
  requester/approver/executor/override roles, including the mandatory
  four-eyes (`requested_by != approved_by`) rule for SC-1/SC-2 and why DEMO
  is structurally unable to reach the operational command path. Binding for
  P07.05, P08, P09.02, P09.03.
- 2026-09-18 - OPS/SEC (Claude) - P02.07 TODO to DONE. Defined
  `metric-label-set/v1` (propertyNames restricted to a fixed low-cardinality
  key enum, structurally forbidding vehicle/track IDs as metric labels per
  `docs/environment/RESOURCE_BUDGET.md`), `slo-definition/v1` (objective
  type/target/window/query-pointer, optionally linked to an
  `docs/requirements/ACCEPTANCE_TARGETS.md` target ID) and
  `policy-decision/v1` (OPA input/decision pair, conditional `reason`
  required on `deny`, `permit`/`deny` only - no decision value represents a
  policy-unavailable outage) under `source-code/contracts/`, each with
  passing valid/invalid examples. Full `check.py`: ruff clean, 49 pytest
  passed (40 prior plus 9 new), management validation passed.
- 2026-09-18 - EMERG (Claude) - P02.06 TODO to DONE. Defined
  `emergency-call/v1` (ambulance/fire/police, priority, source_reliability,
  status lifecycle, deliberately no patient/medical field) and
  `emergency-unit-assignment/v1` (assigned/acknowledged/en_route/staged/
  on_scene/clear/unavailable state machine, route_alternatives with per-route
  ETA and uncertainty, optional cross-agency handover) under
  `source-code/contracts/`, each with passing valid/invalid examples. Full
  `check.py`: ruff clean, 40 pytest passed (34 prior plus 6 new), management
  validation passed.
- 2026-09-18 - LEAD (Claude) - P02.05 TODO to DONE. Defined `forecast/v1`
  (per-measurement uncertainty required, truth_label const "predicted"),
  `incident/v1` (root_cause_hypothesis kept distinct from verified_cause,
  non-empty evidence_event_ids), `recommendation/v1` (alternatives with
  predicted_benefit/predicted_harm/confidence, safety_bounds required, never
  invokes an adapter directly), `command/v1` (required idempotency_key,
  enumerated structured error with retryable flag) and `outcome/v1`
  (independent pre/post_window verification, enumerated classification
  including "unknown", separate from command/v1) under
  `source-code/contracts/`, each with passing valid/invalid examples.
  Unblocks P02.07, P02.09, and gives P02.08 half its inputs (with P02.06).
- 2026-09-18 - LEAD/QA (Claude) - P02.01 TODO to DONE. Wrote
  `docs/requirements/ACCEPTANCE_TARGETS.md`: 30 measurable targets across
  latency, load/throughput, accuracy, false alarm, emergency ETA, safety,
  recovery and UX/accessibility, each with explicit measurement conditions
  and a Result column pinned to "Not yet measured" until its named task
  (P04.09 through P12.02) produces evidence - no result is claimed here.
  `validate_management.py` and full `check.py` still pass unchanged (19
  pytest, ruff clean).
- 2026-09-18 - LEAD (Claude) - P02.04 TODO to DONE. Defined
  `road-geometry/v1` (MAP: approaches, lanes, movements, conflict groups,
  stop lines, crossing zones, geometry_version/effective_from/superseded_by),
  `signal-state/v1` (SPaT + controller mode/plan/cycle/movements/freshness,
  cross-referencing geometry_version) and `network-state/v1` (per lane/
  segment/intersection/corridor aggregate measurements with sample_count,
  truth_label, freshness_status) under `source-code/contracts/`, each with
  passing valid/invalid examples. `test_contracts.py` auto-discovers new
  contract directories; full `check.py`: ruff clean, 19 pytest passed (10
  prior plus 9 new), management validation passed. Unblocks P02.05, P02.06,
  P02.09, P03.01.
- 2026-09-18 - DEVOPS (Claude) - P01.07 IN_PROGRESS to BLOCKED. Codex's
  k3d/K3s cluster (`rancher/k3s:v1.35.5-k3s1`) reached `Ready`; deployed and
  verified a least-privilege scoped test workload to `Ready`, then removed
  it. Ran Falco 0.39.2 against all three driver types: fixed a real
  `fs.inotify.max_user_instances` exhaustion (128 to 1024, additive, host
  UID-0 processes from the shared WSL2 VM) that was blocking daemon startup,
  then found live syscall capture fails on `modern_ebpf` (`scap_init`
  failure), `ebpf` (no prebuilt probe for this kernel string) and kernel
  module (no matching `/lib/modules/.../build` headers) - a WSL2 kernel
  limitation, not a configuration error. Evidence in
  `docs/environment/CONTAINER_K3S_FALCO_FEASIBILITY.md`. Unblock action:
  supply/authorize the target Ubuntu LTS host from
  `docs/environment/TOPOLOGY.md`.
- 2026-09-18 - LEAD (Claude) - P02.03 TODO to DONE. Defined versioned
  `device/v1` and `observation-envelope/v1` JSON Schema contracts under
  `source-code/contracts/` covering identity, time, units, quality,
  provenance, truth-label and privacy/retention classification, each with
  valid and invalid examples and a `truth_label` kept distinct from device
  `deployment_type` (simulated/real) per `docs/PROJECT_CONTEXT.md`. Added
  `source-code/tests/test_contracts.py`; full `check.py` run: ruff clean, 10
  pytest passed (4 prior plus 6 new), management validation passed. This
  unblocks P02.05, P02.07, P02.09, P03.03, P04.01, P05.01 and P05.04. Worked
  in parallel with Codex on P01.07; touched only `source-code/contracts/`,
  `source-code/tests/test_contracts.py` and the two management files.
- 2026-09-18 - DEVOPS - P01.07 TODO to IN_PROGRESS. Started bounded WSL2 container/K3s/Falco feasibility; native target acceptance remains explicitly open.
- 2026-09-18 - SIM - P01.06 IN_PROGRESS to DONE. Pinned official SUMO 1.27.1 by digest, generated a map and demand, completed two repeat wrapper invocations, and reproduced 15,426 telemetry records with equal semantic hashes.
- 2026-09-18 - ARCH (Claude) - P02.02 TODO to DONE. Recorded eight ADRs
  (simulator, streaming, data, map, AI, edge, central/cloud, deployment) with
  options considered, trade-offs, consequences and revision triggers under
  `docs/decisions/`. Map (ADR-0004) and central/cloud placement (ADR-0007)
  are Provisional and reference the existing open questions in `Memory.md`;
  no new user decision was invented. Worked in parallel with Codex on
  P01.06/P01.07; touched only `docs/decisions/` and the two management files.
- 2026-09-18 - SIM - P01.06 TODO to IN_PROGRESS. Started pinned headless SUMO and deterministic telemetry feasibility on the WSL/Docker development route.
- 2026-09-18 - DEVOPS - P01.05 IN_PROGRESS to IN_REVIEW. Added least-privilege Python, Node and Trivy gates; local checks and Trivy 0.74.0 scan pass, while the first hosted GitHub run remains pending.
- 2026-09-18 - DEVOPS - P01.05 TODO to IN_PROGRESS. Started least-privilege Python, frontend and security CI gates.
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
