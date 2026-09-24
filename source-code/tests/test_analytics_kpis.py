"""P06.01: KPI unit tests on hand-built events with hand-computed answers.
Agreement with SUMO ground truth and the platform path are proven by
backend/analytics/verify_kpis.py (docs/evidence/p06_01_kpis.json)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.analytics.kpis import (
    compute_corridor_kpis,
    iso_z,
    percentile,
    to_network_state_record,
    window_floor,
)
from backend.analytics.topology import (
    Segment,
    apply_lane_share,
    corridor_directions,
    parse_net_segments,
)

T0 = datetime(2026, 9, 18, 9, 0, 0, tzinfo=timezone.utc)
SEGS = [
    Segment("int-a1_int-a2", "int-a1", "int-a2", "corridor-a", "east", 1, 300.0, 15.0),
    Segment("int-a2_int-a3", "int-a2", "int-a3", "corridor-a", "east", 2, 300.0, 15.0),
    Segment("int-a3_int-a4", "int-a3", "int-a4", "corridor-a", "east", 3, 300.0, 15.0),
]


def loop_event(
    edge: str, k: int, count: int, occupancy: float, speed: float | None, lane: int = 3
) -> dict:
    m = [
        {
            "name": "vehicle_count",
            "value": count,
            "unit": "count",
            "quality": "valid",
            "confidence": 0.98,
        },
        {
            "name": "occupancy",
            "value": occupancy,
            "unit": "ratio",
            "quality": "valid",
            "confidence": 0.95,
        },
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
        "event_type": "traffic.loop_detector.count",
        "observation_time": iso_z(T0 + timedelta(seconds=30 * (k + 1))),
        "location": {"lane_id": f"{edge}_{lane}"},
        "measurements": m,
    }


def full_window(count=3, occ=0.1, speed=10.0) -> list[dict]:
    return [loop_event(s.edge_id, k, count, occ, speed) for s in SEGS for k in range(10)]


def only(events: list[dict]) -> dict:
    (row,) = compute_corridor_kpis(events, SEGS, "g1")
    return row


def test_volume_scales_the_instrumented_lane_by_its_share() -> None:
    row = only(full_window(count=3))
    # 3 vehicles per 30 s on the loop lane = 360 veh/h, / default share 0.5
    assert row["kpis"]["volume_veh_h"] == pytest.approx(720.0)
    halved = compute_corridor_kpis(
        full_window(count=3), apply_lane_share(SEGS, {s.edge_id: 0.25 for s in SEGS}), "g1"
    )[0]
    assert halved["kpis"]["volume_veh_h"] == pytest.approx(1440.0)


def test_speed_is_the_space_mean_not_the_arithmetic_mean() -> None:
    events = [
        loop_event(s.edge_id, k, 1, 0.1, 10.0 if k < 5 else 5.0) for s in SEGS for k in range(10)
    ]
    row = only(events)
    # harmonic mean of 10 and 5 m/s with equal counts = 6.667
    assert row["kpis"]["speed_m_s"] == pytest.approx(6.667, abs=0.01)


def test_density_travel_time_and_delay() -> None:
    row = only(full_window(occ=0.09, speed=10.0))
    assert row["kpis"]["density_veh_km"] == pytest.approx(0.09 * 1000 / 4.5 / 0.5)
    assert row["kpis"]["travel_time_s"] == pytest.approx(90.0)  # 900 m at 10 m/s
    assert row["kpis"]["free_flow_travel_time_s"] == pytest.approx(60.0)
    assert row["kpis"]["delay_s"] == pytest.approx(30.0)
    assert row["quality"] == "valid"


def test_standing_traffic_at_the_loop_raises_the_queue_indicator() -> None:
    events = [loop_event(s.edge_id, k, 0, 0.9, None) for s in SEGS for k in range(10)]
    assert only(events)["kpis"]["queue_fraction"] == 1.0
    assert only(full_window())["kpis"]["queue_fraction"] == 0.0


def test_missing_data_lowers_quality_and_uses_free_flow_for_the_gap() -> None:
    row = only([e for e in full_window() if "a2_int-a3" not in e["location"]["lane_id"]])
    assert row["quality"] == "suspect" and row["segments_reporting"] == 2
    assert row["kpis"]["free_flow_travel_time_s"] == pytest.approx(60.0)


def test_duplicate_events_do_not_double_count() -> None:
    assert compute_corridor_kpis(full_window() * 2, SEGS, "g1") == compute_corridor_kpis(
        full_window(), SEGS, "g1"
    )


def test_window_floor_and_percentile() -> None:
    assert window_floor(T0 + timedelta(seconds=299), 300) == T0
    assert window_floor(T0 + timedelta(seconds=300), 300) == T0 + timedelta(seconds=300)
    assert percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.95) == pytest.approx(4.8)
    assert percentile([7.0], 0.95) == 7.0


def test_network_state_record_freshness_is_decided_at_read_time() -> None:
    row = only(full_window())
    end = T0 + timedelta(seconds=300)
    assert (
        to_network_state_record(row, as_of=end + timedelta(seconds=30))["freshness_status"]
        == "fresh"
    )
    assert (
        to_network_state_record(row, as_of=end + timedelta(hours=1))["freshness_status"] == "stale"
    )
    record = to_network_state_record(row, as_of=end)
    assert record["network_element_id"] == "corridor-a/east" and record["truth_label"] == "inferred"


def test_reliability_indices_require_six_trailing_windows() -> None:
    events = [loop_event(s.edge_id, k, 3, 0.1, 10.0) for s in SEGS for k in range(10 * 8)]
    rows = compute_corridor_kpis(events, SEGS, "g1")
    assert [r["kpis"]["buffer_index"] is None for r in rows] == [True] * 5 + [False] * 3
    assert rows[-1]["kpis"]["travel_time_index"] == pytest.approx(1.5)


NET = Path(__file__).resolve().parents[1] / "simulator" / "network" / "output" / "district.net.xml"


@pytest.mark.skipif(not NET.is_file(), reason="P03.01 network output not built")
def test_real_network_yields_three_corridors_two_directions_three_segments_each() -> None:
    groups = corridor_directions(parse_net_segments(NET))
    assert len(groups) == 6 and all(len(v) == 3 for v in groups.values())
    assert [s.edge_id for s in groups[("corridor-a", "west")]] == [
        "int-a4_int-a3",
        "int-a3_int-a2",
        "int-a2_int-a1",
    ]
