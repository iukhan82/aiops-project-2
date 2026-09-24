"""P04.01: corridor topology and corridor-context features (features v2)."""

from datetime import timedelta

from edge_helpers import GEOMETRY, T0, event, make_chain_registry, make_registry

from edge.features import FEATURE_NAMES, FeatureBuilder
from edge.timeutil import parse_ts
from edge.topology import Neighbors, loop_topology
from edge.validation import EdgeValidator, ValidationConfig

STEP = 30


def _validator() -> EdgeValidator:
    return EdgeValidator(
        make_chain_registry(), ValidationConfig(expected_geometry_version=GEOMETRY)
    )


def _series(device, counts, occs, speeds=None):
    speeds = speeds or [10.0 if c else None for c in counts]
    return [
        event(device, seq=i, obs=T0 + timedelta(seconds=STEP * (i + 1)), count=c, occ=o, speed=s)
        for i, (c, o, s) in enumerate(zip(counts, occs, speeds, strict=True))
    ]


def _feed(builder, validator, events):
    for e in sorted(events, key=lambda e: e["observation_time"]):
        now = parse_ts(e["observation_time"]) + timedelta(seconds=1)
        builder.update(validator.validate(e, now))


def _as_of(n):
    return T0 + timedelta(seconds=STEP * n)


def _named(result):
    return dict(zip(result.names, result.values, strict=True))


def test_topology_is_derived_from_lane_ids_and_ignores_u_turns() -> None:
    topo = loop_topology(make_chain_registry())
    assert topo["loop-e1"] == Neighbors(upstream=None, downstream="loop-e2")
    assert topo["loop-e2"] == Neighbors(upstream="loop-e1", downstream="loop-e3")
    assert topo["loop-e3"] == Neighbors(upstream="loop-e2", downstream=None)
    assert topo["loop-w2"] == Neighbors(upstream=None, downstream=None)
    assert "cyc-e1" not in topo  # cycle counters are not vehicle loops


def test_registry_without_lane_ids_has_empty_topology() -> None:
    assert loop_topology(make_registry()) == {}


def test_real_network_topology_is_consistent_when_available() -> None:
    from pathlib import Path

    from edge.validation import DeviceRegistry

    devices = (
        Path(__file__).resolve().parents[1] / "simulator" / "sensors" / "output" / "run-a"
    ) / "devices.jsonl"
    if not devices.is_file():
        import pytest

        pytest.skip("sensor catalog not generated")
    topo = loop_topology(DeviceRegistry.from_jsonl(devices))
    assert len(topo) == 18
    for device_id, n in topo.items():
        if n.downstream:  # symmetric: my downstream's upstream is me
            assert topo[n.downstream].upstream == device_id
        if n.upstream:
            assert topo[n.upstream].downstream == device_id
    assert sum(1 for n in topo.values() if n.downstream is None) == 6  # 3 corridors x 2 dirs


def test_context_features_use_neighbor_windows_with_separate_lineage() -> None:
    v = _validator()
    b = FeatureBuilder(topology=loop_topology(make_chain_registry()))
    e1 = _series("loop-e1", [1, 1, 1, 1], [0.01] * 4)
    e2 = _series("loop-e2", [2, 2, 2, 2], [0.02, 0.02, 0.02, 0.5])
    e3 = _series("loop-e3", [0, 0, 0, 0], [0.0] * 4)
    for events in (e1, e2, e3):
        _feed(b, v, events)

    result = b.build("loop-e2", _as_of(4))
    f = _named(result)
    assert result.quality == "ok"
    assert f["down_available"] == 1 and f["up_available"] == 1
    assert f["down_count_mean"] == 0 and f["down_zero_flow_run"] == 4
    assert f["count_gap_down"] == 2.0  # own mean 2 vs starved downstream 0
    assert f["up_count_mean"] == 1.0 and f["up_occ_last"] == 0.01
    assert set(result.context_event_ids) == {"downstream", "upstream"}
    assert result.context_event_ids["downstream"] == tuple(e["event_id"] for e in e3)
    assert result.context_event_ids["upstream"] == tuple(e["event_id"] for e in e1)
    assert not set(result.source_event_ids) & {e["event_id"] for e in e1 + e3}
    assert result.source_event_ids == tuple(e["event_id"] for e in e2)


def test_corridor_ends_are_structural_not_degraded() -> None:
    v = _validator()
    b = FeatureBuilder(topology=loop_topology(make_chain_registry()))
    _feed(b, v, _series("loop-e1", [1] * 4, [0.01] * 4))
    _feed(b, v, _series("loop-e2", [1] * 4, [0.01] * 4))
    first = b.build("loop-e1", _as_of(4))  # no upstream loop exists at all
    assert first.quality == "ok" and first.context_missing == ()
    assert _named(first)["up_available"] == 0 and _named(first)["down_available"] == 1


def test_expected_but_unusable_neighbor_degrades_and_is_named_in_lineage() -> None:
    v = _validator()
    b = FeatureBuilder(topology=loop_topology(make_chain_registry()))
    _feed(b, v, _series("loop-e2", [1] * 4, [0.01] * 4))
    _feed(b, v, _series("loop-e3", [0] * 4, [0.0] * 4))  # downstream ok, upstream e1 silent
    result = b.build("loop-e2", _as_of(4))
    assert result.quality == "degraded"
    assert result.context_missing == ("upstream",)
    f = _named(result)
    assert f["up_available"] == 0 and f["up_count_mean"] == 0 and f["down_available"] == 1
    assert "upstream" not in result.context_event_ids


def test_neighbor_context_is_past_only_too() -> None:
    v = _validator()
    b = FeatureBuilder(topology=loop_topology(make_chain_registry()))
    _feed(b, v, _series("loop-e2", [2, 2, 2, 2], [0.02] * 4))
    _feed(b, v, _series("loop-e3", [3, 3, 3, 3], [0.03] * 4))
    before = b.build("loop-e2", _as_of(4))

    future_e3 = [
        event("loop-e3", seq=4 + i, obs=T0 + timedelta(seconds=STEP * (5 + i)), count=0, occ=0.9)
        for i in range(3)
    ]
    _feed(b, v, future_e3)
    assert b.build("loop-e2", _as_of(4)) == before
    assert not set(before.context_event_ids["downstream"]) & {e["event_id"] for e in future_e3}


def test_opposite_direction_loops_do_not_leak_into_each_other() -> None:
    v = _validator()
    b = FeatureBuilder(topology=loop_topology(make_chain_registry()))
    _feed(b, v, _series("loop-w2", [5] * 4, [0.2] * 4))
    _feed(b, v, _series("loop-e2", [1] * 4, [0.01] * 4))
    f = _named(b.build("loop-w2", _as_of(4)))
    assert f["down_available"] == 0 and f["up_available"] == 0
    assert len(FEATURE_NAMES) == 24 and FEATURE_NAMES[16] == "down_available"
