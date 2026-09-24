# Threat model

Task P09.01. Source of truth: `source-code/security/threat_model.json` (boundaries, assets, threats) and
`source-code/security/controls.json` (the control catalogue). The tables below are generated from those files by
`source-code/security/verify_threat_model.py --render`, so this document cannot say something the data does not. The verifier also checks
that every control marked implemented is backed by evidence that passes today. The residual-risk view is `RISK_REGISTER.md`.

This is a design and evidence record for an academic prototype. It does not claim compliance with, or certification to, any standard;
`CONTROL_MAPPING.md` (P09.07) maps controls to frameworks and says where the evidence stops.

## Scope and method

- **In scope:** everything the platform does from device to operator and back to a controller adapter: devices and edge sites, the MQTT
  broker and gateway, the event stream and ingestion, the data stores and audit, the staff API and live feed, the browser console, the
  identity provider, the policy decision point, the command path and adapters, models and datasets, the build and supply chain, the people
  behind the roles, external integrations, the demo control service, AIOps remediation, the cluster/host runtime and the observability
  pipeline.
- **Boundaries** come from `diagrams/sources/05-security-trust-boundaries.mmd` (trust zones TZ0-TZ8) plus the areas the security baseline
  requires (`docs/SECURITY_COMPLIANCE_BASELINE.md`). The verifier fails if a zone in the diagram has no boundary or no threat, or if any of
  the thirteen required areas has fewer than two threats.
- **Categories:** STRIDE (spoofing, tampering, repudiation, information disclosure, denial of service, elevation of privilege) plus three
  the platform needs: *safety* (a physical or operational harm to road users, whatever its cause), *AI* (model validity, drift, fairness,
  over-reliance) and *supply chain*.
- **Two impact scales, kept apart.** Safety impact is what happens to people on the road; security impact is what happens to information
  and services. A threat can be low on one and high on the other (a stale screen is a safety threat with modest information impact; a
  leaked location history is the reverse). The register shows both residuals.
- **Scoring.** Likelihood (1-3) times the larger impact (1-3). Only an *implemented* control lowers likelihood (one step each, at most
  two); a partial or planned control earns nothing, so a gap can never quietly look like protection.
- **Accepted risks** carry a written rationale and remain in the register.

## Assumptions

1. Devices, calls, units, traffic and the SUMO network are simulated. No real controller, CAD, public-alert or V2X system is connected and
   none is authorized. Threats against those (B14) are recorded so a real integration starts from a threat model, and two are explicitly
   accepted because the adapter does not exist.
2. The platform network is not assumed trustworthy: device-to-broker is mutually authenticated today; the links inside the platform are
   plain until P09.04, and the model says so (CTL-11, CTL-12).
3. A human decides. Nothing the platform recommends reaches a controller without a person requesting it, a different person approving it
   (for the classes that need two), a policy check at that moment, and the executor service - never a person - performing it.
4. The auditor role can read but never change anything; the demo operator can start scenarios but cannot reach anything operational.

## Data flow and where the boundaries are

```text
devices/edge --mTLS 8883--> MQTT broker/gateway --> event stream --> ingestion --> PostgreSQL/PostGIS + audit
                                                                         |
operator console --OIDC (PKCE), bearer token--> staff API --policy--> command path --> executor --> simulator adapters
        ^                                          |                                          |
        |                                          +--> identity provider (Keycloak)          +--> outcome verifier (independent)
        +--live feed (WebSocket, token in query)---+
demo operator --token--> scenario-control service (own identity, own audit, no route to the above)
```

The command path is the highest-consequence flow and has the densest treatment: recommendation bounds (CTL-20), request and approval
separation (CTL-05), execution-time policy (CTL-06), a single executor identity (CTL-07), expiry and idempotency (CTL-24), independent
verification with rollback (CTL-21) and an independent signal-safety monitor (CTL-22).

## Boundaries

<!-- BEGIN GENERATED: boundaries -->

