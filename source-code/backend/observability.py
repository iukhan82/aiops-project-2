"""P10.01: shared OpenTelemetry instrumentation for every backend service
(the edge runtime has its own dependency-free tracing/metrics in
`edge/observability.py` - ADR-0006's resource budget keeps the container's
dependency closure minimal, so it does not import the real OTel SDK).

One `TracerProvider`/`MeterProvider` per process, resource-tagged with
`service.name`/`service.version`/`deployment_type` (device/v1's own
"simulated" vocabulary - this platform never claims to be a real
deployment). The exporter is OTLP/HTTP when `OTEL_EXPORTER_OTLP_ENDPOINT` is
set (P10.03 points that at a real Tempo/Prometheus destination); otherwise
spans/metrics stay in an in-memory exporter, so importing and using this
module never requires a live collector - `recorded_spans()` is how tests
and `verify_observability.py` inspect what was actually emitted.

The async edge-to-action path (MQTT -> Kafka -> Postgres -> detection ->
correlation -> recommendation -> command -> outcome) is not one unbroken
W3C trace - there is no broker-level context propagation here, and adding
one would mean putting a new field on every versioned wire contract this
project has (device/v1 through outcome/v1), breaking every existing
consumer's `additionalProperties: false` schema validation for a P10 nicety.
Instead every span in that path carries a `correlation_id` attribute set to
the domain id the message already has on the wire (`event_id`,
`incident_id`, `command_id`) - attributes are unbounded-cardinality by
design (the metric-label-set/v1 contract says so explicitly: "Trace and log
attributes are not bound by this contract"), so a Tempo/Grafana query can
join the whole path by that id. A synchronous call chain within one
process (an HTTP request into a DB write) still gets one real trace via
ordinary parent/child spans - proven in `verify_observability.py`.

Metric labels are bounded by the same `contracts/metric-label-set/v1`
schema every metric on this platform must satisfy: the key enum is loaded
from that file, not re-typed here, so it cannot drift; an unlisted key
raises (a programming error caught at test time, not a cardinality leak at
run time), and `http_route` is always the ASGI route TEMPLATE
(`/api/v1/devices/{device_id}`), never the raw request path, collapsing to
"unmatched" for anything that did not match a registered route - the one
label an unauthenticated caller could otherwise inflate without bound.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Span, Status, StatusCode

from backend.redaction import install_log_redaction

REPO_ROOT = Path(__file__).resolve().parents[2]
LABEL_SCHEMA = REPO_ROOT / "source-code" / "contracts" / "metric-label-set" / "v1" / "schema.json"
MAX_LABEL_VALUE_LEN = 128


def _load_allowed_label_keys() -> frozenset[str]:
    schema = json.loads(LABEL_SCHEMA.read_text(encoding="utf-8"))
    return frozenset(schema["properties"]["labels"]["propertyNames"]["enum"])


ALLOWED_LABEL_KEYS = _load_allowed_label_keys()


class InMemorySpanExporter(SpanExporter):
    """Keeps the last N finished spans in memory - what `recorded_spans()`
    reads. This is the default exporter (no collector required); a real
    OTLP endpoint replaces it only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set."""

    def __init__(self, capacity: int = 2000) -> None:
        self._spans: list[ReadableSpan] = []
        self._capacity = capacity

    def export(self, spans) -> SpanExportResult:  # noqa: ANN001 - matches SDK signature
        self._spans.extend(spans)
        overflow = len(self._spans) - self._capacity
        if overflow > 0:
            del self._spans[:overflow]
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

    def snapshot(self) -> list[ReadableSpan]:
        return list(self._spans)


_state: dict[str, object] = {}


def configure(service_name: str, service_version: str = "0.1.0") -> InMemorySpanExporter | None:
    """Idempotent per service name: a second call with the SAME name is a
    no-op and returns the first call's in-memory exporter (or None when a
    real OTLP endpoint is configured). A call with a DIFFERENT name builds
    a fresh, independent provider pair - real processes only ever call this
    once, but tests import several services into one process, and OTel's
    own `trace.set_tracer_provider` silently refuses a second global
    registration ("Overriding of current TracerProvider is not allowed").
    So `get_tracer()`/`BoundedCounter`/`BoundedHistogram` below always read
    the provider INSTANCE this call returns via `_state`, never the global
    registry - `trace.set_tracer_provider`/`metrics.set_meter_provider` are
    still attempted, best-effort, only so a real OTel auto-instrumentation
    library elsewhere in the process can find something sane."""
    if _state.get("service_name") == service_name:
        return _state.get("memory_exporter")  # type: ignore[return-value]

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "deployment_type": "simulated",
        }
    )
    # The OTel SDK's own exporters resolve their endpoint from
    # OTEL_EXPORTER_OTLP_{TRACES,METRICS}_ENDPOINT first, falling back to
    # OTEL_EXPORTER_OTLP_ENDPOINT - traces and metrics go to different real
    # services here (Tempo, Prometheus), so either the generic var or BOTH
    # per-signal vars must be checked, not the generic one alone.
    traces_configured = bool(
        os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    )
    metrics_configured = bool(
        os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        or os.environ.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT")
    )

    tracer_provider = TracerProvider(resource=resource)
    memory_exporter: InMemorySpanExporter | None = None
    if traces_configured:
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    else:
        memory_exporter = InMemorySpanExporter()
        tracer_provider.add_span_processor(SimpleSpanProcessor(memory_exporter))

    if metrics_configured:
        # A short interval, not the 60 s default: real processes here are
        # often short-lived verify scripts, which would otherwise exit
        # before their first export ever fires.
        reader = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=2_000)
    else:
        reader = PeriodicExportingMetricReader(
            ConsoleMetricExporter(out=open(os.devnull, "w")),  # noqa: SIM115 - lives for the process
            export_interval_millis=60_000,
        )
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])

    try:
        trace.set_tracer_provider(tracer_provider)
        metrics.set_meter_provider(meter_provider)
    except Exception:  # noqa: BLE001 - best-effort global registration only
        pass

    install_log_redaction()
    logging.getLogger().addFilter(_TraceContextFilter())

    _state.update(
        service_name=service_name,
        memory_exporter=memory_exporter,
        tracer=tracer_provider.get_tracer(service_name),
        meter=meter_provider.get_meter(service_name),
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
    )
    return memory_exporter


def flush() -> None:
    """Forces every pending span/metric out now, rather than waiting for
    the batch/periodic export timer - real for short-lived processes (a
    verify script, a CLI run) that would otherwise exit before their first
    scheduled export ever fires."""
    provider = _state.get("tracer_provider")
    if provider is not None:
        provider.force_flush()  # type: ignore[union-attr]
    meter_provider = _state.get("meter_provider")
    if meter_provider is not None:
        meter_provider.force_flush()  # type: ignore[union-attr]


def get_tracer() -> trace.Tracer:
    return _state.get("tracer") or trace.get_tracer("aiops")  # type: ignore[return-value]


def recorded_spans() -> list[ReadableSpan]:
    exporter = _state.get("memory_exporter")
    return exporter.snapshot() if exporter else []


class _TraceContextFilter(logging.Filter):
    """Stamps the current span's trace_id/span_id onto every log record, so
    a log line can be joined back to the trace that produced it."""

    def filter(self, record: logging.LogRecord) -> bool:
        span = trace.get_current_span()
        ctx = span.get_span_context()
        record.trace_id = format(ctx.trace_id, "032x") if ctx.is_valid else "-"
        record.span_id = format(ctx.span_id, "016x") if ctx.is_valid else "-"
        return True


def validate_labels(labels: Mapping[str, str]) -> None:
    bad = set(labels) - ALLOWED_LABEL_KEYS
    if bad:
        raise ValueError(
            f"metric label keys {sorted(bad)} are not in contracts/metric-label-set/v1's "
            f"allowed key enum {sorted(ALLOWED_LABEL_KEYS)}"
        )


def bound_label_value(value: str) -> str:
    return value[:MAX_LABEL_VALUE_LEN]


def http_route_label(route_template: str | None) -> str:
    """OTel semantic convention: `http.route` is the matched template, never
    the raw path. No route matched (404, or an attacker probing arbitrary
    paths) always collapses to one series, "unmatched" - the raw path is
    never turned into a label."""
    return route_template if route_template else "unmatched"


class BoundedCounter:
    def __init__(self, name: str, description: str = "") -> None:
        meter = _state.get("meter") or metrics.get_meter("aiops")
        self._counter = meter.create_counter(name, description=description)  # type: ignore[union-attr]

    def add(self, amount: float = 1.0, **labels: str) -> None:
        validate_labels(labels)
        self._counter.add(amount, {k: bound_label_value(v) for k, v in labels.items()})


class BoundedHistogram:
    def __init__(self, name: str, description: str = "", unit: str = "ms") -> None:
        meter = _state.get("meter") or metrics.get_meter("aiops")
        self._histogram = meter.create_histogram(name, description=description, unit=unit)  # type: ignore[union-attr]

    def record(self, value: float, **labels: str) -> None:
        validate_labels(labels)
        self._histogram.record(value, {k: bound_label_value(v) for k, v in labels.items()})


def _matched_route_template(scope: Mapping[str, object]) -> str | None:
    """This Starlette version (1.6.0) does not put the matched `Route` on
    `scope["route"]` the way older versions/OTel instrumentation libraries
    assume - only `scope["endpoint"]` (the handler function) survives
    routing. So the template is found by matching that endpoint back
    against the app's own registered routes (`scope["app"]`, set before the
    middleware stack even runs) - a linear scan over a few dozen routes,
    fine at this scale. Returns None for a genuinely unmatched request
    (404: no endpoint was ever set)."""
    endpoint = scope.get("endpoint")
    app = scope.get("app")
    if endpoint is None or app is None:
        return None
    for route in getattr(app, "routes", []):
        if getattr(route, "endpoint", None) is endpoint:
            return getattr(route, "path", None)
    return None


class HTTPTracingMiddleware:
    """Starlette ASGI middleware: one span plus one bounded metric emission
    per HTTP request, `http_route` always the matched route TEMPLATE (see
    `http_route_label`), never the raw path - added with
    `app.add_middleware(HTTPTracingMiddleware, service_name="...")`
    alongside this project's existing `BodyLimit`/`SecurityHeaders`
    middleware (`backend/api/hardening.py`)."""

    def __init__(self, app, service_name: str) -> None:  # noqa: ANN001 - ASGI app, not typed upstream
        self.app = app
        self._requests = BoundedCounter(
            f"{service_name}_http_requests", "HTTP requests by route and status"
        )
        self._latency = BoundedHistogram(
            f"{service_name}_http_request_duration", "HTTP request duration", unit="ms"
        )

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001 - ASGI signature
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_holder: dict[str, int] = {}

        async def send_wrapper(message) -> None:  # noqa: ANN001 - ASGI message
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
            await send(message)

        with traced("http.request", method=scope.get("method", "")) as span:
            await self.app(scope, receive, send_wrapper)
            label = http_route_label(_matched_route_template(scope))
            status_code = status_holder.get("code", 0)
            span.set_attribute("http.route", label)
            span.set_attribute("http.status_code", status_code)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            self._requests.add(http_route=label, http_status_code=str(status_code))
            self._latency.record(elapsed_ms, http_route=label)


@contextmanager
def traced(name: str, *, correlation_id: str | None = None, **attrs: str) -> Iterator[Span]:
    """`with traced("gateway.publish", correlation_id=event_id, device_type=...) as span:`
    - a failing block records the exception on the span and re-raises; it
    never swallows an error to make a trace look healthier than the run was."""
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as span:
        if correlation_id is not None:
            span.set_attribute("correlation_id", correlation_id)
        for key, value in attrs.items():
            span.set_attribute(key, value)
        try:
            yield span
        except Exception as exc:  # noqa: BLE001 - re-raised immediately, only annotates the span
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            raise
