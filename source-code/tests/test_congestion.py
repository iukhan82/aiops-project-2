"""P06.04: congestion/spillback rules on hand-built loop events. Held-out
precision/recall and platform behaviour are proven by
backend/analytics/verify_congestion.py (docs/evidence/p06_04_congestion.json)."""

from datetime import datetime, timedelta, timezone

from backend.analytics.congestion import (
    Params,
    SpillParams,
    TruthEpisode,
    detect_congestion,
    detect_spillback,
    match_spans,
    truth_episodes,
    truth_spillbacks,
    wilson,
)
from backend.analytics.topology import Segment

T0 = datetime(2026, 9, 18, 9, 0, 0, tzinfo=timezone.utc)
SEGS = [
    Segment("int-a1_int-a2", "int-a1", "int-a2", "corridor-a", "east", 1, 300.0, 15.0),
    Segment("int-a2_int-a3", "int-a2", "int-a3", "corridor-a", "east", 2, 300.0, 15.0),
    Segment("int-a3_int-a4", "int-a3", "int-a4", "corridor-a", "east", 3, 300.0, 15.0),
]
P = Params(occ_on=0.3, speed_on=8.0, n_on=2, n_off=2)


def ev(edge: str, k: int, occ: float, speed: float | None, count: int = 3) -> dict:
    m = [
        {
            "name": "vehicle_count",
            "value": count,
            "unit": "count",
            "quality": "valid",
            "confidence": 0.9,
        },
        {"name": "occupancy", "value": occ, "unit": "ratio", "quality": "valid", "confidence": 0.9},
    ]
    if speed is not None:
        m.append(
            {
                "name": "mean_speed",
                "value": speed,
                "unit": "m_s-1",
                "quality": "valid",
                "confidence": 0.9,
            }
        )
    return {
        "event_id": f"{edge}-{k}",
        "event_type": "traffic.loop_detector.count",
        "observation_time": (T0 + timedelta(seconds=30 * (k + 1))).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "location": {"lane_id": f"{edge}_3"},
        "measurements": m,
    }


def series(edge: str, pattern: list[tuple[float, float | None]]) -> list[dict]:
    return [ev(edge, k, occ, sp) for k, (occ, sp) in enumerate(pattern)]


FREE, JAM = (0.05, 14.0), (0.7, 1.0)


def test_episode_needs_n_on_consecutive_congested_intervals() -> None:
    assert detect_congestion(series("int-a1_int-a2", [FREE, JAM, FREE, JAM, FREE]), SEGS, P) == []
    (ep,) = detect_congestion(
        series("int-a1_int-a2", [FREE, JAM, JAM, JAM, FREE, FREE, FREE]), SEGS, P
    )
    assert ep.onset == T0 + timedelta(seconds=30) and ep.detected_at == T0 + timedelta(seconds=90)
    assert (
        ep.clear == T0 + timedelta(seconds=120)
        and ep.segment == "int-a1_int-a2"
        and ep.direction == "east"
    )


def test_detected_at_is_the_confirmation_time_not_the_latest_congested_interval() -> None:
    (ep,) = detect_congestion(series("int-a1_int-a2", [JAM] * 8 + [FREE] * 3), SEGS, P)
    assert ep.detected_at == T0 + timedelta(seconds=60) and ep.last_seen == T0 + timedelta(
        seconds=240
    )


def test_an_episode_that_never_clears_stays_open() -> None:
    (ep,) = detect_congestion(series("int-a1_int-a2", [FREE, JAM, JAM, JAM]), SEGS, P)
    assert ep.clear is None and ep.duration_s == 90.0


def test_a_data_gap_closes_the_episode_instead_of_bridging_it() -> None:
    events = series("int-a1_int-a2", [JAM, JAM, JAM]) + [
        ev("int-a1_int-a2", 20, 0.7, 1.0),
        ev("int-a1_int-a2", 21, 0.7, 1.0),
    ]
    assert len(detect_congestion(events, SEGS, P)) == 2


def test_duplicate_events_and_unknown_segments_are_ignored() -> None:
    events = (
        series("int-a1_int-a2", [JAM, JAM, JAM])
        + series("int-a1_int-a2", [JAM, JAM, JAM])
        + series("not-a-segment", [JAM] * 4)
    )
    (ep,) = detect_congestion(events, SEGS, P)
    assert len(ep.evidence) == 3