| Boundary | Zone | Area | Threats | What it is |
|---|---|---|---|---|
| B01 Devices and sensors | TZ0 | device | 5 | Simulated loop detectors, signal controllers, weather, road-condition, pedestrian and cycle sensors, and the AVL units of emergency vehicles; in a real deployment, roadside equipment. |
| B02 Edge runtime and local models | TZ0 | edge | 4 | The edge container that validates, extracts features, runs the ONNX model or baseline, and keeps a durable outbox; no camera frame leaves it. |
| B03 MQTT broker and gateway | TZ1 | broker | 4 | The mTLS MQTT listener with per-device identity and topic ACLs, and the gateway that validates events and forwards them to the stream. |
| B04 Event backbone and ingestion | TZ2 | stream | 4 | The Kafka-compatible stream and the consumer that validates and persists observations exactly once by meaning. |
| B05 Data stores and audit evidence | TZ6 | data | 7 | PostgreSQL/PostGIS records (topology, telemetry, incidents, commands, outcomes, calls) and the append-only audit and history tables. |
| B06 Staff API and live feed | TZ3 | API | 8 | The FastAPI service that answers the operator console: REST, WebSocket, authorization, workflow endpoints and the governance endpoints. |
| B07 Operator web application | TZ7 | web | 5 | The browser console: sign-in, live map, dispatch, approvals, audit, platform status. |
| B08 Identity provider | TZ5 | identity | 5 | Keycloak: realm, roles, demo identities, the browser client and the service clients. |
| B09 Policy decision point | TZ5 | policy | 3 | The execution-time command policy today and Open Policy Agent (P09.03): who may do what to which target, now. |
| B10 Command path and controller adapters | TZ4 | controller adapter | 7 | Request, approval, the executor worker, the verifier worker and the simulator signal, diversion and sign adapters. |
| B11 Models and datasets | TZ3 | model | 4 | Training data, evaluation ledgers, ONNX artifacts, forecast and detector packages. |
| B12 Build, dependencies and images | CI | supply chain | 3 | Source repository, Python and Node dependencies, base images, CI workflows and release artifacts. |
| B13 Human operators and administrators | TZ7 | operator | 3 | The people behind the roles: operators, supervisors, dispatchers, incident commanders, auditors, field responders and platform administrators. |
| B14 External integrations | EXT | external integration | 3 | CAD/AVL feeds, public-warning channels, transit and V2X feeds. All simulated today; none is authorized as a real connection. |
| B15 Demo and scenario control | TZ8 | demo | 2 | The separate scenario-control service and the demo operator identity. |
| B16 AIOps engine and remediation workers | TZ3 | aiops | 2 | Platform-incident correlation and bounded remediation (Phase 10). |
| B17 Cluster and host runtime | TZ3 | runtime | 5 | Kubernetes or Docker hosts, container runtime, network and secrets storage (Phase 11). |
| B18 Observability pipeline | TZ3 | observability | 2 | Logs, metrics and traces and their storage and dashboards (Phase 10). |

<!-- END GENERATED: boundaries -->

## Assets

<!-- BEGIN GENERATED: assets -->

| ID | Asset | Class |
|---|---|---|
| A01 | Safe operation of signals and the road-user safety it protects | safety-critical |
| A02 | Command records, approvals and outcomes | integrity |
| A03 | Emergency call and unit data (no patient or medical data is held) | confidential |
| A04 | Telemetry and network state | integrity |
| A05 | Location and trajectory data | privacy |
| A06 | Camera-derived features | privacy |
| A07 | Credentials, tokens and private keys | confidential |
| A08 | Audit evidence | integrity |
| A09 | Models, datasets and evaluation ledgers | integrity |
| A10 | Policy rules and authorization data | integrity |
| A11 | Availability of the platform and its identity, policy and data services | availability |
| A12 | Configuration, device registry and inventory | integrity |
| A13 | Source, dependencies, images and release artifacts | integrity |
| A14 | The truthfulness of what an operator sees (freshness, source, identity) | safety-critical |

<!-- END GENERATED: assets -->

## Threats by boundary

<!-- BEGIN GENERATED: threats -->

#### B01 Devices and sensors (TZ0)

