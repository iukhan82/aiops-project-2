# SLOs and platform dependency topology

Task P10.02. Ten service-level objectives (`source-code/backend/slos.json`),
each a real `contracts/slo-definition/v1` record, and the real platform
dependency graph (`source-code/backend/topology.json`). Both are checked
against real sources by `source-code/backend/verify_slos.py`, not
hand-maintained and left to drift:

- every SLO validates against the real schema;
- every `related_acceptance_target_id` names a real row in
  `docs/requirements/ACCEPTANCE_TARGETS.md`;
- every metric-backed SLO's referenced metric name is grepped out of the
  real source file that emits it (P10.01's instrumentation) - a query
  cannot silently name a metric nobody produces;
- every topology edge's declared dependency is grepped out of the real
  source file that makes the real connection;
- LAT-02 (edge-to-central ingest P95 < 2 s) is measured directly, not left
  as a target: **27.8 ms** (n=20) against the real running stack - see
  `docs/requirements/ACCEPTANCE_TARGETS.md`.

## What "measurable" means before P10.03 exists

No Prometheus/Tempo/Grafana is provisioned yet (P10.03). Every SLO's
`measurement_query_ref` is real PromQL-shaped text naming a metric this
codebase genuinely emits today via `backend/observability.py` /
`edge/observability.py` - inspectable now, executable once P10.03 points a
real collector at these processes. Two SLOs (command acknowledgement,
emergency ETA accuracy) have no live metric yet; their `measurement_query_ref`
instead points at the real evidence file that already measured them
directly (P07.09, P07.10) - carried forward honestly rather than inventing
a metric ahead of a collector that could query it.

## SLOs

| SLO | Service | Type | Target | Window | Acceptance target |
|---|---|---|---|---|---|
| edge-model-inference-latency-p95 | edge-runtime | latency | < 100 ms | 60 s | LAT-01 |
| edge-pending-devices-saturation | edge-runtime | saturation | < 70 | 60 s | - |
| ingestion-ingest-latency-p95 | ingestion | latency | < 2000 ms | 300 s | LAT-02 |
| ingestion-rejection-rate | ingestion | error_rate | < 5% | 300 s | - |
| network-state-freshness-share | api | freshness | > 95% | 300 s | SAFE-03 |
| api-http-latency-p95 | api | latency | < 2000 ms | 300 s | - |
| api-http-error-rate | api | error_rate | < 1% | 300 s | - |
| api-availability | api | availability | > 99% | 24 h | - |
| command-adapter-acknowledgement-p95 | control | acknowledgement | < 3000 ms | 1 h | LAT-04 |
| emergency-route-eta-accuracy | control | eta_accuracy | < 15% | 1 h | ETA-01 |

## Platform dependency topology

Five processes this repository can run today, and the real infrastructure
each one connects to (grepped, not asserted):

```
edge-runtime            (no drain-to-MQTT wired yet - a real, undrawn gap)

gateway       --> mosquitto (mTLS)
gateway       --> kafka

ingestion     --> kafka
ingestion     --> postgres

api           --> postgres
api           --> opa
api           --> keycloak

scenario-control --> postgres
scenario-control --> opa
scenario-control --> keycloak
```

P11.01's own backend-worker and frontend images extend this graph (adding
`executor_worker`/`verifier_worker` -> postgres + simulator adapters,
`frontend` -> `api`) without changing its shape; out of scope for this
task, which covers what can run as a separate process today.