def test_standing_traffic_without_speed_counts_as_congested() -> None:
    assert len(detect_congestion(series("int-a1_int-a2", [(0.8, None)] * 3), SEGS, P)) == 1


def test_severity_grows_with_occupancy_and_duration() -> None:
    (short,) = detect_congestion(series("int-a1_int-a2", [(0.32, 2.0)] * 2 + [FREE] * 2), SEGS, P)
    (long_,) = detect_congestion(series("int-a1_int-a2", [(0.9, 0.5)] * 8 + [FREE] * 2), SEGS, P)
    assert (short.severity, long_.severity) == ("low", "critical")


def test_observed_spillback_needs_upstream_to_start_after_downstream_and_overlap() -> None:
    down = series("int-a2_int-a3", [JAM] * 8 + [FREE] * 2)
    up = [ev("int-a1_int-a2", k + 2, 0.7, 1.0) for k in range(4)] + [
        ev("int-a1_int-a2", k + 6, 0.05, 14.0) for k in range(3)
    ]
    eps = detect_congestion(down + up, SEGS, P)
    (sb,) = detect_spillback(eps, SEGS)
    assert (sb.origin_segment, sb.upstream_segment, sb.inferred) == (
        "int-a2_int-a3",
        "int-a1_int-a2",
        False,
    )
    assert (
        detect_spillback(
            detect_congestion(series("int-a2_int-a3", [JAM] * 6 + [FREE] * 2), SEGS, P), SEGS
        )
        == []
    )


def test_inferred_spillback_from_a_persistent_episode_but_never_for_the_first_segment() -> None:
    long_eps = detect_congestion(series("int-a2_int-a3", [(0.8, 0.5)] * 9 + [FREE] * 3), SEGS, P)
    (sb,) = detect_spillback(long_eps, SEGS, SpillParams(120.0, 0.6))
    assert (
        sb.inferred
        and sb.upstream_segment == "int-a1_int-a2"
        and sb.severity in ("high", "critical")
    )
    first = detect_congestion(series("int-a1_int-a2", [(0.8, 0.5)] * 9 + [FREE] * 3), SEGS, P)
    assert detect_spillback(first, SEGS, SpillParams(120.0, 0.6)) == []


def test_truth_episode_needs_a_standing_queue_for_two_intervals_and_merges_small_gaps() -> None:
    def row(edge, b, waiting):
        return {"edge_id": edge, "begin_s": float(b), "waiting_s": waiting}

    # 30 waiting-seconds/30 s = 1 vehicle x 3.5 m: 300 s-waiting -> 10 veh -> 35 m (>= 30 m)
    rows = [
        row("int-a1_int-a2", 0, 300.0),
        row("int-a1_int-a2", 30, 300.0),
        row("int-a1_int-a2", 90, 300.0),
        row("int-a1_int-a2", 300, 300.0),
        row("int-a2_int-a3", 0, 20.0),
        row("int-a2_int-a3", 30, 20.0),
    ]
    (t,) = truth_episodes(rows, SEGS, T0)
    assert t.segment == "int-a1_int-a2" and t.onset == T0 and t.end == T0 + timedelta(seconds=120)


def test_truth_spillback_uses_the_same_propagation_rule() -> None:
    down = TruthEpisode("int-a2_int-a3", T0, T0 + timedelta(seconds=300), 100.0)
    up = TruthEpisode(
        "int-a1_int-a2", T0 + timedelta(seconds=60), T0 + timedelta(seconds=240), 50.0
    )
    assert len(truth_spillbacks([up, down], SEGS)) == 1
    assert truth_spillbacks([down], SEGS) == []


def test_matching_is_one_to_one_and_respects_tolerance() -> None:
    truth = [("s", T0 + timedelta(seconds=100), T0 + timedelta(seconds=200))]
    close = ("s", T0 + timedelta(seconds=230), T0 + timedelta(seconds=300))
    far = ("s", T0 + timedelta(seconds=400), None)
    assert match_spans([close], truth) == [(0, 0)]
    assert match_spans([far], truth) == []
    assert match_spans(
        [close, ("s", T0 + timedelta(seconds=210), T0 + timedelta(seconds=250))], truth
    ) == [(1, 0)]  # earliest onset wins the one truth span


def test_wilson_interval_is_sane() -> None:
    assert wilson(0, 0) is None
    lo, hi = wilson(7, 7)
    assert 0.6 < lo < 0.7 and hi == 1.0