| ID | Category | Threat | Safety | Security | Likelihood | Treated by | State |
|---|---|---|---|---|---|---|---|
| T-01 | Spoofing | A device is impersonated to inject false readings | medium | medium | medium | CTL-09, CTL-10 | treated |
| T-02 | Tampering | Captured telemetry is replayed | low | medium | medium | CTL-10, CTL-09 | treated |
| T-03 | Tampering | A device is stolen or opened and its key extracted | low | medium | low | CTL-09, CTL-39 | partially treated |
| T-04 | Information disclosure | Camera-derived data or tracks identify individuals | none | high | medium | CTL-19, CTL-18 | treated |
| T-05 | Denial of service | A faulty or compromised device floods the platform | medium | medium | medium | CTL-10, CTL-33 | treated |

#### B02 Edge runtime and local models (TZ0)

| ID | Category | Threat | Safety | Security | Likelihood | Treated by | State |
|---|---|---|---|---|---|---|---|
| T-06 | Tampering | A tampered model is activated at an edge site | medium | high | low | CTL-25 | treated |
| T-07 | Tampering | The edge outbox is altered on disk | low | medium | low | CTL-31, CTL-41, CTL-10 | treated |
| T-08 | Denial of service | The edge loses the network, identity or central services | high | medium | medium | CTL-41, CTL-23, CTL-06 | treated |
| T-09 | Information disclosure | Raw camera frames or intermediate data are retained locally | none | high | low | CTL-19, CTL-31 | treated |

#### B03 MQTT broker and gateway (TZ1)

| ID | Category | Threat | Safety | Security | Likelihood | Treated by | State |
|---|---|---|---|---|---|---|---|
| T-10 | Spoofing | A device publishes under another device's identity or topic | medium | high | medium | CTL-09 | treated |
| T-11 | Information disclosure | A client subscribes to other devices' topics | low | medium | medium | CTL-09 | treated |
| T-12 | Denial of service | The broker or gateway is flooded or a consumer stalls | medium | medium | medium | CTL-10, CTL-33, CTL-42 | treated |
| T-13 | Spoofing | A certificate from an untrusted authority is accepted | medium | high | low | CTL-09 | treated |

#### B04 Event backbone and ingestion (TZ2)

| ID | Category | Threat | Safety | Security | Likelihood | Treated by | State |
|---|---|---|---|---|---|---|---|
| T-14 | Tampering | A poisoned or conflicting event overwrites a stored observation | medium | high | medium | CTL-10 | treated |
| T-15 | Repudiation | Redelivery duplicates or reorders events | low | medium | high | CTL-10 | treated |
| T-16 | Information disclosure | An unauthorized consumer reads the stream | none | medium | medium | CTL-11, CTL-13 | open |
| T-17 | Denial of service | Ingestion lag hides fresh data behind old data | medium | medium | medium | CTL-42, CTL-23 | treated |

#### B05 Data stores and audit evidence (TZ6)

| ID | Category | Threat | Safety | Security | Likelihood | Treated by | State |
|---|---|---|---|---|---|---|---|
| T-18 | Tampering | Audit or history rows are rewritten to hide an action | medium | high | low | CTL-15 | treated |
| T-19 | Repudiation | A person denies having requested, approved or denied a command | medium | high | medium | CTL-15, CTL-05, CTL-36 | partially treated |
| T-20 | Information disclosure | Database credentials are read from configuration or the process environment | medium | high | medium | CTL-14, CTL-11, CTL-12 | open |
| T-21 | Information disclosure | Audit details or exports carry tokens, identifiers or locations | none | high | medium | CTL-16, CTL-17 | treated |
| T-22 | Tampering | SQL injection through an API parameter | medium | high | low | CTL-32 | treated |
| T-23 | Denial of service | Data is lost with no restorable backup | medium | high | low | CTL-35 | open |
| T-24 | Information disclosure | Location or trajectory data is kept beyond its purpose | none | medium | medium | CTL-18 | treated |

#### B06 Staff API and live feed (TZ3)

