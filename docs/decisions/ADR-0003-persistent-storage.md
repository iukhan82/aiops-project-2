# ADR-0003: Persistent storage engines

- **Status:** Accepted
- **Date:** 2026-09-18
- **Deciders:** ARCH, with DATA/BACKEND as implementing owners

## Context

The platform needs authoritative storage for topology, devices, incidents,
commands, emergency workflow, and audit records, plus efficient storage for
high-volume time-stamped measurements and aggregates
(`docs/REFERENCE_ARCHITECTURE.md` section 3). Geometry (roads, lanes,
intersections, emergency routes) requires real spatial query support, not
approximate bounding boxes.

## Decision drivers

- Strong consistency and constraint enforcement for commands, incidents, and
  audit trails, where idempotency and append-only integrity are safety
  properties (`docs/PROJECT_CONTEXT.md`).
- Native geospatial types/operators for routing, geometry versioning, and
  proximity queries (P02.04, P07.02).
- Workstation budget: 0.75 CPU/1,024 MiB for the primary database
  (`docs/environment/RESOURCE_BUDGET.md`).
- Avoid multiplying stateful systems beyond what the assessment and the
  measured budget can support.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **PostgreSQL + PostGIS** | Mature ACID relational store, first-class geospatial types/indexes (GiST), strong migration/testing tooling, single engine covers topology + control-record consistency + geometry | Not purpose-built for very high-cardinality time-series without extension/partitioning discipline |
| MongoDB with geospatial indexes | Flexible schema, adequate 2dsphere geo queries | Weaker multi-record transactional guarantees for command/approval/audit workflows that need strict state-machine consistency; adds a second query paradigm for no required benefit here |
| Elasticsearch with geo_point | Strong search/aggregation, geo queries | Not an authoritative system of record for transactional command/audit data; eventual-consistency indexing model is a poor fit for approval/execution ordering |
| Dedicated time-series database (InfluxDB) as the *only* store | Excellent write throughput for raw telemetry | No relational/geospatial integrity for topology, incidents, commands; would still need a second engine for authoritative records, doubling operational surface |

## Decision

PostgreSQL with the PostGIS extension is the single authoritative relational
and geospatial store for topology, devices, incidents, commands, emergency
workflow, configuration metadata, and audit references. High-volume raw
measurements and rollups use TimescaleDB (a PostgreSQL extension, not a
separate engine) where partition/compression behavior is measured to need
it; otherwise partitioned PostgreSQL tables suffice at the project's scale.
This keeps exactly one primary database engine, consistent with the
resource-budget line item in `docs/environment/RESOURCE_BUDGET.md`.
Object storage (model artifacts, reports, evidence) and Redis (caches,
leases, transient coordination only) remain as stated in
`docs/REFERENCE_ARCHITECTURE.md` section 3 and are not authoritative for
commands or incidents.

## Consequences

- All schema changes go through ordered, checksummed migrations under
  `source-code/database/` (P05.04).
- A measured decision on TimescaleDB versus plain partitioned tables is
  deferred to P05.04/P06.01 once real telemetry volume is observed; this ADR
  fixes the engine family (PostgreSQL), not that sub-choice.
- Redis must never become the system of record for command or incident
  state, per the existing architecture invariant.

## Security/privacy/safety effects

Scoped database roles per service, TLS on the PostgreSQL wire protocol, and
migration-enforced constraints support least privilege and prevent
inconsistent command/incident state (`docs/PROTOCOLS_AND_STANDARDS.md`).
Audit records require append-only protection at the schema/role level
(P09.05).

## Revision triggers

- Measured telemetry write/query load exceeds partitioned-PostgreSQL
  capacity under P11.06 load testing, which would trigger adopting
  TimescaleDB compression/continuous aggregates or a dedicated time-series
  engine as a documented follow-up ADR.

## Related requirements

RQ10, P02.04, P05.04-P05.10.
