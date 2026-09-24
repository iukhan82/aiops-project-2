"""P04.05: edge runtime - real ONNX inference, abstention, fallback, metrics,
health and bounded behavior."""

import asyncio
import json
import shutil
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
from edge_helpers import T0, event, make_chain_registry

from edge.features import FEATURE_VERSION
from edge.health import start_health_server
from edge.model_runtime import InferenceError
from edge.replay import replay_events
from edge.runtime import EdgeConfig, EdgeRuntime, ListSink, MemorySequence
from edge.timeutil import parse_ts

REGISTRY = Path(__file__).resolve().parents[1] / "models" / "registry"
PACKAGE = REGISTRY / "traffic-safety-blockage" / "1.0.0"
BASELINE = REGISTRY / "baseline" / "baseline_v1.json"
STEP = 30


@pytest.fixture()
def registry_file(tmp_path: Path) -> Path:
    path = tmp_path / "devices.jsonl"
    path.write_text(
        "\n".join(json.dumps(d) for d in make_chain_registry().all()) + "\n", encoding="utf-8"
    )
    return path


def _config(registry_file: Path, **overrides) -> EdgeConfig:
    values = {
        "site_id": "corridor-a",
        "runtime_device_id": "edge-runtime-corridor-a",
        "geometry_version": "2026-09-18.1",
        "registry_path": registry_file,
        "baseline_path": BASELINE,
        "model_dir": PACKAGE,
        "boot_id": "test-boot",
    }
    values.update(overrides)
    return EdgeConfig(**values)


def _runtime(registry_file, **overrides) -> tuple[EdgeRuntime, ListSink]:
    sink = ListSink()
    runtime = EdgeRuntime.build(_config(registry_file, **overrides), sink, MemorySequence())
    return runtime, sink


def _series(device, counts, occs, speeds=None, start=1, seq_start=0):
    speeds = speeds or [10.0 if c else None for c in counts]
    return [
        event(
            device,
            seq=seq_start + i,
            obs=T0 + timedelta(seconds=STEP * (start + i)),
            count=c,
            occ=o,
            speed=s,
        )
        for i, (c, o, s) in enumerate(zip(counts, occs, speeds, strict=True))
    ]


def _blocked_scenario():
    """e2 saturated with no flow, its downstream e3 starved, upstream e1 flowing."""
    return (
        _series("loop-e1", [3, 3, 3, 3], [0.05] * 4)
        + _series("loop-e2", [2, 1, 0, 0], [0.05, 0.3, 0.8, 0.9], [8.0, 2.0, None, None])
        + _series("loop-e3", [3, 0, 0, 0], [0.05, 0.0, 0.0, 0.0], [10.0, None, None, None])
    )


def _normal_scenario():
    return (
        _series("loop-e1", [3, 3, 3, 3], [0.03] * 4)
        + _series("loop-e2", [3, 3, 3, 3], [0.03] * 4)
        + _series("loop-e3", [3, 3, 3, 3], [0.03] * 4)
    )


def _replay(runtime, events):
    return replay_events(
        runtime, sorted(events, key=lambda e: (e["observation_time"], e["device_id"]))
    )


# --- startup, health, readiness -------------------------------------------------------


def test_startup_with_verified_model_is_ready_and_not_degraded(registry_file) -> None:
    runtime, _ = _runtime(registry_file)
    health = runtime.health()
    assert health.live and health.ready and not health.degraded
    assert health.mode == "model" and health.reasons == ()
    assert health.model.startswith("traffic-safety-blockage/1.0.0@sha256:")
    assert "edge_model_active 1" in runtime.render_metrics()


def test_missing_model_falls_back_to_baseline_and_says_so(registry_file, tmp_path) -> None:
    runtime, _ = _runtime(registry_file, model_dir=tmp_path / "no-such-package")
    health = runtime.health()
    assert health.ready and health.degraded and health.mode == "baseline"
    assert "model_unavailable:artifact_missing" in health.reasons
    assert 'edge_model_errors_total{code="artifact_missing"} 1' in runtime.render_metrics()


