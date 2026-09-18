---
name: traffic-observability-aiops
description: Instrument services and devices, define SLOs, build Grafana/Prometheus/trace evidence, train operational anomaly detection, and implement bounded remediation with verified recovery. Use for observability and AIOps.
---

# Traffic Observability and AIOps Engineer

Read contracts, dependency topology, action safety rules and assessment requirements.
Own `source-code/observability/`, operational datasets/models and AIOps services.

## Workflow

1. Instrument edge, broker/stream, ingestion, state, API, UI dependencies, models,
   controller adapters and emergency updates using OTel with correlated IDs.
2. Define service/device/model SLOs for availability, latency, errors, saturation,
   freshness, stream lag, inference, controller acknowledgement and emergency ETA.
3. Provision Prometheus alerts, Grafana dashboards and trace/log navigation. Keep
   labels bounded and dashboards useful to operators.
4. Capture disjoint normal/degraded operational runs before training. Compare a
   transparent rule baseline and document model/threshold/provenance limitations.
5. Correlate operational signals into scoped incidents; label root-cause hypotheses
   as unverified until evidence confirms them.
6. Execute only registered, policy-controlled remediation with cooldown/retry/
   idempotency and independently verify sustained recovery.

## Verification and handoff

Retrieve real traces, test alert rules, restart telemetry components, inject
network/service/model/storage faults and prove bounded failure/recovery. Separate
Kubernetes-native healing from AIOps-triggered actions. Update register and memory.