| ID | Category | Threat | Safety | Security | Likelihood | Treated by | State |
|---|---|---|---|---|---|---|---|
| T-25 | Spoofing | A forged, expired or wrong-audience token is accepted | high | high | medium | CTL-01 | treated |
| T-26 | Elevation of privilege | A role calls a capability it does not hold | high | high | medium | CTL-03, CTL-04 | treated |
| T-27 | Elevation of privilege | A user of one agency reads or changes another agency's records | medium | high | medium | CTL-47 | open (accepted) |
| T-28 | Information disclosure | Responses carry more than the caller needs | none | medium | medium | CTL-16, CTL-17 | treated |
| T-29 | Denial of service | A request flood or oversized body exhausts the API | medium | medium | medium | CTL-33 | treated |
| T-30 | Tampering | Mass assignment: a client names its own author, requester or status | medium | high | medium | CTL-32, CTL-03 | treated |
| T-31 | Information disclosure | The access token in the WebSocket URL is logged or cached | low | medium | medium | CTL-01, CTL-37 | partially treated (accepted) |
| T-32 | Elevation of privilege | A new endpoint ships without an authorization decision | high | high | medium | CTL-03 | treated |

#### B07 Operator web application (TZ7)

| ID | Category | Threat | Safety | Security | Likelihood | Treated by | State |
|---|---|---|---|---|---|---|---|
| T-33 | Tampering | Cross-site scripting steals or misuses the session | high | high | low | CTL-43, CTL-33 | treated |
| T-34 | Tampering | The console is framed or spoofed by another origin | high | high | low | CTL-33, CTL-02 | treated |
| T-35 | Safety | The screen shows old or cached data as if it were live | high | medium | medium | CTL-23, CTL-42 | treated |
| T-36 | Spoofing | Phishing or an open redirect during sign-in | medium | high | low | CTL-02, CTL-43 | treated |
| T-37 | Safety | A demonstration screen or simulated data is mistaken for real operations | medium | low | medium | CTL-08, CTL-23 | treated |

#### B08 Identity provider (TZ5)

| ID | Category | Threat | Safety | Security | Likelihood | Treated by | State |
|---|---|---|---|---|---|---|---|
| T-38 | Spoofing | Passwords are guessed or stuffed against operator accounts | high | high | medium | CTL-02 | treated |
| T-39 | Elevation of privilege | A client or redirect misconfiguration leaks tokens or codes | high | high | low | CTL-02 | treated |
| T-40 | Denial of service | The identity provider is down when tokens expire | medium | medium | medium | CTL-01, CTL-23 | treated |
| T-41 | Tampering | The realm drifts from the reviewed configuration | medium | high | medium | CTL-02 | treated |
| T-42 | Information disclosure | The administrator console or its credentials are exposed | medium | high | low | CTL-14 | open |

#### B09 Policy decision point (TZ5)

| ID | Category | Threat | Safety | Security | Likelihood | Treated by | State |
|---|---|---|---|---|---|---|---|
| T-43 | Elevation of privilege | A protected action skips the policy decision | high | high | medium | CTL-03, CTL-04, CTL-06 | treated |
| T-44 | Safety | The policy engine fails open | high | high | low | CTL-06, CTL-04 | treated |
| T-45 | Tampering | The policy rules or their data are altered | high | high | low | CTL-04, CTL-29, CTL-03 | treated |

#### B10 Command path and controller adapters (TZ4)

| ID | Category | Threat | Safety | Security | Likelihood | Treated by | State |
|---|---|---|---|---|---|---|---|
| T-46 | Tampering | A command is aimed at a target it was not approved for | high | high | medium | CTL-06, CTL-24 | treated |
| T-47 | Elevation of privilege | A requester approves their own command, or two low roles collude | high | high | medium | CTL-05 | treated |
| T-48 | Safety | An old approved command is replayed | high | medium | medium | CTL-24, CTL-06 | treated |
| T-49 | Safety | A signal change creates an unsafe state | high | medium | low | CTL-20, CTL-22 | treated |
| T-50 | Safety | An action makes things worse and is not undone | high | low | medium | CTL-21 | treated |
| T-51 | Elevation of privilege | A person or another service reaches an adapter | high | high | low | CTL-07, CTL-08 | treated |
| T-52 | Safety | The adapter is unreachable or does not acknowledge | medium | low | medium | CTL-21, CTL-06 | treated |

#### B11 Models and datasets (TZ3)