def test_tampered_model_is_refused_and_baseline_serves(registry_file, tmp_path) -> None:
    bad = tmp_path / "pkg"
    shutil.copytree(PACKAGE, bad)
    data = bytearray((bad / "model.onnx").read_bytes())
    data[10] ^= 0xFF
    (bad / "model.onnx").write_bytes(bytes(data))
    runtime, sink = _runtime(registry_file, model_dir=bad)
    assert runtime.health().mode == "baseline"
    assert "model_unavailable:integrity_mismatch" in runtime.health().reasons
    inferences = _replay(runtime, _blocked_scenario())
    assert {i.mode for i in inferences} == {"baseline"}
    assert sink.events  # still produces candidates while degraded


def test_no_model_and_no_baseline_is_not_ready_and_abstains_explicitly(
    registry_file, tmp_path
) -> None:
    runtime, sink = _runtime(registry_file, model_dir=None, baseline_path=tmp_path / "missing.json")
    health = runtime.health()
    assert not health.ready and health.mode == "none"
    assert any(r.startswith("baseline_unavailable") for r in health.reasons)
    inferences = _replay(runtime, _normal_scenario())
    assert inferences and all(i.decision == "abstain" for i in inferences)
    assert all(i.abstain_reason in {"no_decision_path", "insufficient_data"} for i in inferences)
    assert all(
        any(m["name"] == "abstained" and m["value"] is True for m in e["measurements"])
        for e in sink.events
    )


def test_liveness_fails_when_heartbeat_goes_stale(registry_file) -> None:
    runtime, _ = _runtime(registry_file, liveness_timeout_s=5.0)
    assert runtime.health().live
    runtime._last_heartbeat -= 60
    assert not runtime.health().live
    runtime.heartbeat()
    assert runtime.health().live


# --- inference ------------------------------------------------------------------------


def test_blockage_pattern_raises_a_verifiable_candidate_event(registry_file) -> None:
    from edge.contracts import load_validator

    runtime, sink = _runtime(registry_file)
    source_events = _blocked_scenario()
    inferences = _replay(runtime, source_events)
    alarms = [i for i in inferences if i.decision == "alarm"]
    assert alarms, "a saturated no-flow loop with a starved downstream must alarm"
    assert all(i.mode == "model" and i.probability is not None for i in alarms)

    schema = load_validator("observation-envelope")
    for out in sink.events:
        schema.validate(out)  # derived events honor the same contract as sensor events
        assert out["truth_label"] == "inferred"
        assert out["event_type"] == "edge.inference.blockage_candidate"
        assert out["device_id"] == "edge-runtime-corridor-a"
        assert out["ingest_time"] >= out["observation_time"]
    seqs = [e["sequence_number"] for e in sink.events]
    assert seqs == list(range(len(seqs)))

    alarm_event = next(
        e
        for e in sink.events
        if any(m["name"] == "decision" and m["value"] == "alarm" for m in e["measurements"])
    )
    sources = set(alarm_event["provenance"]["source_event_ids"])
    assert sources and sources <= {e["event_id"] for e in source_events}
    assert (
        "model=traffic-safety-blockage/1.0.0@sha256:"
        in alarm_event["provenance"]["pipeline_version"]
    )
    assert f"features={FEATURE_VERSION}" in alarm_event["provenance"]["pipeline_version"]
    prob = next(m for m in alarm_event["measurements"] if m["name"] == "blockage_probability")
    assert 0.5 <= prob["confidence"] <= 1.0 and prob["unit"] == "ratio"


def test_normal_flow_is_clear_and_silent_unless_emit_clear(registry_file) -> None:
    quiet, sink = _runtime(registry_file)
    inferences = _replay(quiet, _normal_scenario())
    assert {i.decision for i in inferences} <= {"clear", "abstain"}
    assert not any(i.decision == "alarm" for i in inferences)
    assert not [e for e in sink.events if any(m["value"] == "alarm" for m in e["measurements"])]

    chatty, sink2 = _runtime(registry_file, emit_clear=True)
    _replay(chatty, _normal_scenario())
    assert len(sink2.events) == len(_replay(_runtime(registry_file)[0], _normal_scenario()))


def test_too_little_data_abstains_with_reason_not_a_guess(registry_file) -> None:
    runtime, sink = _runtime(registry_file)
    inferences = _replay(runtime, _series("loop-e2", [9], [0.9]))
    assert [(i.decision, i.abstain_reason) for i in inferences] == [
        ("abstain", "insufficient_data")
    ]
    out = sink.events[0]
    assert any(
        m["name"] == "abstain_reason" and m["value"] == "insufficient_data"
        for m in out["measurements"]
    )


