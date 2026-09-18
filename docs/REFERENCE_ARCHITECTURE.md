# Reference Architecture

## 1. Logical architecture

```text
Sensors / simulated devices / external feeds
  |-- traffic, pedestrian, cyclist, signal, weather, road condition
  |-- transit, parking, events, emergency CAD/AVL, operator reports
  v
Edge sites at intersections or corridors
  |-- protocol adapters and schema validation
  |-- privacy-preserving video/radar feature extraction
  |-- local inference and rules
  |-- durable outbox and safe offline behavior
  v
Secure edge gateway (MQTT/mTLS)
  v
Central event backbone (Kafka/Redpanda + schema governance)
  |-- state and geospatial processing
  |-- traffic and safety incident correlation
  |-- forecasts and optimization
  |-- emergency routing and coordination
  |-- command/policy/outcome workflow
  |-- AIOps correlation and remediation
  v
PostgreSQL/PostGIS | time-series store | object/model store | cache
  v
Operator console | dispatcher view | field view | analytics | public APIs
  v
Simulation adapters first; approved signal/VMS/CAD integrations later
```

## 2. Service boundaries

| Service | Responsibility |
|---|---|
| Device registry | Device identity, type, location, ownership, capability, certificate, configuration |
| Edge runtime | Adapters, validation, feature extraction, inference, buffering, health |
| Event gateway | Authenticated ingest, quotas, schema enforcement, edge acknowledgements |
| Network state | Current intersection/segment/lane state with freshness and provenance |
| Geospatial service | Road graph, geometry versions, routing restrictions, map layers |
| Traffic analytics | Aggregation, queues, travel time, congestion, forecasts |
| Safety detector | Collision/hazard/conflict candidates and supporting evidence |
| Incident service | Correlation, lifecycle, ownership, timeline, escalation, review |
| Emergency service | CAD/AVL abstraction, units, routes, ETA, staging, agency coordination |
| Optimization service | Candidate signal/diversion/priority plans and predicted outcomes |
| Command service | Request, approval, policy, execution, acknowledgement, rollback |
| Outcome verifier | Independent pre/post metrics, sustained recovery, effectiveness result |
| Notification service | Agency and public messages, acknowledgement, correction, expiry |
| Model service | Registry, evaluation, activation, shadowing, drift, rollback |
| Audit service | Append-only security, incident, action, policy, and access evidence |
| AIOps engine | Service/device/model incidents, dependency correlation, bounded remediation |
| Staff API/BFF | Role-tailored APIs and real-time UI subscriptions |

## 3. Data stores

- PostgreSQL/PostGIS for authoritative topology, devices, incidents, commands,
  emergency workflow, configuration metadata, and audit references.
- A time-series-capable store for high-volume measurements and aggregates.
- Object storage for approved model artifacts, reports, snapshots, and limited
  evidence objects.
- Event backbone retention for replayable streams with bounded retention.
- Edge SQLite or equivalent durable outbox per device/site.
- Redis only for reproducible caches, leases, or transient coordination; never as
  the sole record for commands or incidents.

## 4. Key contracts

- Device and capability registration.
- Observation/event envelope.
- Signal SPaT/MAP and controller state.
- Network state and forecast.
- Safety candidate and correlated incident.
- Emergency call, unit, assignment, route, and status.
- Recommendation with alternatives, uncertainty, constraints, and evidence.
- Command request, approval, policy decision, acknowledgement, rollback, and
  outcome verification.
- AIOps telemetry, platform incident, remediation, and recovery.

All contracts require semantic versioning, valid/invalid examples, compatibility
tests, timestamps, units, geometry version, provenance, confidence, and privacy
classification.

## 5. Command safety path

```text
Evidence -> recommendation -> operator request -> policy evaluation
         -> optional second approval -> command with expiry/idempotency
         -> adapter acknowledgement -> observed state confirmation
         -> independent outcome window -> effective/ineffective/unsafe/unknown
         -> rollback or escalation when required
```

The recommendation service cannot call infrastructure adapters directly. The
executor rechecks authorization, current conditions, command age, safe limits,
and target identity at execution time.

## 6. Availability and degraded modes

- Edge sites keep a safe static signal plan and buffer telemetry while offline.
- The UI shows last update and stale state; it never displays cached data as live.
- Loss of prediction falls back to measured state and rule baselines.
- Loss of policy or identity blocks new high-impact commands.
- Loss of public-notification adapters preserves the incident and retries within
  bounded rules while exposing the failure to operators.
- Recovery reconciles observed controller state before accepting new commands.

## 7. Deployment profiles

1. **Developer:** Docker Compose, small SUMO map, single edge simulator.
2. **Integrated acceptance:** K3s, multiple simulated edge sites, full security,
   observability, event backbone, databases, and failure testing.
3. **Proposed production:** multi-zone central services, geographically distributed
   edge sites, redundant gateways, external secrets, backups, disaster recovery,
   and certified controller adapters. This profile is designed, not claimed as
   deployed by the academic project.
