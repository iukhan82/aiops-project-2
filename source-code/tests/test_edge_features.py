"""P04.01: past-only feature extraction, missing/stale handling, lineage."""

import random
from datetime import timedelta

from edge_helpers import T0, event, make_validator

from edge.features import FEATURE_NAMES, FEATURE_VERSION, FeatureBuilder, FeatureConfig
from edge.timeutil import parse_ts

STEP = 30


def _feed(builder, validator, events, now_offset=1.0):
    accepted = []
    for e in events:
        now = parse_ts(e["observation_time"]) + timedelta(seconds=now_offset)
        accepted.append(builder.update(validator.validate(e, now)))
    return accepted


def _series(counts, occs, speeds, device="loop-a", start=1):
    return [
        event(
            device, seq=i, obs=T0 + timedelta(seconds=STEP * (start + i)), count=c, occ=o, speed=s
        )
        for i, (c, o, s) in enumerate(zip(counts, occs, speeds, strict=True))
    ]


def _as_of(n_intervals):
    return T0 + timedelta(seconds=STEP * n_intervals)


def _named(result):
    return dict(zip(result.names, result.values, strict=True))


def test_full_window_matches_hand_computed_values_and_lineage() -> None:
    v, b = make_validator(), FeatureBuilder()
    events = _series([2, 4, 6, 8], [0.1, 0.2, 0.3, 0.6], [12.0, 10.0, 8.0, 2.0])
    assert all(_feed(b, v, events))
    result = b.build("loop-a", _as_of(4))

    assert result.quality == "ok"
    assert result.names == FEATURE_NAMES
    assert result.feature_version == FEATURE_VERSION
    assert len(result.values) == len(FEATURE_NAMES) == 24
    f = _named(result)
    assert f["count_last"] == 8 and f["count_mean"] == 5 and f["count_max"] == 8
    assert f["count_delta"] == 8 - (2 + 4 + 6) / 3
    assert f["occ_last"] == 0.6 and abs(f["occ_mean"] - 0.3) < 1e-9 and f["occ_max"] == 0.6
    assert f["speed_last"] == 2.0 and f["speed_min"] == 2.0
    assert abs(f["speed_mean"] - 8.0) < 1e-9
    assert f["speed_delta"] == 2.0 - (12 + 10 + 8) / 3
    assert f["zero_flow_run"] == 0 and f["high_occ_run"] == 1
    assert f["missing_fraction"] == 0 and f["speed_imputed_fraction"] == 0
    assert result.source_event_ids == tuple(e["event_id"] for e in events)
    assert result.missing_intervals == 0 and result.imputed == ()


def test_features_are_past_only_future_events_change_nothing() -> None:
    v, b = make_validator(), FeatureBuilder()
    past = _series([1, 2, 3, 4], [0.1] * 4, [11] * 4)
    _feed(b, v, past)
    before = b.build("loop-a", _as_of(4))

    future = _series([99, 99, 99], [0.99] * 3, [0.1] * 3, start=5)
    for i, e in enumerate(future):
        e["sequence_number"] = 4 + i
    _feed(b, v, future)
    after = b.build("loop-a", _as_of(4))

    assert after == before
    assert not set(after.source_event_ids) & {e["event_id"] for e in future}
    later = b.build("loop-a", _as_of(7))  # once as_of advances they are past
    assert later.values != before.values


def test_arrival_order_does_not_change_features() -> None:
    events = _series([3, 1, 4, 1], [0.2, 0.1, 0.4, 0.1], [9, 12, 5, 12])
    results = []
    for seed in range(5):
        shuffled = events[:]
        random.Random(seed).shuffle(shuffled)
        b = FeatureBuilder()
        for e in shuffled:
            # fresh validator per event: bypasses sequence ordering so this
            # isolates the feature builder's own order-independence
            b.update(make_validator().validate(e, T0 + timedelta(seconds=STEP * 5)))
        results.append(b.build("loop-a", _as_of(4)))
    assert all(r.values == results[0].values for r in results)
    assert all(r.source_event_ids == results[0].source_event_ids for r in results)


