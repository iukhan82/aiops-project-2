"""P10.05: a seeded load that drives the platform's REAL instruments through normal operation and one injected fault.

Nothing here writes a metric value. Every number a detector will later read is produced by the code that emits it in
production: the ingestion outcome counters and latency histogram (`backend/ingestion/ingest.py`), the network-state
freshness counter (`backend/state/network_state.py`), the gateway counters (same names `backend/gateway/gateway.py`
registers) and the API's HTTP middleware (`backend/observability.HTTPTracingMiddleware`, called as an ASGI application so
its request counter and duration histogram see real elapsed time). The generator only decides *what happens* each second -
how many events, which outcomes, how long a request takes - from a seeded random stream and a fault schedule.

The load itself is synthetic (there is no production traffic to record); what is measured is how the real instruments
respond to it.
"""

from __future__ import annotations

import asyncio
import math
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend import observability  # noqa: E402

SCENARIOS = (
    "normal",
    "ingest_reject_surge",
    "ingest_latency_slow",
    "gateway_stall",
    "api_errors",
    "api_slow",
    "state_stale",
)

# One run: 45 s of warm-up (the 30 s rate windows fill by 75 s), then the timeline below. Seconds from the run's start.
RUN_SECONDS = 330
WARMUP_SECONDS = 45


@dataclass
class Plan:
    """Everything that varies between runs, drawn once from the seed, recorded in the run's manifest."""

    seed: int
    scenario: str
    replicate: int
    ingest_rate: float  # baseline events per second
    api_rate: float  # baseline requests per second
    reject_base: float  # normal schema-rejection share
    ingest_latency_median_ms: float
    api_latency_median_ms: float
    api_error_base: float
    stale_base: float  # normal share of network-state records that are stale
    delivery_base: float  # normal share of received events delivered within the second
    burst_start: int  # a benign traffic burst: more load, no failures
    burst_seconds: int
    fault_start: int | None
    fault_seconds: int
    severity: float  # 0.5 mild .. 1.0 severe; scales the fault's effect
    load_period_s: float
    load_phase: float
    extra: dict = field(default_factory=dict)

    def fault_active(self, second: int) -> bool:
        return (
            self.fault_start is not None
            and self.fault_start <= second < self.fault_start + self.fault_seconds
        )

    def in_burst(self, second: int) -> bool:
        return self.burst_start <= second < self.burst_start + self.burst_seconds

    def as_dict(self) -> dict:
        return asdict(self)


def make_plan(seed: int, scenario: str, replicate: int = 0) -> Plan:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}")
    rng = random.Random(f"ops-dataset:{seed}:{scenario}:{replicate}")
    fault = scenario != "normal"
    fault_start = rng.randint(160, 180) if fault else None
    fault_seconds = rng.randint(80, 100) if fault else 0
    # The benign burst never overlaps the fault window: it is there so that "more load" alone is not the signal.
    burst_start = rng.randint(115, 125)
    return Plan(
        seed=seed,
        scenario=scenario,
        replicate=replicate,
        ingest_rate=rng.uniform(14.0, 30.0),
        api_rate=rng.uniform(10.0, 22.0),
        reject_base=rng.uniform(0.002, 0.012),
        ingest_latency_median_ms=rng.uniform(35.0, 80.0),
        api_latency_median_ms=rng.uniform(15.0, 45.0),
        api_error_base=rng.uniform(0.001, 0.006),
        stale_base=rng.uniform(0.005, 0.04),
        delivery_base=rng.uniform(0.990, 1.0),
        burst_start=burst_start,
        burst_seconds=rng.randint(15, 20),
        fault_start=fault_start,
        fault_seconds=fault_seconds,
        severity=rng.uniform(0.5, 1.0) if fault else 0.0,
        load_period_s=rng.uniform(90.0, 200.0),
        load_phase=rng.uniform(0.0, 2 * math.pi),
    )


def _poisson_like(rng: random.Random, mean: float) -> int:
    return max(0, round(rng.gauss(mean, math.sqrt(max(mean, 1.0)))))


def _lognormal(rng: random.Random, median: float, sigma: float = 0.55) -> float:
    return median * math.exp(rng.gauss(0.0, sigma))


