"""P10.08: the bounded trace-sampling override."""

import json
import random

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from backend import observability, trace_sampling as ts


def test_no_file_a_malformed_file_and_an_expired_file_all_mean_sample_everything(tmp_path):
    path = tmp_path / "s.json"
    assert ts.read_override(path) == 1.0
    path.write_text("not json", encoding="utf-8")
    assert ts.read_override(path) == 1.0
    ts.write_override(0.2, 60, "test", path, now=1000.0)
    assert ts.read_override(path, now=1030.0) == 0.2
    assert ts.read_override(path, now=1061.0) == 1.0  # expired without anyone clearing it


def test_a_hand_edited_file_outside_the_bounds_is_ignored(tmp_path):
    path = tmp_path / "s.json"
    for doc in (
        {"ratio": 0.0, "expires_at": 5000},  # would drop every trace
        {"ratio": 0.001, "expires_at": 5000},
        {
            "ratio": 0.5,
            "expires_at": 1000 + ts.MAX_TTL_S * 10,
        },  # an override that never really expires
        {"ratio": "half", "expires_at": 5000},
    ):
        path.write_text(json.dumps(doc), encoding="utf-8")
        assert ts.read_override(path, now=1000.0) == 1.0


def test_writing_an_override_outside_the_bounds_is_refused_not_clamped(tmp_path):
    path = tmp_path / "s.json"
    for ratio, ttl in ((0.0, 60), (0.005, 60), (1.5, 60), (0.5, 0), (0.5, ts.MAX_TTL_S + 1)):
        with pytest.raises(ValueError):
            ts.write_override(ratio, ttl, "test", path)
    assert not path.exists()


def _provider(path, clock):
    from opentelemetry.sdk.trace.sampling import ParentBased

    exporter = observability.InMemorySpanExporter(capacity=100000)
    sampler = ParentBased(ts._OverridableRoot(path, refresh_s=1.0, clock=clock))  # noqa: SLF001
    provider = TracerProvider(sampler=sampler)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def test_the_override_lowers_the_share_of_new_traces_kept_and_expiry_restores_it(tmp_path):
    path = tmp_path / "s.json"
    now = [1000.0]
    provider, exporter = _provider(path, lambda: now[0])
    tracer = provider.get_tracer("t")
    random.seed(7)

    def run(n):
        before = len(exporter.snapshot())
        for _ in range(n):
            with tracer.start_as_current_span("root"):
                pass
        return len(exporter.snapshot()) - before

    assert run(500) == 500  # no override: everything
    ts.write_override(0.1, 60, "test", path, now=now[0])
    now[0] += 2
    kept = run(4000)
    assert 0.06 * 4000 < kept < 0.14 * 4000
    now[0] += 120  # past the expiry
    assert run(500) == 500


def test_a_trace_already_started_keeps_its_decision_at_any_ratio(tmp_path):
    path = tmp_path / "s.json"
    now = [1000.0]
    provider, exporter = _provider(path, lambda: now[0])
    tracer = provider.get_tracer("t")
    with tracer.start_as_current_span("root-before-override"):
        ts.write_override(0.01, 60, "test", path, now=now[0])
        now[0] += 5
        with tracer.start_as_current_span("child"):
            pass
    names = [s.name for s in exporter.snapshot()]
    assert names == ["child", "root-before-override"]  # never half a trace


def test_configure_installs_the_overridable_sampler():
    observability.configure("sampler-check")
    provider = observability._state["tracer_provider"]  # noqa: SLF001
    assert "OverridableRatioSampler" in provider.sampler.get_description()
