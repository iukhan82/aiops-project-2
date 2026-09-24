"""P10.01: dependency-free, OTel-schema-compatible span emission for the
edge runtime container. ADR-0006's resource budget (0.75 CPU/512 MiB, "no
build toolchain in the final image") is why this does not import the real
`opentelemetry-sdk` the way `backend/observability.py` does - the edge
container's entire point is a minimal, audited dependency closure
(`edge/requirements-lock.txt`, `pip install --no-deps`). A span emitted
here has the same shape OTel expects (trace_id, span_id, parent_span_id,
name, start/end time, status, attributes) as one structured JSON line per
span - swappable for the real SDK later without changing a call site, if
the budget ever allows it.

Correlation with the backend's real OTel traces does not need a shared
trace_id (there is no broker-level context propagation across MQTT/Kafka
here - see `backend/observability.py`'s module docstring for why). Every
inference span instead carries `correlation_id` set to the exact value
`EdgeRuntime._to_event` already puts on the emitted observation-envelope
event (`f"{site_id}:{device_id}:{observation_time}"`) - the id a
Tempo/Grafana query joins the rest of the edge-to-action path on.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol

_current_trace_id: ContextVar[str | None] = ContextVar("_current_trace_id", default=None)
_current_span_id: ContextVar[str | None] = ContextVar("_current_span_id", default=None)


def new_trace_id() -> str:
    return uuid.uuid4().hex  # 32 hex chars: the W3C trace_id shape


def new_span_id() -> str:
    return uuid.uuid4().hex[:16]  # 16 hex chars: the W3C span_id shape


class SpanSink(Protocol):
    def emit(self, span: dict) -> None: ...


class StdoutSink:
    """One JSON line per finished span on stdout - the container's log
    stream, picked up wherever the platform's log collection points."""

    def emit(self, span: dict) -> None:
        sys.stdout.write(json.dumps(span, sort_keys=True, default=str) + "\n")


class RecordingSink:
    """Keeps finished spans in memory - what tests and
    `verify_observability.py` read instead of parsing stdout."""

    def __init__(self) -> None:
        self.spans: list[dict] = []

    def emit(self, span: dict) -> None:
        self.spans.append(span)


_sink: SpanSink = StdoutSink()


def configure(sink: SpanSink | None = None) -> None:
    global _sink
    _sink = sink or StdoutSink()


@contextmanager
def span(
    name: str,
    *,
    service_name: str = "edge-runtime",
    correlation_id: str | None = None,
    **attrs: object,
):
    """A failing block is recorded with `status="error"` and re-raised - it
    never swallows an error to make a trace look healthier than the run was."""
    trace_id = _current_trace_id.get() or new_trace_id()
    parent_span_id = _current_span_id.get()
    span_id = new_span_id()
    trace_token = _current_trace_id.set(trace_id)
    span_token = _current_span_id.set(span_id)
    started = time.time()
    status, status_message = "ok", ""
    try:
        yield {"trace_id": trace_id, "span_id": span_id}
    except Exception as exc:
        status, status_message = "error", str(exc)
        raise
    finally:
        ended = time.time()
        _current_trace_id.reset(trace_token)
        _current_span_id.reset(span_token)
        attributes = dict(attrs)
        if correlation_id is not None:
            attributes["correlation_id"] = correlation_id
        _sink.emit(
            {
                "trace_id": trace_id,
                "span_id": span_id,
                "parent_span_id": parent_span_id,
                "name": name,
                "service_name": service_name,
                "start_time": started,
                "end_time": ended,
                "duration_ms": round((ended - started) * 1000, 3),
                "status": status,
                "status_message": status_message,
                "attributes": attributes,
            }
        )
