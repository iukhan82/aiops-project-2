# Project memory and handover

Last updated: 2026-09-23 (Asia/Karachi), Phase 09/10 in-progress session

## Current state

- Project 2 planning baseline is established for an Intelligent Traffic and
  Emergency Response Platform.
- Both six-page assessment PDFs were read completely. Requirements are normalized
  in `docs/requirements/TRACEABILITY.md`; the originals remain outside this
  project because they contain confidential/personal assessment information.
- The initial demonstration assumption is 12 simulated intersections across
  three corridors with ambulance, fire, and police scenarios. This is provisional
  until environment and schedule profiling.
- Platform feature, sensor/data, architecture, safety, compliance, protocol, and
  delivery-roadmap documents exist.
- Sixteen project-local role skills exist and have been validated.
- Source, diagram, documentation, presentation, skills, workflow, and sanitized
  file areas are separated. Phases 03 (simulation/datasets), 04 (edge AI),
  05 (streaming/geospatial/backend), **06 (traffic intelligence), 07
  (emergency coordination and governed control) and 08 (operations UI) are
  all complete**; real MQTT/Kafka/PostgreSQL services, a gateway, central
  ingestion, network-state aggregation, versioned/live APIs, incident/
  command repositories, detectors/forecasts/incident correlation,
  emergency dispatch, governed simulator control with independent outcome
  verification, and a 21-screen operator UI behind Keycloak are all built
  and verified against the real running stack (`source-code/infra/`,
  `source-code/database/`, `source-code/backend/`, `source-code/frontend/`).
  **P09.02 (Keycloak) was pulled forward** to unblock the UI's sign-in.
- Management validation passed: 132 unique tasks, 16 valid skills, no dependency
  cycles, required folder structure present, and local Markdown links resolved.

## Current task

Codex has reached its session limit and is not currently active; Claude is
the sole active agent. Phase 01: P01.06 DONE (SUMO); P01.05 IN_REVIEW (no
hosted GitHub Actions run yet, needs user-authorized push); **P01.07 DONE**
(2026-09-19: user supplied a real Ubuntu 24.04.4 LTS host,
`192.168.11.108`/"monitoring", kernel `7.0.0-31-generic`, KVM-virtualized;
installed `kind` v0.33.0, cluster `aiops-test` node Ready, isolated from the
host's pre-existing K3s v1.36.4+k3s1 cluster; Falco live syscall capture
proven working with Falco **0.44.1**'s `modern_ebpf` driver - 0.39.2 (the
version pinned elsewhere in this project) has an incompatible kmod driver
source against kernel 6.4+'s `class_create()` signature and a silently
failing modern_ebpf path on this kernel; verified live (not replayed) via a
triggered `sudo cat /etc/shadow` producing a matching Falco alert with the
exact SSH-session process ancestry within 3s. Full mechanism and
reproduction in `docs/environment/CONTAINER_K3S_FALCO_FEASIBILITY.md`.
**Action item for any future Falco deployment in this project: pin
`>=0.44.1`, not 0.39.2.** The host has a pre-existing `aiops` namespace
workload and an unrelated `interview-platform` namespace this session did
not create and did not disturb - flagged to the user as a shared host.
The SSH password was shared in plaintext chat and should be rotated by the
user.)

**Phase 02 exit gate is met: P02.01-P02.09 are all DONE.** `docs/decisions/`
holds 8 ADRs; `docs/requirements/ACCEPTANCE_TARGETS.md` holds 30 measurable
targets, all Result columns "Not yet measured";
`docs/security/ROLES_AND_ACTION_AUTHORITY.md` finalizes 11 operational
roles, 7 authority classes and 3 safety classes; `source-code/contracts/`
holds 15 versioned JSON Schema contracts (device, observation-envelope,
road-geometry, signal-state, network-state, forecast, incident,
recommendation, command, outcome, emergency-call,
emergency-unit-assignment, metric-label-set, slo-definition,
policy-decision), each with passing valid/invalid examples
(`source-code/tests/test_contracts.py`, 49 pytest checks); `diagrams/`
holds all 7 mandatory Mermaid views (system, network, data, workflow,
security, hybrid, deployment) as `sources/*.mmd` rendered to
`exports/*.svg` via `@mermaid-js/mermaid-cli` (real headless-Chromium
render), 4 of 7 visually inspected as PNG.

