"""P09.03 (CTL-33): what the API does before it has decided anything - headers, body size, request rate.

* `SecurityHeaders`: every HTTP response, including refusals and errors, says it is not to be sniffed, framed, cached, or carried
  in a referrer. The API returns JSON only, so its content-security policy is the strictest one (`default-src 'none'`). There is
  no cross-origin policy to open: the browser reaches the API through the UI's own origin (a proxy), so no CORS header is ever sent.
* `BodyLimit`: a request body larger than `MAX_BODY_BYTES` is refused with 413 before it is read into memory, whether it declares
  its size or streams it in chunks. No endpoint takes a large body: the biggest is a few hundred bytes of JSON.
* `RateLimiter`: token buckets. A caller that has already been identified is limited per person - reads and writes separately, so a
  runaway script cannot hammer the write path while the UI keeps reading. A caller that has NOT been identified is limited per
  network address on failures only (a bad or missing token), so guessing tokens or hammering the identity-key fetch is throttled
  without slowing anyone who is signed in. Limits are configurable (`AIOPS_RATE_*`); the defaults leave a busy control-room session
  far from them.

These are in-process limits: they protect one API process from one misbehaving client. A distributed ceiling belongs at the ingress
(P11), which the deployment adds; this is the layer that is always there.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable

MAX_BODY_BYTES = int(os.environ.get("AIOPS_MAX_BODY_BYTES", 64 * 1024))

SECURITY_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
)


class SecurityHeaders:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        *[(n, v) for n, v in SECURITY_HEADERS if n not in present],
                    ],
                }
            await send(message)

        await self.app(scope, receive, send_with_headers)


class _BodyTooLarge(Exception):
    pass


class BodyLimit:
    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = next((v for k, v in scope.get("headers", []) if k == b"content-length"), None)
        try:
            too_big = declared is not None and int(declared) > self.max_bytes
        except ValueError:
            too_big = True
        if too_big:
            await self._refuse(send)
            return
        received = 0
        refused = False

        async def counting_receive():
            nonlocal received, refused
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    refused = True
                    raise _BodyTooLarge
            return message

        started = False

        async def guarded_send(message) -> None:
            nonlocal started
            if refused:
                return
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception:  # noqa: BLE001 - the application may turn the aborted read into its own error; ours is the answer
            if not refused:
                raise
        if refused and not started:
            await self._refuse(send)

    async def _refuse(self, send) -> None:
        body = b'{"detail":{"error":"body_too_large","message":"The request body is larger than this API accepts."}}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class Bucket:
    __slots__ = ("tokens", "stamp")

    def __init__(self, tokens: float, stamp: float) -> None:
        self.tokens, self.stamp = tokens, stamp


class RateLimiter:
    """Token buckets keyed by string. `check` spends one token and returns 0.0 when allowed, or the seconds to wait when not."""

    def __init__(
        self, rate_per_s: float, burst: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.rate, self.burst, self.clock = rate_per_s, burst, clock
        self._buckets: dict[str, Bucket] = {}
        self._lock = threading.Lock()

    def check(self, key: str, cost: float = 1.0) -> float:
        now = self.clock()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) > 20_000:
                    self._buckets.clear()  # bounded memory under an address-spraying flood; a cleared bucket only forgives, never blocks
                bucket = self._buckets[key] = Bucket(self.burst, now)
            bucket.tokens = min(self.burst, bucket.tokens + (now - bucket.stamp) * self.rate)
            bucket.stamp = now
            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return 0.0
            return (cost - bucket.tokens) / self.rate


def _limit(name: str, default_rate: float, default_burst: float) -> RateLimiter:
    return RateLimiter(
        float(os.environ.get(f"AIOPS_RATE_{name}_PER_S", default_rate)),
        float(os.environ.get(f"AIOPS_RATE_{name}_BURST", default_burst)),
    )


READS = _limit("READ", 100.0, 300.0)
WRITES = _limit("WRITE", 5.0, 40.0)
# Failed identifications, per client address: 20 in a burst, then one every 3 seconds.
FAILURES = _limit("AUTH_FAILURE", 0.33, 20.0)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "WEBSOCKET"}


def limit_person(sub: str, method: str) -> float:
    """Spend a token for an identified caller. 0.0 when allowed, otherwise the seconds to wait."""
    if method.upper() in SAFE_METHODS:
        return READS.check(f"read:{sub}")
    return WRITES.check(f"write:{sub}")


def client_address(scope_client: tuple[str, int] | None) -> str:
    return scope_client[0] if scope_client else "unknown"