| ID | Category | Threat | Safety | Security | Likelihood | Treated by | State |
|---|---|---|---|---|---|---|---|
| T-53 | AI | Training data is poisoned or leaks into evaluation | medium | medium | low | CTL-26 | treated |
| T-54 | AI | A model drifts or is fooled by unusual input | medium | medium | medium | CTL-44, CTL-20, CTL-21, CTL-25 | partially treated |
| T-55 | AI | Service quality is unequal across areas or road users | medium | medium | medium | CTL-45 | open |
| T-56 | AI | Operators over-rely on a recommendation | high | low | medium | CTL-05, CTL-20, CTL-21 | treated |

#### B12 Build, dependencies and images (CI)

| ID | Category | Threat | Safety | Security | Likelihood | Treated by | State |
|---|---|---|---|---|---|---|---|
| T-57 | Supply chain | A malicious or vulnerable dependency is installed | medium | high | medium | CTL-27, CTL-28, CTL-29 | partially treated |
| T-58 | Supply chain | A tampered image or artifact is deployed | medium | high | low | CTL-29, CTL-27 | partially treated |
| T-59 | Supply chain | A secret is committed or a CI token is abused | low | high | medium | CTL-14, CTL-28 | open |

#### B13 Human operators and administrators (TZ7)

| ID | Category | Threat | Safety | Security | Likelihood | Treated by | State |
|---|---|---|---|---|---|---|---|
| T-60 | Elevation of privilege | An insider approves harmful commands | high | high | low | CTL-05, CTL-06, CTL-21, CTL-15 | treated |
| T-61 | Repudiation | An override is used without a record | high | high | low | CTL-34, CTL-15 | treated |
| T-62 | Elevation of privilege | Accounts keep roles they no longer need | medium | medium | medium | CTL-46, CTL-02 | partially treated |

#### B14 External integrations (EXT)

| ID | Category | Threat | Safety | Security | Likelihood | Treated by | State |
|---|---|---|---|---|---|---|---|
| T-63 | Spoofing | A forged CAD or AVL feed creates false calls or moves units | medium | medium | low | CTL-40, CTL-10 | treated (accepted) |
| T-64 | Tampering | A false public warning is sent through a notification channel | high | high | low | CTL-40 | treated (accepted) |
| T-65 | Information disclosure | An integration credential leaks | low | medium | low | CTL-14, CTL-40 | partially treated |

#### B15 Demo and scenario control (TZ8)

| ID | Category | Threat | Safety | Security | Likelihood | Treated by | State |
|---|---|---|---|---|---|---|---|
| T-66 | Elevation of privilege | Demo controls reach the operational command path | high | high | low | CTL-08 | treated |
| T-67 | Repudiation | Demo activity is mixed into the operational audit trail | low | medium | low | CTL-08 | treated |

#### B16 AIOps engine and remediation workers (TZ3)

| ID | Category | Threat | Safety | Security | Likelihood | Treated by | State |
|---|---|---|---|---|---|---|---|
| T-68 | Safety | A wrong or runaway remediation worsens an outage | medium | medium | medium | CTL-38 | open |
| T-69 | Elevation of privilege | The remediation identity is used to reach traffic actions | high | high | low | CTL-07, CTL-38, CTL-02 | partially treated |

#### B17 Cluster and host runtime (TZ3)

| ID | Category | Threat | Safety | Security | Likelihood | Treated by | State |
|---|---|---|---|---|---|---|---|
| T-70 | Elevation of privilege | A container escape or over-privileged workload | medium | high | low | CTL-13, CTL-31, CTL-30 | partially treated |
| T-71 | Tampering | Lateral movement between services | medium | high | medium | CTL-13, CTL-11, CTL-12 | open |
| T-72 | Information disclosure | Secrets are readable from cluster or host state | medium | high | medium | CTL-14, CTL-13 | open |
| T-73 | Repudiation | Malicious runtime behaviour goes unnoticed | low | medium | medium | CTL-30 | open |
| T-74 | Denial of service | Resource exhaustion starves safety-critical services | medium | medium | medium | CTL-13, CTL-31 | partially treated |

#### B18 Observability pipeline (TZ3)

