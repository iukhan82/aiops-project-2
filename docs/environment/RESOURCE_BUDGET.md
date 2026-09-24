# Resource budgets

Budgets are planning limits, not measurements. P11 load/capacity tests will replace
assumptions with observed values.

## Development workstation budget

WSL allocation: 4 CPUs and approximately 11 GiB RAM. Reserve at least 2 GiB for
WSL/system overhead and unrelated shared Docker workloads. Project steady-state
budget: no more than 8 GiB RAM and 3.5 CPU equivalent under nominal load.

| Component group | CPU request budget | Memory limit budget |
|---|---:|---:|
| SUMO and scenario control | 0.75 | 768 MiB |
| Three logical edge runtimes | 0.75 | 1,024 MiB total |
| MQTT plus stream gateway | 0.25 | 384 MiB |
| Kafka-compatible broker | 0.75 | 1,024 MiB |
| PostgreSQL/PostGIS | 0.75 | 1,024 MiB |
| Backend/emergency/control services | 0.75 | 1,280 MiB total |
| Keycloak and OPA | 0.50 | 1,024 MiB total |
| Frontend | 0.10 | 256 MiB |
| OTel/Prometheus/Grafana/Tempo/logs | 0.75 | 1,536 MiB total |
| **Maximum project profile** | **5.35 requested** | **8,320 MiB limits** |

Requests may be oversubscribed because profiles do not run every tool at maximum
simultaneously. Limits must prevent one service from exhausting the workstation.
Use reduced retention, bounded cardinality and small synthetic networks locally.

## Assessment target budget

Planning target: 12 CPUs, 32 GiB RAM and 200 GiB free SSD recommended.

- Reserve 2 CPUs and 6 GiB RAM for OS/K3s/Falco/system services.
- Project requests stay below 8 CPUs and 20 GiB RAM nominal.
- Project aggregate limits stay below 10 CPUs and 24 GiB RAM.
- Reserve at least 30% disk free before acceptance exercises.
- Bound event-stream, telemetry, trace, log, object and database retention.
- Define PVC quotas and storage warning/hard thresholds before full-stack tests.

If actual target has less than 8 CPUs, 16 GiB RAM or 100 GiB free SSD, re-profile
the stack before deployment rather than silently removing mandatory capabilities.

## Edge runtime budget

Initial per logical edge instance:

- 250 millicpu request, 750 millicpu limit.
- 256 MiB request, 512 MiB limit.
- P95 warm inference target below 100 ms on CPU; final target set after benchmark.
- Durable outbox quota starts at 256 MiB per edge with warning at 70% and hard
  handling before 90%; final value follows measured event size/outage duration.
- Continue safe inference/buffering during central outage; no controller action is
  dependent on central availability.

## Data and observability budgets

- Raw high-rate tracks remain edge-local or short-lived; central events use
  aggregates/candidates needed for operations and evidence.
- Metrics label sets must be bounded. Device/intersection IDs are allowed only
  where cardinality tests support them; vehicle/track IDs never become metric labels.
- Development traces/logs use short retention. Assessment evidence exports only
  selected sanitized traces and measurements.
- Set Kafka retention, database partitions, Prometheus retention and Tempo/log
  limits explicitly before P05/P10 acceptance.

## Capacity gates

Measure nominal, 2x, 5x and failure/replay loads. Record throughput, p50/p95/p99
latency, CPU, memory, disk growth, lag, loss, duplicates and operator freshness.
Stop load before host instability. P11 defines the supported ceiling from measured
results, not these planning budgets.
