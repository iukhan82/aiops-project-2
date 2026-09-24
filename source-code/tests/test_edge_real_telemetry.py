"""P04.01: run the edge validator and feature builder over the real P03.03
simulated telemetry (skipped when that gitignored output is absent)."""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from edge.features import FEATURE_NAMES, FeatureBuilder
from edge.timeutil import parse_ts
from edge.validation import DeviceRegistry, EdgeValidator, Status, ValidationConfig

RUN_A = Path(__file__).resolve().parents[1] / "simulator" / "sensors" / "output" / "run-a"
DEVICES = RUN_A / "devices.jsonl"
EVENTS = RUN_A / "observations.jsonl"


@pytest.fixture(scope="module")
def replayed():
    if not (DEVICES.is_file() and EVENTS.is_file()):
        pytest.skip("P03.03 sensor output not generated (run sensors/run_container.sh)")
    registry = DeviceRegistry.from_jsonl(DEVICES)
    validator = EdgeValidator(registry, ValidationConfig(expected_geometry_version="2026-09-18.1"))
    builder = FeatureBuilder()
    verdicts = []
    events = [json.loads(line) for line in EVENTS.read_text(encoding="utf-8").splitlines() if line]
    for event in events:
        now = parse_ts(event["ingest_time"])
        verdict = validator.validate(event, now)
        verdicts.append((event, verdict))
        builder.update(verdict)
    return registry, builder, verdicts


def test_real_simulated_telemetry_has_no_rejections(replayed) -> None:
    _, _, verdicts = replayed
    rejected = [(e["event_id"], v.reasons) for e, v in verdicts if v.status is Status.REJECTED]
    assert rejected == []
    assert len(verdicts) > 1000


def test_real_loop_devices_yield_full_feature_windows(replayed) -> None:
    registry, builder, verdicts = replayed
    loop_events = [
        e for e, v in verdicts if e["event_type"].startswith("traffic.loop_detector.") and v.usable
    ]
    device_id = loop_events[0]["device_id"]
    last_obs = max(
        parse_ts(e["observation_time"]) for e in loop_events if e["device_id"] == device_id
    )
    result = builder.build(device_id, last_obs)
    assert result.quality in {"ok", "degraded"}
    assert result.values is not None and len(result.values) == len(FEATURE_NAMES)
    assert set(result.source_event_ids) <= {e["event_id"] for e in loop_events}
    assert result.as_of == last_obs
    assert last_obs - result.window_start <= timedelta(seconds=150)