def test_missing_interval_is_counted_marked_degraded_not_invented() -> None:
    v, b = make_validator(), FeatureBuilder()
    events = _series([2, 4, 6, 8], [0.1] * 4, [10] * 4)
    del events[1]  # interval 2 never arrived
    _feed(b, v, events)
    result = b.build("loop-a", _as_of(4))
    assert result.quality == "degraded"
    assert result.missing_intervals == 1
    f = _named(result)
    assert f["missing_fraction"] == 0.25
    assert len(result.source_event_ids) == 3
    assert f["count_mean"] == (2 + 6 + 8) / 3  # mean over present intervals only


def test_too_few_intervals_is_insufficient_and_has_no_vector() -> None:
    v, b = make_validator(), FeatureBuilder()
    _feed(b, v, _series([5], [0.1], [9]))
    result = b.build("loop-a", _as_of(1))
    assert result.quality == "insufficient" and result.values is None
    assert b.build("loop-unknown", _as_of(1)).quality == "insufficient"


def test_stale_and_rejected_events_never_enter_windows() -> None:
    v, b = make_validator(), FeatureBuilder()
    old = event(seq=0, obs=T0 + timedelta(seconds=STEP))
    stale = v.validate(old, T0 + timedelta(seconds=STEP + 600))
    assert stale.usable and not stale.fresh
    assert b.update(stale) is False and b.skipped_not_fresh == 1

    rejected = v.validate({"junk": True}, T0)
    assert b.update(rejected) is False


def test_zero_traffic_speed_is_imputed_and_lineage_says_so() -> None:
    v, b = make_validator(), FeatureBuilder(FeatureConfig(free_flow_speed_m_s=13.9))
    _feed(b, v, _series([0, 0, 0, 0], [0.0] * 4, [None] * 4))
    result = b.build("loop-a", _as_of(4))
    f = _named(result)
    assert f["speed_last"] == f["speed_mean"] == f["speed_min"] == 13.9
    assert f["speed_imputed_fraction"] == 1.0
    assert f["zero_flow_run"] == 4
    assert set(result.imputed) == {"speed_last", "speed_mean", "speed_min"}


def test_masked_measurements_leave_window_insufficient() -> None:
    v, b = make_validator(), FeatureBuilder()
    events = [
        event(seq=i, obs=T0 + timedelta(seconds=STEP * (i + 1)), quality="suspect")
        for i in range(4)
    ]
    _feed(b, v, events)  # all measurements suspect -> masked -> no usable count/occ
    assert b.build("loop-a", _as_of(4)).quality == "insufficient"


def test_runs_break_on_missing_interval() -> None:
    v, b = make_validator(), FeatureBuilder()
    events = _series([5, 5, 0, 0], [0.5, 0.5, 0.9, 0.9], [4, 4, None, None])
    del events[1]
    _feed(b, v, events)
    f = _named(b.build("loop-a", _as_of(4)))
    assert f["zero_flow_run"] == 2
    assert f["high_occ_run"] == 2


def test_history_is_bounded() -> None:
    v, b = make_validator(), FeatureBuilder(FeatureConfig(history_capacity=6))
    _feed(b, v, _series([1] * 40, [0.1] * 40, [9] * 40))
    assert len(b._history["loop-a"]) == 6
    assert b.build("loop-a", _as_of(40)).quality == "ok"


def test_devices_are_isolated() -> None:
    v, b = make_validator(), FeatureBuilder()
    _feed(b, v, _series([1, 1, 1, 1], [0.1] * 4, [9] * 4, device="loop-a"))
    _feed(b, v, _series([7, 7, 7, 7], [0.7] * 4, [3] * 4, device="loop-b"))
    assert _named(b.build("loop-a", _as_of(4)))["count_mean"] == 1
    assert _named(b.build("loop-b", _as_of(4)))["count_mean"] == 7