def test_degraded_features_are_marked_suspect_in_the_output(registry_file) -> None:
    runtime, sink = _runtime(registry_file, emit_clear=True)
    _replay(runtime, _normal_scenario()[:0] + _series("loop-e2", [3, 3, 3, 3], [0.03] * 4))
    last = sink.events[-1]  # neighbors silent: expected-but-missing context -> degraded
    quality = next(m for m in last["measurements"] if m["name"] == "feature_quality")
    assert quality["value"] == "degraded"
    assert next(m for m in last["measurements"] if m["name"] == "decision")["quality"] == "suspect"


def test_event_ids_are_deterministic_for_a_fixed_boot_id(registry_file) -> None:
    first, s1 = _runtime(registry_file)
    second, s2 = _runtime(registry_file)
    _replay(first, _blocked_scenario())
    _replay(second, _blocked_scenario())
    assert [e["event_id"] for e in s1.events] == [e["event_id"] for e in s2.events]
    third, s3 = _runtime(registry_file, boot_id="another-boot")
    _replay(third, _blocked_scenario())
    assert {e["event_id"] for e in s3.events}.isdisjoint({e["event_id"] for e in s1.events})


def test_evaluation_waits_for_eval_delay_and_never_double_evaluates(registry_file) -> None:
    runtime, _ = _runtime(registry_file, eval_delay_s=2.0)
    events = _series("loop-e2", [1, 1, 1, 1], [0.01] * 4)
    for e in events:
        runtime.ingest(e, parse_ts(e["ingest_time"]) + timedelta(seconds=0.5))
    boundary = parse_ts(events[-1]["observation_time"])
    assert runtime.evaluate_due(boundary + timedelta(seconds=1)) == []
    assert len(runtime.evaluate_due(boundary + timedelta(seconds=2.1))) == 1
    assert runtime.evaluate_due(boundary + timedelta(seconds=30)) == []


# --- failure handling -------------------------------------------------------------------


def test_repeated_inference_errors_disable_the_model_and_fall_back(
    registry_file, monkeypatch
) -> None:
    runtime, sink = _runtime(registry_file, max_consecutive_model_errors=3)

    def boom(_values):
        raise InferenceError("simulated numerical failure")

    monkeypatch.setattr(runtime.model, "predict_proba", boom)
    _replay(runtime, _blocked_scenario())
    health = runtime.health()
    assert runtime.model is None and health.mode == "baseline" and health.degraded
    assert "model_disabled:consecutive_inference_errors" in health.reasons
    text = runtime.render_metrics()
    assert 'edge_model_errors_total{code="inference_failed"}' in text
    assert 'edge_model_errors_total{code="consecutive_errors"} 1' in text
    assert "edge_model_active 0" in text
    assert sink.events  # never silent: baseline alarm or explicit model_inference_error abstention


def test_swapping_the_model_out_and_back_restores_model_mode(registry_file) -> None:
    runtime, _ = _runtime(registry_file)
    model = runtime.model
    runtime.swap_model(None, "operator_rollback")
    assert runtime.health().mode == "baseline"
    runtime.swap_model(model)
    assert runtime.health().mode == "model" and not runtime.health().degraded


# --- metrics --------------------------------------------------------------------------


def test_validation_outcomes_reasons_and_flags_are_counted(registry_file) -> None:
    runtime, _ = _runtime(registry_file)
    now = T0 + timedelta(seconds=60)
    good = event("loop-e2", seq=0, obs=T0 + timedelta(seconds=30))
    runtime.ingest(good, now)
    runtime.ingest(good, now)  # duplicate
    runtime.ingest({"junk": 1}, now)  # schema
    runtime.ingest(event("loop-zzz", seq=0, obs=T0 + timedelta(seconds=30)), now)  # unknown device
    runtime.ingest(event("loop-e1", seq=0, obs=T0, quality="suspect"), now)  # flagged
    text = runtime.render_metrics()
    assert 'edge_events_total{status="accepted"} 1' in text
    assert 'edge_events_total{status="rejected"} 3' in text
    assert 'edge_events_rejected_total{reason="duplicate_event_id"} 1' in text
    assert 'edge_events_rejected_total{reason="schema_invalid"} 1' in text
    assert 'edge_events_rejected_total{reason="unknown_device"} 1' in text
    assert 'edge_events_flagged_total{flag="measurement_suspect"}' in text