**Phase 03 exit gate is met: P03.01-P03.09 are all DONE.** Everything below
lives under `source-code/simulator/<stage>/`, each with its own README and
`run_container.sh` (or, for the pure-Python stages, a plain `python
build_and_verify.py`); `docs/evidence/SIMULATION_LIMITATIONS.md` is the
single consolidated list of every documented simplification, and
`docs/evidence/PHASE_03_SIMULATION_VERIFICATION.md` plus its 3 committed
JSON snapshots are the cross-stage verification evidence.

- `network/`, `demand/` (P03.01/02): versioned 12-intersection/3-corridor
  SUMO district (`geometry_version: 2026-09-18.1`) plus a pure-Python
  `(seed, run_id)` demand generator (40 vehicles/12 cyclists/16
  pedestrians/4 transit baseline). Both proven byte-identical across
  repeats.
- `sensors/` (P03.03): 70-device catalog + 1453-event observation stream
  (`contracts/device/v1` + `contracts/observation-envelope/v1`); traffic/
  signal telemetry from native SUMO output, VRU/weather/road from seeded
  synthetic generators (documented simplification).
- `emergency/` (P03.04): 9 calls/9 assignments/6 AVL devices/238 AVL
  events (`contracts/emergency-call/v1` + `-unit-assignment/v1`); each
  unit's route/ETA/position is a real, auto-routed SUMO trip; explicitly
  scanned patient-free.
- `scenarios/` (P03.05): all 9 required scenario types with separate
  ground truth; normal/peak/event/stall are real SUMO runs, collision/
  wrong_way/flood/visibility/signal are labeled telemetry overlays (SUMO
  cannot safely simulate those five - reasoning and user sign-off in its
  README).
- `faults/` (P03.06): all 7 required device/platform fault types, same
  overlay mechanism, pure Python (no SUMO). One new `aiops_agent` device
  covers model/service/storage faults.
- `manifest/` (P03.07): immutable manifest (sha256 per file) + an
  ordered/idempotent/duplicate-safe replay proof over 1715 merged events.
  Caught and fixed a real cross-fault `event_id` collision in P03.06.
- `datasets/` (P03.08): disjoint train(5)/validation(2)/test(2)-seed
  splits, 0 leakage problems. Required making P03.06's fault generator
  seed-parametrized (previously seed-independent).
- `verification/` (P03.09): a new cross-stage bounds check (geo-projection
  round trip, timestamp window, confidence/speed range, device referential
  integrity) over all 1715 events - 0 problems; a visually-inspected device
  map confirming correct spatial placement.

All outputs proven byte-identical across repeated runs at every stage.

**Phase 04 exit gate is met: P04.01-P04.09 are all DONE.** Two new top-level
areas, each with its own README:
`docs/evidence/PHASE_04_EDGE_AI_VERIFICATION.md` and
`docs/evidence/EDGE_AI_LIMITATIONS.md` hold the consolidated evidence/limits.

- `source-code/edge/` (serving path, P04.01/05/06/07/08): `EdgeValidator` +
  `FeatureBuilder` (past-only, corridor-context, 24 features
  `loop-window/2`) feed a verified-ONNX-or-transparent-baseline
  `EdgeRuntime` with bounded Prometheus metrics, `/healthz`/`/readyz`, a
  SQLite WAL durable outbox (ordered/acked/idempotent/bounded, crash-
  recovery proven), and atomic model activation/rollback (`os.replace`,
  verify-then-commit). Ran for real in a 0.75 CPU/512 MiB non-root
  read-only container (`edge/Dockerfile`): warm inference p95 0.135ms,
  peak RSS 75.8MB. `edge/vision_privacy.py` adds privacy-zone suppression +
  per-window ephemeral identity + k-anonymity floor for camera-derived
  metadata, grounded against a real P03.02 pedestrian trajectory.