class Load:
    """Runs one plan against the real instruments. `run()` is blocking and returns the wall-clock start/end (epoch s)."""

    def __init__(self, plan: Plan) -> None:
        self.plan = plan
        self.rng = random.Random(f"ops-load:{plan.seed}:{plan.scenario}:{plan.replicate}")
        from backend.ingestion import ingest as ing
        from backend.state import network_state as ns

        self._ing = ing
        self._ns = ns
        self._latency = ing._ingest_latency_histogram()  # noqa: SLF001 - the real instrument
        self._received = observability.BoundedCounter("gateway_events_received")
        self._delivered = observability.BoundedCounter("gateway_events_delivered")
        self._app = self._build_app()

    # ---- the real API middleware around a handler that obeys the request's own instructions
    def _build_app(self):
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse

        app = FastAPI()
        app.add_middleware(observability.HTTPTracingMiddleware, service_name="api")

        @app.get("/v1/work")
        async def work(delay_ms: float = 0.0, status: int = 200):
            await asyncio.sleep(delay_ms / 1000.0)
            return JSONResponse({"ok": status < 500}, status_code=status)

        return app

    async def _request(self, delay_ms: float, status: int) -> None:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/v1/work",
            "raw_path": b"/v1/work",
            "query_string": f"delay_ms={delay_ms:.1f}&status={status}".encode(),
            "headers": [],
            "server": ("ops", 80),
            "client": ("ops", 0),
            "root_path": "",
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message):
            return None

        await self._app(scope, receive, send)

    # ---- one second of ingestion, gateway and network-state activity
    def _stream_second(self, second: int) -> None:
        p, rng = self.plan, self.rng
        wave = 1.0 + 0.25 * math.sin(2 * math.pi * second / p.load_period_s + p.load_phase)
        burst = 2.2 if p.in_burst(second) else 1.0
        events = _poisson_like(rng, p.ingest_rate * wave * burst)
        fault = p.fault_active(second)
        severity = p.severity

        reject_share = p.reject_base
        latency_median = p.ingest_latency_median_ms * (1.0 + (burst - 1.0) * 0.15)
        delivery = p.delivery_base
        stale = p.stale_base
        if fault and p.scenario == "ingest_reject_surge":
            reject_share = 0.05 + 0.40 * severity
        if fault and p.scenario == "ingest_latency_slow":
            latency_median = p.ingest_latency_median_ms * (8.0 + 24.0 * severity)
        if fault and p.scenario == "gateway_stall":
            delivery = max(0.05, 0.65 - 0.55 * severity)
        if fault and p.scenario == "state_stale":
            stale = 0.25 + 0.55 * severity

        for _ in range(events):
            self._received.add(1)
            if rng.random() < delivery:
                self._delivered.add(1)
            if rng.random() < reject_share:
                self._ing._outcome_counter("rejected_schema").add()  # noqa: SLF001
            else:
                self._ing._outcome_counter("inserted").add()  # noqa: SLF001
                self._latency.record(_lognormal(rng, latency_median))
            self._ns._freshness_counter().add(  # noqa: SLF001
                1, freshness_status="stale" if rng.random() < stale else "fresh"
            )

    async def _api_second(self, second: int) -> None:
        p, rng = self.plan, self.rng
        wave = 1.0 + 0.2 * math.sin(2 * math.pi * second / (p.load_period_s * 0.7) + p.load_phase)
        burst = 2.0 if p.in_burst(second) else 1.0
        requests = _poisson_like(rng, p.api_rate * wave * burst)
        fault = p.fault_active(second)
        error_share = p.api_error_base
        slow_share, slow_median = 0.0, 0.0
        if fault and p.scenario == "api_errors":
            error_share = 0.03 + 0.30 * p.severity
        if fault and p.scenario == "api_slow":
            slow_share, slow_median = 0.35 + 0.6 * p.severity, 350.0 + 2200.0 * p.severity
        tasks = []
        for _ in range(requests):
            status = 500 if rng.random() < error_share else 200
            if slow_share and rng.random() < slow_share:
                delay = _lognormal(rng, slow_median, 0.3)
            else:
                delay = _lognormal(rng, p.api_latency_median_ms)
            tasks.append(asyncio.create_task(self._request(delay, status)))
        if tasks:
            await asyncio.gather(*tasks)

    async def _run(self, start: float) -> None:
        pending: list[asyncio.Task] = []
        for second in range(RUN_SECONDS):
            target = start + second
            now = time.time()
            if now < target:
                await asyncio.sleep(target - now)
            self._stream_second(second)
            # API requests can outlast their second (a slow fault); they are not awaited before the next second starts.
            pending.append(asyncio.create_task(self._api_second(second)))
            pending = [t for t in pending if not t.done()]
        await asyncio.gather(*pending)

    def run(self) -> tuple[float, float]:
        start = time.time() + 1.0
        asyncio.run(self._run(start))
        observability.flush()
        return start, time.time()