def test_metric_cardinality_stays_bounded_under_hostile_input(registry_file) -> None:
    runtime, _ = _runtime(registry_file)
    now = T0 + timedelta(seconds=60)
    for i in range(1500):
        runtime.ingest(event(f"loop-fuzz-{i}", seq=i, obs=T0), now)  # 1500 distinct device ids
    runtime.ingest({"x": i for i in range(10)}, now)
    text = runtime.render_metrics()
    assert "loop-fuzz" not in text  # device ids are never labels
    assert runtime.metrics.series_count() < 200
    before = runtime.metrics.series_count()
    for i in range(1500, 3000):
        runtime.ingest(event(f"loop-fuzz-{i}", seq=i, obs=T0), now)
    assert runtime.metrics.series_count() == before


def test_prometheus_exposition_is_well_formed(registry_file) -> None:
    runtime, _ = _runtime(registry_file)
    _replay(runtime, _blocked_scenario())
    text = runtime.render_metrics()
    assert text.endswith("\n")
    for line in text.splitlines():
        assert line.startswith("#") or " " in line
    assert "# TYPE edge_model_inference_latency_ms histogram" in text
    assert 'edge_model_inference_latency_ms_bucket{le="+Inf"}' in text
    assert "edge_decision_latency_ms_count" in text
    assert runtime.hist_model.count > 0 and runtime.hist_model.quantile(0.95) <= 100


def test_model_inference_latency_is_far_below_the_100ms_budget(registry_file) -> None:
    runtime, _ = _runtime(registry_file)
    _replay(runtime, _blocked_scenario() * 1)
    latencies = []
    model = runtime.model
    vector = np.zeros(24, dtype=np.float32)
    for _ in range(300):
        latencies.append(model.infer(vector)[2])
    assert float(np.percentile(latencies, 95)) < 100.0


# --- HTTP health endpoints ------------------------------------------------------------


async def _http(port: int, raw: bytes) -> tuple[int, dict, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(raw)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(-1), 5)
    writer.close()
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    status = int(lines[0].split(" ")[1])
    headers = {k.lower(): v for k, v in (ln.split(": ", 1) for ln in lines[1:])}
    return status, headers, body


def test_http_endpoints_report_health_readiness_and_metrics(registry_file, tmp_path) -> None:
    async def scenario():
        runtime, _ = _runtime(registry_file)
        server = await start_health_server(runtime, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            status, headers, body = await _http(port, b"GET /healthz HTTP/1.1\r\n\r\n")
            assert status == 200 and json.loads(body)["live"] is True
            assert headers["cache-control"] == "no-store"
            status, _, body = await _http(port, b"GET /readyz HTTP/1.1\r\n\r\n")
            assert status == 200 and json.loads(body)["ready"] is True
            status, headers, body = await _http(port, b"GET /metrics HTTP/1.1\r\n\r\n")
            assert status == 200 and b"edge_model_active 1" in body
            assert "text/plain" in headers["content-type"]
            assert (await _http(port, b"GET /nope HTTP/1.1\r\n\r\n"))[0] == 404
            assert (await _http(port, b"POST /metrics HTTP/1.1\r\n\r\n"))[0] == 405
            assert (await _http(port, b"garbage\r\n"))[0] == 400
            assert (await _http(port, b"GET /" + b"a" * 8000 + b" HTTP/1.1\r\n\r\n"))[0] == 400
            # a client that never finishes its request line is dropped by the read timeout
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /health")
            await writer.drain()
            data = await asyncio.wait_for(reader.read(-1), 6)
            assert data.startswith(b"HTTP/1.1 400")
            writer.close()
        finally:
            server.close()
            await server.wait_closed()

        broken = EdgeRuntime.build(
            _config(registry_file, model_dir=None, baseline_path=tmp_path / "gone.json"),
            ListSink(),
        )
        server2 = await start_health_server(broken, "127.0.0.1", 0)
        port2 = server2.sockets[0].getsockname()[1]
        try:
            status, _, body = await _http(port2, b"GET /readyz HTTP/1.1\r\n\r\n")
            assert status == 503 and json.loads(body)["ready"] is False
            assert (await _http(port2, b"GET /healthz HTTP/1.1\r\n\r\n"))[
                0
            ] == 200  # alive, not ready
        finally:
            server2.close()
            await server2.wait_closed()

    asyncio.run(scenario())
