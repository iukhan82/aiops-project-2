# Observability stack (P10.03)

Prometheus, Tempo, Loki and Grafana, pinned by digest, provisioned (not
clicked through) into `up.sh`'s platform stack. Resource limits match
`docs/environment/RESOURCE_BUDGET.md`'s "OTel/Prometheus/Grafana/Tempo/logs"
line exactly: 0.75 CPU / 1,536 MiB total across the four containers.

```
bash ../up.sh                                    # starts these alongside the rest of the platform
python ../verify_observability_stack.py          # P10.03 acceptance evidence
```

- Grafana: http://127.0.0.1:3000 (`admin` / `GRAFANA_ADMIN_PASSWORD` from `.env`)
- Prometheus: http://127.0.0.1:9090
- Tempo: http://127.0.0.1:3200 (OTLP receiver on 4317/4318)
- Loki: http://127.0.0.1:3100

## How metrics and traces get here

No backend service (api/gateway/ingestion/scenario-control) has a
long-running container yet (P11.01), so there is nothing at a fixed
address for Prometheus to scrape. `backend/observability.py` PUSHES
instead, via OTLP, when `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` /
`OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` are set:

```
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:4318/v1/traces
export OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://127.0.0.1:9090/api/v1/otlp/v1/metrics
```

Without these, `configure()` falls back to an in-memory span exporter and a
discarded metrics exporter - importing/using the module never requires a
live collector (unit tests rely on this). A short-lived process (a verify
script, a CLI run) should call `observability.flush()` before exiting, or
its first scheduled export (every 2 s once OTLP is configured) may never
fire.

Real metric names an OTLP push produces in Prometheus, confirmed by
`verify_observability_stack.py`, not assumed: a `BoundedCounter("x")`
becomes `x_total`; a `BoundedHistogram("x", unit="ms")` becomes
`x_milliseconds_bucket`/`_count`/`_sum`. `job` is the `service_name` passed
to `configure()`.

## What is deferred, honestly

Real backend/edge service logs are not shipped into Loki here - those
services have no long-running container to attach a log driver to yet
(P11.01). This stack's Loki push/query round trip and Grafana's
trace<->log correlation (a log's `trace_id` field links to the matching
Tempo trace) are proven with real pushed data
(`verify_observability_stack.py`), not with a real service's own output.
