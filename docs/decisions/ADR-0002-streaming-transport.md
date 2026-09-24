# ADR-0002: Streaming and messaging transport

- **Status:** Accepted
- **Date:** 2026-09-18
- **Deciders:** ARCH, with DATA/SEC as implementing owners

## Context

The platform needs two distinct transports: constrained edge-to-gateway
uplink from many simulated devices, and a durable, replayable central event
backbone feeding state, analytics, incident, and AIOps services
(`docs/REFERENCE_ARCHITECTURE.md` section 1). The assessment mandates MQTT
and a Kafka-compatible stream explicitly (RQ15-class tooling requirements,
`docs/PROTOCOLS_AND_STANDARDS.md`).

## Decision drivers

- Assessment-mandated protocol family (MQTT + Kafka-compatible).
- Durable, ordered, replayable delivery with consumer offsets, required for
  exactly-once-by-meaning ingestion (P05.05) and AIOps correlation (P10.07).
- QoS and per-device identity/ACL support for constrained edge publishers
  operating over an unreliable uplink with durable offline buffering (P04.07).
- Workstation resource budget: no more than 0.25 CPU/384 MiB for the
  edge-facing broker and 0.75 CPU/1,024 MiB for the central backbone
  (`docs/environment/RESOURCE_BUDGET.md`).
- Self-hostable without a managed-cloud dependency, consistent with the local
  K3s assessment profile.

## Options considered

### Edge uplink transport

| Option | Pros | Cons |
|---|---|---|
| **MQTT (Mosquitto/EMQX) with mTLS, QoS 1, topic ACLs** | Lightweight, purpose-built for constrained/intermittent devices, per-device identity via client certificates, broad IoT tooling support, assessment-mandated | Broker itself is a single logical trust point per edge zone; needs topic-ACL discipline to prevent cross-device access (P05.02) |
| CoAP | Very low overhead, good for extremely constrained devices | No mainstream durable broker/ACL ecosystem as mature as MQTT for this use case; adds a second protocol stack for no measured benefit at simulated-device scale |
| Plain HTTPS POST per event | Simple, reuses REST tooling | No native pub/sub, QoS, or broker-side backpressure; polling or per-event connection overhead is worse for constant telemetry streams |

### Central event backbone

| Option | Pros | Cons |
|---|---|---|
| **Kafka-compatible broker (Kafka or Redpanda), partitioned by device/corridor key** | Durable log with replay/offsets, partition ordering, wide consumer ecosystem, assessment-mandated; Redpanda reduces JVM/ZooKeeper operational load for the workstation budget when Kafka-protocol compatibility is documented | Requires partition-key and retention discipline to bound storage (`docs/environment/RESOURCE_BUDGET.md`) |
| RabbitMQ | Simple, flexible routing | Not Kafka-protocol compatible, weaker native replay/offset story for AIOps correlation and long-window incident review |
| NATS JetStream | Lightweight, good latency | Smaller ecosystem for Kafka-style consumer-group replay tooling used across analytics/incident/AIOps services; does not satisfy the assessment's Kafka-compatible requirement |

## Decision

MQTT (QoS 1, mTLS, per-device identity, topic ACLs) for edge-to-gateway
uplink and command acknowledgement. A Kafka-compatible broker (Kafka or
Redpanda, with protocol compatibility documented before substitution) as the
sole central event backbone, partitioned by device/corridor key, feeding all
central services from `docs/REFERENCE_ARCHITECTURE.md` section 2.

## Consequences

- A gateway service bridges validated MQTT messages into the Kafka-compatible
  backbone with application-level acknowledgement (P05.03); no service
  consumes MQTT directly except the gateway.
- Retention, partition count, and consumer-group design must be fixed before
  P05 acceptance and bounded per `docs/environment/RESOURCE_BUDGET.md`.
- Plain MQTT (1883) stays disabled outside isolated test fixtures per
  `docs/environment/TOPOLOGY.md`.

## Security/privacy/safety effects

Per-device mTLS identity and topic ACLs prevent one simulated device from
publishing or subscribing outside its own scope (P05.02). Broker outage must
not silently drop accepted telemetry; edge durable outbox (P04.07) and
gateway acknowledgement carry the availability guarantee instead.

## Revision triggers

- Measured broker resource use exceeds the budget under nominal load (P11.06).
- The assessor requires a specific managed Kafka service, which would need a
  separate hybrid-placement decision (see ADR-0007).

## Related requirements

RQ15 (K3s/MicroK8s plus required tooling), P05.01-P05.05.
