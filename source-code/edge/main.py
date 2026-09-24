"""Edge runtime entrypoint.

    python -m edge.main replay --config CONFIG.json --events EVENTS.jsonl \\
        --out OUT.jsonl --report REPORT.json [--health-port 8080] [--pace 0]

`replay` feeds recorded telemetry through the real runtime (validation ->
features -> ONNX inference -> events) while serving /healthz, /readyz and
/metrics, and writes a measurement report (throughput, cold vs warm latency,
peak memory, cgroup CPU throttling). Live broker intake arrives with Phase 05;
until then replay is the intake used for verification and benchmarking.

CONFIG.json (paths are inside the container/host running the process):
    {"site_id": ..., "runtime_device_id": ..., "geometry_version": ...,
     "registry_path": ..., "baseline_path": ..., "model_dir": ... | null,
     "emit_clear": false, "boot_id": ...}
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import statistics
import sys
import time
from datetime import timedelta
from pathlib import Path

from edge.features import FeatureConfig
from edge.health import start_health_server
from edge.outbox import DurableOutbox, OutboxSink, drain
from edge.replay import INGEST_LAG_S, read_jsonl
from edge.runtime import EdgeConfig, EdgeRuntime, ListSink
from edge.timeutil import parse_ts


def load_config(path: Path) -> EdgeConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["registry_path"] = Path(raw["registry_path"])
    raw["baseline_path"] = Path(raw["baseline_path"])
    raw["model_dir"] = Path(raw["model_dir"]) if raw.get("model_dir") else None
    if "feature" in raw:
        raw["feature"] = FeatureConfig(**raw["feature"])
    return EdgeConfig(**raw)


class JsonlSink:
    def __init__(self, path: Path) -> None:
        self._handle = path.open("w", encoding="utf-8", newline="\n")
        self.count = 0

    def emit(self, event: dict) -> None:
        self._handle.write(json.dumps(event, sort_keys=True) + "\n")
        self.count += 1

    def close(self) -> None:
        self._handle.close()


def _percentiles(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "p50": None, "p95": None, "p99": None, "max": None, "mean": None}
    ordered = sorted(values)

    def pick(q: float) -> float:
        return ordered[min(len(ordered) - 1, int(q * len(ordered)))]

    return {
        "n": len(ordered),
        "p50": round(pick(0.50), 4),
        "p95": round(pick(0.95), 4),
        "p99": round(pick(0.99), 4),
        "max": round(ordered[-1], 4),
        "mean": round(statistics.fmean(ordered), 4),
    }


def _read_proc_kv(path: str) -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        with open(path, encoding="ascii") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    out[parts[0].rstrip(":")] = int(parts[1])
    except OSError:
        pass
    return out


def system_snapshot() -> dict:
    status = _read_proc_kv("/proc/self/status")
    cpu = _read_proc_kv("/sys/fs/cgroup/cpu.stat")
    memory_max = None
    try:
        raw = Path("/sys/fs/cgroup/memory.max").read_text(encoding="ascii").strip()
        memory_max = None if raw == "max" else int(raw)
    except OSError:
        pass
    return {
        "vm_hwm_kb": status.get("VmHWM"),
        "vm_rss_kb": status.get("VmRSS"),
        "cgroup_memory_max_bytes": memory_max,
        "cgroup_cpu_nr_throttled": cpu.get("nr_throttled"),
        "cgroup_cpu_throttled_usec": cpu.get("throttled_usec"),
        "cgroup_cpu_usage_usec": cpu.get("usage_usec"),
        "process_cpu_s": round(time.process_time(), 3),
    }


async def replay_command(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    if args.capture_latency:
        config = EdgeConfig(**{**config.__dict__, "capture_latency_samples": True})
    started = time.perf_counter()
    final_sink = JsonlSink(Path(args.out)) if args.out else ListSink()
    outbox = DurableOutbox(Path(args.outbox)) if args.outbox else None
    sink = OutboxSink(outbox) if outbox else final_sink
    runtime = EdgeRuntime.build(config, sink)
    startup_ms = (time.perf_counter() - started) * 1000.0
    health_server = None
    if args.health_port is not None:
        health_server = await start_health_server(runtime, args.health_host, args.health_port)

    ingested = 0
    wall_start = time.perf_counter()
    prev_tick = None
    first_inference_ms = None
    for _, group in itertools.groupby(
        read_jsonl(Path(args.events)), key=lambda e: e["observation_time"]
    ):
        tick = list(group)
        tick_time = parse_ts(tick[0]["observation_time"])
        if args.pace and prev_tick is not None:
            await asyncio.sleep(max(0.0, (tick_time - prev_tick).total_seconds() / args.pace))
        prev_tick = tick_time
        for event in tick:
            runtime.ingest(event, parse_ts(event["ingest_time"]) + timedelta(seconds=INGEST_LAG_S))
            ingested += 1
        runtime.evaluate_due(tick_time + timedelta(seconds=config.eval_delay_s + 0.1))
        if first_inference_ms is None and runtime.sample_model_ms:
            first_inference_ms = runtime.sample_model_ms[0]
        await asyncio.sleep(0)  # let the health server run between ticks
    wall_s = time.perf_counter() - wall_start

    outbox_report = None
    if outbox is not None:
        drain_result = drain(outbox, lambda payload: (final_sink.emit(payload), True)[1])
        outbox_report = {**drain_result, "counts_after_drain": outbox.counts()}
        outbox.close()

    report = {
        "startup_ms": round(startup_ms, 3),
        "events_ingested": ingested,
        "wall_seconds": round(wall_s, 3),
        "events_per_second": round(ingested / wall_s, 1) if wall_s else None,
        "health": runtime.health().to_dict(),
        "cold_first_model_inference_ms": None
        if first_inference_ms is None
        else round(first_inference_ms, 4),
        "warm_model_inference_ms": _percentiles(runtime.sample_model_ms[1:]),
        "decision_latency_ms": _percentiles(runtime.sample_decision_ms),
        "system": system_snapshot(),
        "metrics_series": runtime.metrics.series_count(),
        "outbox": outbox_report,
        "emitted_events": getattr(final_sink, "count", len(getattr(final_sink, "events", []))),
        "python": sys.version.split()[0],
        "cpu_count_visible": os.cpu_count(),
    }
    if health_server is not None:
        health_server.close()
        await health_server.wait_closed()
    if isinstance(final_sink, JsonlSink):
        final_sink.close()
    if args.report:
        Path(args.report).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="edge.main")
    sub = parser.add_subparsers(dest="command", required=True)
    rp = sub.add_parser("replay", help="replay recorded telemetry through the runtime")
    rp.add_argument("--config", required=True)
    rp.add_argument("--events", required=True)
    rp.add_argument("--out")
    rp.add_argument("--report")
    rp.add_argument("--health-host", default="127.0.0.1")
    rp.add_argument("--health-port", type=int)
    rp.add_argument(
        "--pace",
        type=float,
        default=0.0,
        help="simulated-seconds per real second; 0 = as fast as possible",
    )
    rp.add_argument("--capture-latency", action="store_true")
    rp.add_argument(
        "--outbox",
        help="durable SQLite outbox path; if set, events are buffered there and drained to --out at the end of the run",
    )
    args = parser.parse_args(argv)
    if args.command == "replay":
        return asyncio.run(replay_command(args))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
