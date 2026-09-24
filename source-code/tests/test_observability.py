"""P10.01: the shared OpenTelemetry instrumentation - context propagation,
the bounded-label gate, and the edge runtime's dependency-free span shape.
No live collector or database is needed for any of this; the real-stack
edge-to-action correlation proof is `backend/verify_observability.py`."""

from __future__ import annotations

import pytest

from backend import observability as obs
from edge import observability as edge_obs


@pytest.fixture(autouse=True)
def _fresh_backend_state():
    obs._state.clear()  # noqa: SLF001 - test isolation between configure() calls
    yield
    obs._state.clear()  # noqa: SLF001


def test_configure_is_idempotent_per_service_name() -> None:
    first = obs.configure("test-service")
    second = obs.configure("test-service")
    assert second is first  # second call is a no-op, same exporter object


def test_nested_spans_share_one_trace_and_record_parent_child() -> None:
    obs.configure("test-service")
    with obs.traced("outer", correlation_id="corr-1"):
        with obs.traced("inner", correlation_id="corr-1"):
            pass
    spans = obs.recorded_spans()
    assert len(spans) == 2
    by_name = {s.name: s for s in spans}
    outer, inner = by_name["outer"], by_name["inner"]
    assert outer.context.trace_id == inner.context.trace_id
    assert inner.parent.span_id == outer.context.span_id
    assert outer.attributes["correlation_id"] == "corr-1"


def test_traced_records_error_status_and_still_raises() -> None:
    obs.configure("test-service")
    with pytest.raises(ValueError), obs.traced("failing"):
        raise ValueError("boom")
    (span,) = obs.recorded_spans()
    assert span.status.status_code.name == "ERROR"


def test_metric_label_keys_are_bounded_by_the_real_contract() -> None:
    obs.configure("test-service")
    counter = obs.BoundedCounter("test_counter")
    counter.add(http_route="/api/v1/devices")  # an allowed key: fine
    with pytest.raises(ValueError, match="vehicle_id"):
        counter.add(vehicle_id="veh-12345")  # not in metric-label-set/v1's key enum


def test_http_route_label_passes_through_a_template_and_collapses_none() -> None:
    assert obs.http_route_label("/api/v1/devices/{device_id}") == "/api/v1/devices/{device_id}"
    assert obs.http_route_label(None) == "unmatched"


def test_http_tracing_middleware_collapses_a_hostile_path_scan_to_one_series() -> None:
    """`HTTPTracingMiddleware` only ever derives `http_route` from Starlette's
    OWN matched route template (`scope["route"]`), never from the raw
    request path - so this is the real cardinality proof: a scan of 200
    distinct junk paths, none of which match the app's one real route, must
    still produce spans that all carry the SAME "unmatched" http.route,
    mirroring P04.05's hostile-input bound for the edge metrics registry."""
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    obs.configure("test-http-service")

    async def home(request):  # noqa: ANN001, ARG001
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/api/v1/devices", home)])
    app.add_middleware(obs.HTTPTracingMiddleware, service_name="test-http-service")
    client = TestClient(app)

    assert client.get("/api/v1/devices").status_code == 200
    for i in range(200):
        client.get(f"/hostile/{i}/not-a-real-route")

    spans = [s for s in obs.recorded_spans() if s.name == "http.request"]
    assert len(spans) == 201
    routes_seen = {s.attributes["http.route"] for s in spans}
    assert routes_seen == {"/api/v1/devices", "unmatched"}
    matched = [s for s in spans if s.attributes["http.route"] == "/api/v1/devices"]
    assert matched[0].attributes["http.status_code"] == 200
    unmatched = [s for s in spans if s.attributes["http.route"] == "unmatched"]
    assert len(unmatched) == 200
    assert all(s.attributes["http.status_code"] == 404 for s in unmatched)


def test_bound_label_value_truncates_to_contract_max_length() -> None:
    long_value = "x" * 500
    assert len(obs.bound_label_value(long_value)) == obs.MAX_LABEL_VALUE_LEN


def test_edge_span_has_otel_shape_and_correlation_id() -> None:
    sink = edge_obs.RecordingSink()
    edge_obs.configure(sink)
    try:
        with edge_obs.span(
            "edge.evaluate_device", correlation_id="site:dev:2026-01-01T00:00:00Z", device_id="dev"
        ):
            pass
    finally:
        edge_obs.configure()  # restore the default StdoutSink for other tests
    (span,) = sink.spans
    assert len(span["trace_id"]) == 32
    assert len(span["span_id"]) == 16
    assert span["parent_span_id"] is None
    assert span["status"] == "ok"
    assert span["attributes"]["correlation_id"] == "site:dev:2026-01-01T00:00:00Z"
    assert span["attributes"]["device_id"] == "dev"


def test_edge_nested_spans_share_trace_id_and_link_parent() -> None:
    sink = edge_obs.RecordingSink()
    edge_obs.configure(sink)
    try:
        with edge_obs.span("outer"):
            with edge_obs.span("inner"):
                pass
    finally:
        edge_obs.configure()
    by_name = {s["name"]: s for s in sink.spans}
    assert by_name["outer"]["trace_id"] == by_name["inner"]["trace_id"]
    assert by_name["inner"]["parent_span_id"] == by_name["outer"]["span_id"]


def test_edge_span_records_error_status_and_still_raises() -> None:
    sink = edge_obs.RecordingSink()
    edge_obs.configure(sink)
    try:
        with pytest.raises(RuntimeError), edge_obs.span("failing"):
            raise RuntimeError("boom")
    finally:
        edge_obs.configure()
    (span,) = sink.spans
    assert span["status"] == "error"
    assert "boom" in span["status_message"]
