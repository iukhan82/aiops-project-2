"""P10.05: the signal catalogue. Each signal is a PromQL expression over the metrics the platform really emits, evaluated
per run (`job` is the run id) at a fixed step. These are the same series, in the same form, that the operational
detector (P10.06) will read from the live Prometheus, so a model trained here sees production-shaped input.

Windows are 30 s: the push interval is 2 s, so 30 s holds about 15 samples, and a signal is meaningful only after the
first 30 s of a run (the 60 s warm-up in `load.py` covers that).
"""

from __future__ import annotations

WINDOW = "30s"

# name -> (description, unit, PromQL with a {job} placeholder)
SIGNALS: dict[str, tuple[str, str, str]] = {
    "ingest_rate": (
        "events persisted per second",
        "1/s",
        'sum(rate(ingestion_events_inserted_total{{job="{job}"}}[{w}])) or vector(0)',
    ),
    "ingest_reject_ratio": (
        "share of ingested events rejected for schema, unknown device or content conflict",
        "ratio",
        '(sum(rate(ingestion_events_rejected_schema_total{{job="{job}"}}[{w}])) or vector(0))'
        ' / clamp_min((sum(rate(ingestion_events_inserted_total{{job="{job}"}}[{w}])) or vector(0))'
        ' + (sum(rate(ingestion_events_rejected_schema_total{{job="{job}"}}[{w}])) or vector(0)), 0.000001)',
    ),
    "ingest_latency_p95_ms": (
        "95th percentile observation-to-database latency",
        "ms",
        'histogram_quantile(0.95, sum by (le) (rate(ingestion_ingest_latency_milliseconds_bucket{{job="{job}"}}[{w}])))',
    ),
    "gateway_delivery_ratio": (
        "delivered events over received events",
        "ratio",
        'sum(increase(gateway_events_delivered_total{{job="{job}"}}[{w}]))'
        ' / clamp_min(sum(increase(gateway_events_received_total{{job="{job}"}}[{w}])), 0.000001)',
    ),
    "api_request_rate": (
        "API requests per second",
        "1/s",
        'sum(rate(api_http_requests_total{{job="{job}"}}[{w}])) or vector(0)',
    ),
    "api_error_ratio": (
        "share of API requests answered with a 5xx",
        "ratio",
        '(sum(rate(api_http_requests_total{{job="{job}",http_status_code=~"5.."}}[{w}])) or vector(0))'
        ' / clamp_min(sum(rate(api_http_requests_total{{job="{job}"}}[{w}])), 0.000001)',
    ),
    "api_latency_p95_ms": (
        "95th percentile API request duration",
        "ms",
        'histogram_quantile(0.95, sum by (le) (rate(api_http_request_duration_milliseconds_bucket{{job="{job}"}}[{w}])))',
    ),
    "state_fresh_share": (
        "share of computed network-state records that are fresh",
        "ratio",
        'sum(rate(network_state_records_total{{job="{job}",freshness_status="fresh"}}[{w}]))'
        ' / clamp_min(sum(rate(network_state_records_total{{job="{job}"}}[{w}])), 0.000001)',
    ),
}


def query(name: str, job: str) -> str:
    return SIGNALS[name][2].format(job=job, w=WINDOW)
