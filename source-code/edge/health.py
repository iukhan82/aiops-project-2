"""Minimal, dependency-free HTTP health/metrics endpoint for the edge runtime.

GET /healthz  liveness  (200 while the event loop heartbeat is fresh, else 503)
GET /readyz   readiness (200 only if the runtime can make a safe decision:
                         registry loaded and model or baseline available)
GET /metrics  Prometheus text exposition

Hardened for an internal-only, unauthenticated endpoint (docs/REFERENCE_
ARCHITECTURE.md keeps it off public routes): GET only, tiny request limit,
read timeout, no request body parsing, connection closed after each reply,
and nothing but the three fixed paths is ever served.
"""

from __future__ import annotations

import asyncio
import json

from edge.runtime import EdgeRuntime

MAX_REQUEST_BYTES = 4096
READ_TIMEOUT_S = 2.0
_STATUS = {
    200: "OK",
    404: "Not Found",
    405: "Method Not Allowed",
    400: "Bad Request",
    503: "Service Unavailable",
}


def _response(status: int, body: bytes, content_type: str) -> bytes:
    head = (
        f"HTTP/1.1 {status} {_STATUS[status]}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n\r\n"
    )
    return head.encode("ascii") + body


def route(runtime: EdgeRuntime, method: str, path: str) -> bytes:
    if method != "GET":
        return _response(405, b"method not allowed\n", "text/plain; charset=utf-8")
    if path == "/metrics":
        return _response(
            200, runtime.render_metrics().encode(), "text/plain; version=0.0.4; charset=utf-8"
        )
    if path in ("/healthz", "/readyz"):
        health = runtime.health()
        ok = health.live if path == "/healthz" else health.ready
        body = json.dumps(health.to_dict(), sort_keys=True).encode() + b"\n"
        return _response(200 if ok else 503, body, "application/json")
    return _response(404, b"not found\n", "text/plain; charset=utf-8")


async def _handle(runtime: EdgeRuntime, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n"), READ_TIMEOUT_S)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("request line too long")
        parts = raw.decode("ascii", errors="strict").strip().split(" ")
        if len(parts) != 3 or not parts[2].startswith("HTTP/"):
            raise ValueError("malformed request line")
        writer.write(route(runtime, parts[0], parts[1].split("?", 1)[0]))
    except (
        asyncio.TimeoutError,
        asyncio.IncompleteReadError,
        asyncio.LimitOverrunError,
        ValueError,
        UnicodeDecodeError,
    ):
        writer.write(_response(400, b"bad request\n", "text/plain; charset=utf-8"))
    try:
        await writer.drain()
    finally:
        writer.close()


async def start_health_server(
    runtime: EdgeRuntime, host: str = "127.0.0.1", port: int = 8080
) -> asyncio.AbstractServer:
    return await asyncio.start_server(
        lambda r, w: _handle(runtime, r, w), host, port, limit=MAX_REQUEST_BYTES
    )