| ID | Category | Threat | Safety | Security | Likelihood | Treated by | State |
|---|---|---|---|---|---|---|---|
| T-75 | Information disclosure | Logs, metrics or traces contain tokens or personal identifiers | none | high | medium | CTL-37, CTL-16 | partially treated |
| T-76 | Tampering | Telemetry is poisoned to mislead operators or AIOps | medium | medium | low | CTL-10, CTL-38, CTL-42 | partially treated |


<!-- END GENERATED: threats -->

## Control catalogue

Each control's implementation files and its named evidence are in `source-code/security/controls.json`; the verifier runs the
cross-checks. "Baseline item" is the number in the technical control baseline of `docs/SECURITY_COMPLIANCE_BASELINE.md`.

<!-- BEGIN GENERATED: controls -->

| ID | Layer | Control | State | Task | Baseline item |
|---|---|---|---|---|---|
| CTL-01 | identity | Strict OIDC access-token validation at the API | implemented | P08.04 | 5 |
| CTL-02 | identity | Hardened identity provider: PKCE S256 only, exact redirect URIs, no implicit or password grants, lockout, refresh-token rotation | implemented | P09.02 | 5 |
| CTL-03 | authorization | Default-deny endpoint authorization from one inventory (capability per endpoint, unlisted route denied) | implemented | P08.04 | 3, 4 |
| CTL-04 | authorization | Open Policy Agent as the policy decision point for the API and the command executor, fail closed when it is unavailable | implemented | P09.03 | 3, 7 |
| CTL-05 | authorization | Structural four-eyes and role gates per safety class (requester is never the approver; the role must hold APPROVE for the class) | implemented | P07.05 | 7 |
| CTL-06 | safety | Execution-time policy re-evaluation: expiry, target validity, safe bounds, fresh evidence; an outage never approves | implemented | P07.05 | 7, 15 |
| CTL-07 | authorization | Only system:command-executor executes; no human path reaches an adapter; the verifier is independent of everyone who touched the command | implemented | P08.08 | 1, 7 |
| CTL-08 | authorization | Demo and scenario control isolated: its own service, verified identity, audit trail and no operational reach in either direction | implemented | P08.09 | 1, 3 |
| CTL-09 | transport | MQTT per-device mTLS identity with default-deny topic ACLs | implemented | P05.02 | 1, 2, 3 |
| CTL-10 | data | Validated central ingestion: schema, registered device identity, content-conflict rejection, idempotent replay | implemented | P05.05 | 3, 13 |
| CTL-11 | transport | TLS to the database and between services; service-to-service mutual authentication | planned | P09.04 | 2 |
| CTL-12 | identity | Workload identity for services: each service authenticates as its own Keycloak client, not as a shared database role name | planned | P09.04 | 1, 4 |
| CTL-13 | runtime | Kubernetes RBAC, default-deny network policy, non-root and read-only workloads, admission checks | planned | P11.02 | 3, 4 |
| CTL-14 | operations | Secrets generated at deployment, kept out of Git and out of committed realm/config files, rotated on a schedule | partial | P09.04 | 6 |
| CTL-15 | data | Append-only operator audit and state-transition histories, enforced by the database, not by convention | implemented | P09.05 | 9 |
| CTL-16 | data | Recursive redaction of tokens, credentials, identifiers and locations in audit records, logs, error responses and evidence bundles | implemented | P09.05 | 6, 10 |
| CTL-17 | authorization | Role-limited evidence access: the trail is readable by the auditor role only; exports are redacted and attributed | implemented | P09.05 | 10 |
| CTL-18 | data | Retention windows per class, aggregation before deletion, audit class never purged, active records never purged | implemented | P05.10 | 10 |
| CTL-19 | data | Privacy-preserving edge processing: privacy-zone suppression, per-window ephemeral identity, k-anonymity floor | implemented | P04.06 | 10 |
| CTL-20 | safety | Recommendation safety bounds enforced by dropping over-bound alternatives (first of two independent safety checks) | implemented | P07.04 | 7 |
| CTL-21 | safety | Independent outcome verification with rollback or escalation; a rollback is claimed only when the undo is confirmed | implemented | P07.09 | 7, 15 |
| CTL-22 | safety | Independent signal safety monitor and safe-by-construction pre-emption (only ever shortens the current phase; abort restores the original program) | implemented | P07.07 | 7, 15 |
| CTL-23 | safety | Fail-safe degraded behaviour in the UI: stale data is never shown as live, an unreachable API or identity provider is said plainly | implemented | P08.10 | 15 |
| CTL-24 | safety | Command expiry, idempotency keys and replay protection enforced at the database | implemented | P07.05 | 7 |
| CTL-25 | ai | Model integrity: verified ONNX artifact hash, verify-then-commit activation, rollback to the previous model | implemented | P04.08 | 8 |
| CTL-26 | ai | Dataset integrity: disjoint seed splits with a leakage check, and a test-split ledger that refuses a reopen | implemented | P03.08 | 8 |
| CTL-27 | supply-chain | Pinned dependencies and container images (exact locks; image digests in the platform compose file) | partial | P09.08 | 11 |
| CTL-28 | supply-chain | CI security gates: secret, dependency, IaC and configuration scanning with least-privilege tokens | partial | P01.05 | 11 |
| CTL-29 | supply-chain | SBOM, build provenance, artifact signing and admission verification gate release artifacts | implemented | P09.08 | 11 |
| CTL-30 | runtime | Runtime detection with Falco: scoped rules for unexpected shells, sensitive-file reads and writes to binary directories | partial | P09.09 | 12 |
| CTL-31 | runtime | Non-root, read-only, resource-limited containers for the edge runtime | implemented | P04.09 | 4 |
| CTL-32 | data | Input validation and output handling: closed request schemas, validated cursors and time ranges, escaped search text, no client-named authors | implemented | P08.09 | 13 |
| CTL-33 | authorization | Rate limits, request-size limits, security response headers and strict cross-origin policy at the API | implemented | P09.03 | 13 |
| CTL-34 | safety | Override (incident commander, SC-2 only) with a mandatory justification and independent audit; no override exists for SC-0 or SC-1 | implemented | P09.05 | 7 |
| CTL-35 | operations | Encrypted, tested backups with restore evidence and restricted access | planned | P11.05 | 14 |
| CTL-36 | data | One time source for audit evidence: the database clock stamps every audit and history row | partial | P11.03 | 9 |
| CTL-37 | operations | Telemetry hygiene: bounded low-cardinality metric labels by contract; no credentials, tokens or personal identifiers in logs, metrics or traces | partial | P10.01 | 6, 10 |
| CTL-38 | safety | Bounded, policy-controlled AIOps remediation: registered actions only, approvals, blast-radius limits | planned | P10.08 | 7 |
| CTL-39 | transport | IoT/OT device security profile: unique identity, certificate lifecycle and revocation, secure update and support policy | partial | P11.02 | 1, 8 |
| CTL-40 | authorization | External integrations stay simulated: no real CAD, controller, public-alert or V2X adapter is authorized, so none is connected | implemented | P05.09 | 1, 2 |
| CTL-41 | safety | Durable, ordered, bounded edge outbox and safe offline behaviour (buffer, acknowledge, replay without duplicates) | implemented | P04.07 | 8, 15 |
| CTL-42 | operations | Honest platform status: probes made at request time, a check with no answer is unknown and never healthy, freshness budgets shared with the map | implemented | P08.09 | 12 |
| CTL-43 | identity | Browser token handling: authorization code with PKCE, token held in memory only, the console never asks for a password itself | implemented | P08.04 | 5 |
| CTL-44 | ai | Model drift, confidence-collapse, feature-loss and data-quality monitoring with staged activation | planned | P10.06 | 8 |
| CTL-45 | ai | Performance reported per scenario class and per road user, never blended; geographic and modal fairness review | partial | P12.04 | 8 |
| CTL-46 | operations | Access review and joiner-mover-leaver procedure for operating roles | planned | P09.07 | 4 |
| CTL-47 | authorization | Agency scope on emergency records: a per-user agency attribute checked by policy on every read and change | planned | P09.06 | 3 |

<!-- END GENERATED: controls -->

## Keeping it current

Adding a component means adding a boundary or threats to it and a control or evidence line for each treatment. Finishing a planned control
means flipping its status and adding its evidence in the same change; the verifier then recomputes the residual risk, and refuses a
control that claims to be implemented without passing evidence.