- `source-code/models/` (training path, P04.02/03/04/09): built a new
  63-run/9-seed real SUMO road-blockage dataset first (`models/dataset/`,
  reusing P03.08's seed/split assignment) since none existed; transparent
  rule baseline (P04.02) fit TRAIN/selected VALIDATION; a 30-candidate
  model search (P04.03) with the test split opened exactly *once*
  (`models/registry/test_split_ledger.json`) - model beat the baseline on
  held-out F1 (0.524 vs 0.480) and incident recall (29/48 vs 24/48).
  ONNX export (P04.04) with ORT-vs-sklearn parity <=6.6e-7. P04.09's
  benchmark replayed 12,960 real events through the real bounded container
  and cited (not recomputed) the sealed test-split accuracy.
- Total: 288 pytest passing (`source-code/tests/`), all via `check.py`,
  ruff clean, management validation clean.

**Phase 05 exit gate is met: P05.01-P05.10 are all DONE.** Three new
top-level areas, each with its own README: `source-code/infra/` (DEVOPS),
`source-code/database/` (DATA/BACKEND), `source-code/backend/` (BACKEND).
Every task's evidence is a real run against the live P05.01 stack
(Mosquitto, Redpanda, PostgreSQL/PostGIS via `source-code/infra/platform/`
docker-compose), not a mock or a unit test alone.

- `infra/platform/` (P05.01/P05.02): digest-pinned Mosquitto/Redpanda/
  PostGIS, resource-limited per `RESOURCE_BUDGET.md`, health/persistence
  proven across a real container restart. mTLS listener (8883) with
  per-device client-cert identity and topic ACLs; found and documented a
  real mosquitto quirk (SUBACK is granted optimistically, enforcement is at
  delivery time - verified via actual message delivery, not the SUBACK
  code).
- `backend/gateway/` (P05.03): MQTT-to-Kafka bridge reusing
  `edge.outbox.DurableOutbox` (P04.07) as its own Kafka-outage buffer.
  Fixed a real bug during build: DurableOutbox's sqlite3 connection cannot
  cross threads (it's documented single-writer/single-reader) - the fix is
  a single dedicated worker thread that owns the outbox exclusively, not a
  change to the already-tested P04.07 module.
- `database/` (P05.04/P05.10): 11 ordered, checksummed migrations
  (`migrate.py`) - topology/devices/telemetry/commands/incidents/
  emergency/ingestion-support/audit-trails/scenario-control/retention.
  Checksum-tamper detection proven; a real topology seed from the actual
  P03.01 SUMO network. `retention.py`: per-`retention_class` windows,
  `audit` class never purged, rollup-before-delete, active (non-terminal)
  commands/incidents preserved regardless of age, storage-pressure proven
  to shorten the effective window on real before/after data.
- `backend/ingestion/` (P05.05): Kafka-to-Postgres consumer - schema/
  identity/content conflicts all rejected and audited; identical replay is
  idempotent both per-message and for a fresh consumer group replaying the
  whole topic from scratch.
- `backend/state/` (P05.06): windowed network-state aggregation, carry-
  forward on empty windows, a produced record verified against the real
  `contracts/network-state/v1` schema via `jsonschema.validate`.
- `backend/api/` (P05.07): FastAPI+uvicorn (no ADR fixed a backend
  framework - documented rationale in the README) - versioned
  cursor-paginated device/observation/network-state APIs plus a device/
  corridor-scoped live WebSocket feed; reconnect proven gapless and
  duplicate-free against a real running server on a real socket.
- `backend/repositories/` (P05.08): incident/command state machines with
  row-locked concurrency safety (proven via two real threads racing a
  transition on the same row through separate connections), idempotent
  command creation, required audit references.
- `backend/scenario_control/` (P05.09): a deliberately separate demo-
  control API wrapping P03.07's real replay mechanism. Fixed a real
  phantom-read gap in the concurrent-run bound during build (`SELECT
  COUNT(*) FOR UPDATE` cannot stop a new concurrent INSERT; switched to
  `pg_advisory_xact_lock`), then proved the bound holds under genuinely
  concurrent requests (`asyncio.gather`, not sequential calls).
- Total at Phase 05's close: 324 pytest passing, ruff clean.

**Phase 06 exit gate is met (P06.01-P06.08).** `backend/analytics/`: corridor
KPIs validated against SUMO truth on a shared 36-run dataset; four
transparent forecast baselines plus a trained model that is accepted only
where it beats the baseline on validation (TEST opened once, ledgered);
congestion/spillback, stalled-vehicle (real edge ONNX model fused with
congestion), overlay hazards and pedestrian/cyclist conflict candidates
(anonymous, PET truth); graph-aware incident correlation with calibrated
confidence, escalation, resolve and reopen; incident-level evaluation with
FA-01 and LAT-03 measured. Weak results are recorded as weak (recall
bounded by loop placement, small samples reported with Wilson intervals).

**Phase 07 exit gate is met (P07.01-P07.10).** `backend/emergency/`,
`routing/`, `control/`: CAD/AVL adapter and call/assignment state machines,
fastest-safe routes with uncertainty, cross-agency staging, recommendations
with hard safety bounds, the execution-time policy (four-eyes, role, expiry,
target, bounds, policy outage), real simulator signal/diversion/sign
adapters (TraCI in the pinned SUMO container), pre-emption, transit
priority, independent outcome verification with physical rollback, and
three end-to-end scenarios with measured ETA/safety/traffic outcomes and
failure limits. LAT-04 met.

**Phase 08 exit gate is met (P08.01-P08.10)** - `source-code/frontend/`
(React 19 + TypeScript strict + Vite, plain CSS on generated tokens, SVG map)
and `docs/ux/`. One machine-readable inventory (`src/config/inventory.json`:
7 roles, 17 capabilities, 21 screens, 45 endpoints, journeys with failure
cases) drives the backend's default-deny authorization, the UI navigation,
the wireframes and the design docs; `tools/inventory.py`, `design.py`,
`verify_wireframes.py` prove the agreement. All 21 screens are real: live
map with accessible list, analytics, incidents, dispatch and a read-only
field view, recommendations/commands/outcomes with four-eyes approval,
shift handover, audit trail, platform status, and separate demo controls.
Sign-in is Keycloak 26 (Authorization Code + PKCE, token in memory); the API
validates RS256/JWKS strictly and audits refusals. Real Chrome + real
Keycloak + real API tests on a demo world (separate database `aiops_demo`
fed by `backend/demo/feeder.py`; command histories seeded by
`seed_actions.py`, real vs recorded flagged in the data). What the browser
suites prove is in `source-code/frontend/README.md`; results in
`docs/evidence/p08_*`. LAT-05, LOAD-04 and UX-01..04 are measured in
P08.10 - see `docs/requirements/ACCEPTANCE_TARGETS.md` for the numbers and
their limits.

Totals at Phase 08's close: 539 pytest and 41 vitest passing, 110 real-Chrome
tests across ten specs (all passing; two needed a rerun after host faults, listed
in `docs/evidence/p08_10_ui_acceptance.json`), ruff check and format clean
(`ruff format` was applied once to the whole tree: earlier phases' code had
never been formatted, so `check.py` and the CI gate would have failed),
management validation clean.

Things a future session must know: FastAPI 0.141 hides `include_router`
routes from a global dependency, so route modules use `register(app)`; the
demo world's replayed traffic does not react to commands (the verifier says
so in each outcome); the scenario-control API used to trust an
`X-Demo-Role` header and now verifies a Keycloak token; **the Postgres
container's backend aborted twice on a glibc malloc assertion**
(`malloc.c: sysmalloc: Assertion ... failed`, role `aiops_app`, database
`aiops_demo`, during BIND; 14:32 and 16:00 UTC on 2026-09-21; kernel log
"postgres: potentially unexpected fatal signal 6"). The cause is NOT
isolated (candidates: PostGIS/GEOS in the pinned `postgis/postgis:16-3.4`
image, WSL2 kernel 6.18.33.2, host memory pressure - about 0.5 GB free of
16 GB - and a saturated disk with 38-74 s checkpoint writes). Each crash
sends Postgres through about 40 s of recovery and it killed the demo
workers, so the feeder, executor and verifier now reconnect instead of
exiting. Investigate before P11's resilience work and before trusting a long
unattended run on this host. Also: `database/verify_migrate.py --reset`
wipes the `aiops` verification database's segments too, so reseed with
`seed_topology`/`seed_segments` afterwards or `verify_operator_actions.py`
finds no route; and never run `verify_keycloak.py` while browser suites are
running (it exercises account lockout).

## Exact next action

**Phase 08 is DONE; Phase 09 (security, privacy, compliance) is in progress.**
DONE: P09.01 (threat model: 76 threats, 47 controls in `source-code/security/`,
residual risk COMPUTED by `security/verify_threat_model.py`, generated tables
in `docs/security/`), P09.02 (Keycloak), P09.03 (Open Policy Agent as the
`aiops-opa` container on loopback 8181; policies in `source-code/policy/`;
`backend/pdp.py` fails closed; the executor re-decides before it acts;
`policy_decisions` is append-only; `backend/api/hardening.py` = headers, body
limit, rate limits; `python source-code/policy/verify_policy.py` is the
79-check acceptance run and stops/starts `aiops-opa`), **P09.05** (audit hash
chain over 8 tables - migration `0026_audit_hash_chain.sql`,
`backend/audit_chain.py` verifies server-side via SQL, NOT by re-deriving
`row_to_json` in Python - that was tried and is wrong because a nested jsonb
column renders with JSONB's own spaced formatting while the row's own
top-level fields render compact; `backend/redaction.py` wired into
`workflow.audit`/`policy.record_decision`/scenario-control + a DB CHECK
backstop + a log filter; `GET /api/v1/audit/export` (auditor only);
`POST /api/v1/commands/{id}/override` (incident_commander, SC-2 only);
`python source-code/backend/verify_audit_protection.py` is the 31-check run).
After changing a role, route or policy, run
`python source-code/policy/build_data.py` and `docker restart aiops-opa`.
P09.06 also DONE: `source-code/security/data_inventory.json` + `verify_data_inventory.py`,
`docs/security/DATA_INVENTORY_AND_DPIA.md` (7 categories, checked against
`database/retention.py`'s windows, the contract enums and the live `devices`
tables). CTL-39 is `partial` - device revocation has no CRL/OCSP-equivalent,
pointed at P11.02.
**Gotcha found the hard way**: the `aiops` verification database is SHARED,
persistent state across every verify script run in a session - a fixture a
script does not clean up outlives that run and can break a LATER, unrelated
script. Two concrete forms hit today: (1) an open critical incident silently
escalates any nearby action to SC-2 in every later script - if a verify
script fails with an unexpected SC-2/`role_cannot_review`, check
`SELECT * FROM incidents WHERE severity='critical' AND status='open'` first;
(2) a command left `executed` with no `command_outcomes` row is picked up by
`verifier_worker.verify_ready`'s UNSCOPED, system-wide scan ahead of whatever
a later script's own outcome-verification test just created, so THAT script's
own check fails on a command it never made - dozens of such rows from
P07.06/P07.09 runs earlier in this session were still sitting there hours
later. `verify_audit_protection.py` cleans its own fixtures in a `finally`
block; `verify_command_workflow.py` now sweeps pre-existing executed/no-
outcome rows before building its own fixtures. **P07.06 and P07.09 still do
not clean up their own `executed`/no-outcome commands** - a latent gap, not
yet fixed; if a future run of `verify_command_workflow.py` (or anything else
that calls `verify_ready`) fails the same way again, sweep manually:
`DELETE FROM commands WHERE status='executed' AND acknowledged_at < now() AND
NOT EXISTS (SELECT 1 FROM command_outcomes o WHERE o.command_id = commands.command_id)`.
Also found: WSL Docker's venv (`AIOPS-Project-1/.venv`) is on PATH by
default - every shell command in this project MUST start with
`cd "D:/AIOPS Project/AIOPS-Project-2" && source .venv/Scripts/activate &&`
or it silently runs against the WRONG project's Python (explains earlier
"confluent_kafka missing" flakiness). Docker itself is native-Linux WSL
only (no Windows docker client on this host): anything needing `docker`
(P09.08's supply-chain build, container feasibility work) must run via
`wsl -e bash -lc "cd '/mnt/d/AIOPS Project/AIOPS-Project-2' && ..."`, not
the Windows venv shell. A bare WSL `python3` is externally-managed (PEP
668) and has none of this project's pinned packages - a dedicated venv
was created at `~/.venvs/aiops-supply-chain` in WSL
(`python3 -m venv ~/.venvs/aiops-supply-chain && ~/.venvs/aiops-supply-chain/bin/pip
install -r source-code/requirements-lock.txt`) to run `build_release.py`
locally; CI installs the same lock into the runner's own Python instead.
**P09.08 DONE** (2026-09-23): edge image built and gated for real (SBOM,
Trivy scan, bandit SAST, signed provenance, proven admission gate - see
TASK_REGISTER for the full account); found a real 44-HIGH Trivy result
that turned out to be 100% upstream-NOFIX Debian OS packages in the pinned
`python:3.12-slim` base, gated on fixable-only rather than faking a clean
scan. One low-sensitivity side effect from tool exploration before the
real run: an early `cosign sign-blob` test (a throwaway one-line JSON test
file, not project data) was accidentally uploaded to the public Sigstore
Rekor transparency log before `--tlog-upload=false` was added - harmless
but irreversible and public, flagged to the user for completeness; the
real P09.08 artifacts were all signed with `--tlog-upload=false` and never
touched that log.
**P10.01 DONE** (2026-09-23): real OpenTelemetry instrumentation across the
edge-to-action path (edge inference, gateway, ingestion, network-state,
congestion detection, incident correlation, recommend/request/review/
verify_and_rollback), correlated by the real domain id already on each
message (event_id/incident_id/command_id), proven against the real running
stack in `verify_observability.py` (7/7 checks). Two real bugs caught by
the new tests before being called done: OTel's global TracerProvider/
MeterProvider is set-once-per-process (fixed by keeping provider instances
in `backend.observability._state` instead of depending on the global
registry - only matters when one process configures more than one
service, e.g. this project's tests); Starlette 1.6.0 does not populate
`scope["route"]` (fixed by matching `scope["endpoint"]` against the app's
routes instead - see `_matched_route_template`). Also fixed, found by
installing root `requirements-lock.txt` alone into a clean venv: it was
missing most of what the 615-test suite (now 625) actually imports and
would have failed the very first hosted CI run; rebuilt from a
clean-venv-verified freeze.
**P10.02 DONE** (2026-09-23): 10 real SLOs + the real 5-process dependency
topology, both cross-checked against real sources (`verify_slos.py`), not
hand-maintained. Caught a real mistake while building it: the topology
draft assumed edge-runtime publishes to MQTT - it does not, nothing in
`source-code/edge/` opens an MQTT connection, so `topology.json` now states
that as a real gap instead of a false edge. LAT-02 measured for real:
27.8 ms P95 (n=20), closing a target that had been open since P05.05.
**P10.03 DONE** (2026-09-23): Prometheus/Tempo/Loki/Grafana added to the
platform stack (digest-pinned, resource limits matching RESOURCE_BUDGET.md
exactly), Grafana provisioned not clicked through, real OTLP push/query
round trip proven for metrics+traces+logs+dashboard against the actual
running stack. Found and fixed two real bugs in P10.01's
`backend/observability.py` while proving this for real: `configure()` only
looked at the generic `OTEL_EXPORTER_OTLP_ENDPOINT` var (the correct setup
here needs per-signal endpoints - traces to Tempo, metrics to Prometheus -
so it silently exported nothing); the metrics reader's 60 s default
interval would let a short verify script exit before ever exporting -
added `observability.flush()` + a 2 s interval when OTLP is live. Also:
`source-code/infra/platform/.env` had CRLF endings from earlier
Windows-side edits, silently corrupting `GRAFANA_ADMIN_PASSWORD` with a
trailing `\r` under real WSL bash - fixed to LF. Metric naming confirmed
empirically (not assumed): OTLP-to-Prometheus counters get `_total`,
histograms get `_<unit>_bucket/_count/_sum`.
NEXT: P10.04 (alert rules - can build/test locally now that P10.02/P10.03
exist), then P10.05-P10.09 continue Phase 10. Phase 09
remainder needs a real host again: P09.04 (TLS/workload identity/secrets -
partly local, partly needs P11.02's cluster), P09.07 (control mapping,
needs P09.04/06 first), P09.09 (Falco - needs the real Ubuntu target host,
same as P01.07), P09.10. Two smaller items remain open from
earlier phases: **P01.05** (CI workflow built and locally green, no hosted
run yet - needs a user-authorized push) and **P01.08** (clean-start developer
instructions, unblockable). The frontend README documents the full run order
for the demo stack. The user's standing instruction: finish ALL phases, then
push to GitHub (no push before that and not without their go-ahead).

The real Ubuntu LTS host (`192.168.11.108`) is a **shared host** with a
pre-existing K3s cluster and unrelated namespaces - confirm scope with the
user before deploying anything there, and the SSH password shared in chat
should be rotated. The local WSL Docker host is also shared with unrelated
projects (ports 8000, 8080, 8088, 18080, 18443 are taken): this project uses
5432, 1883, 19092, Keycloak 8180, API 8100, scenario control 8101, Vite 5173.
Nothing of this project's UI stack is deployed anywhere but this machine.

## Open decisions and required user information

- Submission deadline, upload format, GitHub access expectations, and rehearsal
  availability are not recorded for Project 2.
- Target hardware/OS and whether a real cloud account is required are unknown.
- Actual traffic controllers, camera feeds, CAD/AVL systems, public alert systems,
  and physical sensors are not authorized or assumed available.
- Confirm whether the initial map should remain a synthetic 12-intersection grid
  or model a named public area using redistributable map data.

## Durable handover rules

At every pause, record the last completed command, changed files, verification,
blockers, and exact resume action here after updating `TASK_REGISTER.md`. Keep
facts current and delete superseded current-state text rather than accumulating
contradictory summaries.
