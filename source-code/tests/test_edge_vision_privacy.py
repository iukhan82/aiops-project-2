"""P04.06: privacy-preserving track/vision metadata path.

Three structural guarantees under test: privacy zones actually drop points
(not just flag them), identity never persists across aggregation windows,
and a cohort below the k-anonymity floor is never individually reported.
The last section grounds zone suppression and identity rotation against a
real recorded pedestrian trajectory from P03.02's SUMO output, not just
synthetic points.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from edge.contracts import load_validator
from edge.vision_devices import CAMERA_JUNCTIONS, default_privacy_zones, edge_camera_devices
from edge.vision_privacy import (
    AGGREGATE_EVENT_TYPE,
    DEFAULT_MIN_COHORT,
    PrivacyGate,
    PrivacyZone,
    SuppressedPoint,
    SuppressedWindow,
    TrackPoint,
    VisionPrivacyAggregator,
    WindowAggregate,
    close_due_windows,
    to_observation_event,
)

T0 = datetime(2026, 9, 18, 9, 0, 0, tzinfo=timezone.utc)
NOD_FILE = (
    Path(__file__).resolve().parents[1] / "simulator" / "network" / "plain" / "district.nod.xml"
)
FCD_FILE = (
    Path(__file__).resolve().parents[1] / "simulator" / "demand" / "output" / "sim-run-1-fcd.xml"
)


def _device(device_id: str = "camera-x") -> dict:
    return {
        "device_id": device_id,
        "agency_scope": "city-traffic-ops",
        "location": {"latitude": 31.52, "longitude": 74.36, "lane_id": "int-a1_int-a2_2"},
    }


def _point(track_id="t1", device="camera-x", cls="pedestrian", t=T0, x=0.0, y=0.0, speed=1.2):
    return TrackPoint(track_id, device, cls, t, x, y, speed)


# --- PrivacyZone / PrivacyGate ------------------------------------------------------


def test_zone_contains_is_a_circle() -> None:
    zone = PrivacyZone("z1", 100.0, 200.0, 10.0)
    assert zone.contains(100.0, 200.0)
    assert zone.contains(105.0, 200.0)  # on boundary-ish, inside
    assert zone.contains(100.0, 210.0)  # exactly on radius
    assert not zone.contains(100.0, 211.0)
    assert not zone.contains(80.0, 200.0)


def test_point_inside_zone_is_suppressed_outside_is_admitted() -> None:
    gate = PrivacyGate([PrivacyZone("z1", 0.0, 0.0, 5.0)], window_s=30.0)
    inside = gate.admit(_point(x=1.0, y=1.0))
    outside = gate.admit(_point(x=50.0, y=50.0))
    assert isinstance(inside, SuppressedPoint) and inside.reason == "privacy_zone"
    assert inside.zone_id == "z1"
    assert isinstance(outside, str) and len(outside) == 16


def test_no_zones_configured_admits_everything() -> None:
    gate = PrivacyGate([], window_s=30.0)
    assert isinstance(gate.admit(_point(x=0.0, y=0.0)), str)


def test_multiple_zones_each_suppress_their_own_area() -> None:
    gate = PrivacyGate(
        [PrivacyZone("north", 0.0, 100.0, 5.0), PrivacyZone("south", 0.0, -100.0, 5.0)],
        window_s=30.0,
    )
    n = gate.admit(_point(x=0.0, y=100.0))
    s = gate.admit(_point(x=0.0, y=-100.0))
    mid = gate.admit(_point(x=0.0, y=0.0))
    assert isinstance(n, SuppressedPoint) and n.zone_id == "north"
    assert isinstance(s, SuppressedPoint) and s.zone_id == "south"
    assert isinstance(mid, str)


# --- ephemeral identity: no persistence across windows ------------------------------


def test_ephemeral_id_is_stable_within_a_window() -> None:
    gate = PrivacyGate([], window_s=30.0, salt=b"fixed-salt")
    a = gate.ephemeral_id("real-track-42", gate.window_index(T0))
    b = gate.ephemeral_id("real-track-42", gate.window_index(T0 + timedelta(seconds=5)))
    assert a == b  # same window


def test_ephemeral_id_rotates_across_windows() -> None:
    gate = PrivacyGate([], window_s=30.0, salt=b"fixed-salt")
    w1 = gate.ephemeral_id("real-track-42", gate.window_index(T0))
    w2 = gate.ephemeral_id("real-track-42", gate.window_index(T0 + timedelta(seconds=31)))
    assert w1 != w2  # same physical object, unrelated id next window


def test_ephemeral_id_never_contains_the_raw_track_id() -> None:
    gate = PrivacyGate([], window_s=30.0)
    raw_id = "very-distinctive-license-plate-like-id-99887766"
    ephemeral = gate.ephemeral_id(raw_id, 0)
    assert raw_id not in ephemeral
    assert raw_id.encode().hex() not in ephemeral


def test_ephemeral_id_depends_on_the_boot_random_salt() -> None:
    a = PrivacyGate([], window_s=30.0).ephemeral_id("track-1", 0)
    b = PrivacyGate([], window_s=30.0).ephemeral_id("track-1", 0)
    assert a != b  # each gate's salt is independently random (os.urandom)


def test_salt_is_never_a_public_attribute() -> None:
    gate = PrivacyGate([], window_s=30.0)
    assert not hasattr(gate, "salt")
    assert all(not name.startswith("salt") for name in vars(gate))


# --- aggregation: bounded memory, no raw retention -----------------------------------


def test_aggregator_never_stores_a_track_point_only_derived_cell_state() -> None:
    gate = PrivacyGate([], window_s=30.0)
    agg = VisionPrivacyAggregator(gate, min_cohort=1)
    agg.ingest(_point("a", x=0, y=0))
    cell = agg._cells[("camera-x", "pedestrian", gate.window_index(T0))]
    assert not hasattr(cell, "track_id") and not hasattr(cell, "x") and not hasattr(cell, "y")
    assert cell.ephemeral_ids == {gate.ephemeral_id("a", gate.window_index(T0))}


def test_closing_a_window_evicts_its_cell_bounded_memory() -> None:
    gate = PrivacyGate([], window_s=30.0)
    agg = VisionPrivacyAggregator(gate, min_cohort=1)
    for i in range(3):
        agg.ingest(_point(f"t{i}"))
    assert len(agg.open_window_keys()) == 1
    agg.close_window("camera-x", "pedestrian", gate.window_index(T0))
    assert agg.open_window_keys() == []


def test_closing_an_unknown_window_is_a_noop() -> None:
    agg = VisionPrivacyAggregator(PrivacyGate([], window_s=30.0), min_cohort=1)
    assert agg.close_window("camera-x", "pedestrian", 999) is None


# --- k-anonymity floor ----------------------------------------------------------------


def test_below_cohort_floor_is_suppressed_not_reported() -> None:
    gate = PrivacyGate([], window_s=30.0)
    agg = VisionPrivacyAggregator(gate, min_cohort=DEFAULT_MIN_COHORT)
    for i in range(DEFAULT_MIN_COHORT - 1):
        agg.ingest(_point(f"solo-{i}"))
    result = agg.close_window("camera-x", "pedestrian", gate.window_index(T0))
    assert isinstance(result, SuppressedWindow)
    assert result.reason == "below_k_anonymity_floor"
    assert result.below_threshold_count == DEFAULT_MIN_COHORT - 1
    assert agg.cohort_suppressed_windows == 1


def test_a_single_pedestrian_is_never_individually_reported() -> None:
    gate = PrivacyGate([], window_s=30.0)
    agg = VisionPrivacyAggregator(gate, min_cohort=3)
    agg.ingest(_point("lonely-pedestrian", speed=1.4))
    result = agg.close_window("camera-x", "pedestrian", gate.window_index(T0))
    assert isinstance(result, SuppressedWindow) and result.below_threshold_count == 1


def test_at_or_above_cohort_floor_is_reported_with_correct_aggregate() -> None:
    gate = PrivacyGate([], window_s=30.0)
    agg = VisionPrivacyAggregator(gate, min_cohort=3)
    for i, speed in enumerate((1.0, 2.0, 3.0)):
        agg.ingest(_point(f"p{i}", speed=speed))
    result = agg.close_window("camera-x", "pedestrian", gate.window_index(T0))
    assert isinstance(result, WindowAggregate)
    assert result.count == 3
    assert result.mean_speed_m_s == pytest.approx(2.0)
    assert result.window_end - result.window_start == timedelta(seconds=30)


def test_repeated_points_from_the_same_object_count_once() -> None:
    gate = PrivacyGate([], window_s=30.0)
    agg = VisionPrivacyAggregator(gate, min_cohort=1)
    for _ in range(10):  # ten frames, one object
        agg.ingest(_point("same-object", speed=5.0))
    result = agg.close_window("camera-x", "pedestrian", gate.window_index(T0))
    assert isinstance(result, WindowAggregate) and result.count == 1


def test_zone_suppressed_points_never_reach_the_cohort_count() -> None:
    gate = PrivacyGate([PrivacyZone("z", 0.0, 0.0, 5.0)], window_s=30.0)
    agg = VisionPrivacyAggregator(gate, min_cohort=2)
    agg.ingest(_point("in-zone-1", x=1, y=1))
    agg.ingest(_point("in-zone-2", x=2, y=2))
    agg.ingest(_point("outside", x=100, y=100))
    assert agg.zone_suppressed_count == 2
    result = agg.close_window("camera-x", "pedestrian", gate.window_index(T0))
    assert isinstance(result, SuppressedWindow) and result.below_threshold_count == 1


def test_classes_and_devices_are_aggregated_independently() -> None:
    gate = PrivacyGate([], window_s=30.0)
    agg = VisionPrivacyAggregator(gate, min_cohort=1)
    agg.ingest(_point("v1", device="cam-a", cls="vehicle", speed=10.0))
    agg.ingest(_point("p1", device="cam-a", cls="pedestrian", speed=1.0))
    agg.ingest(_point("v2", device="cam-b", cls="vehicle", speed=20.0))
    w = gate.window_index(T0)
    veh_a = agg.close_window("cam-a", "vehicle", w)
    ped_a = agg.close_window("cam-a", "pedestrian", w)
    veh_b = agg.close_window("cam-b", "vehicle", w)
    assert veh_a.count == 1 and veh_a.mean_speed_m_s == 10.0
    assert ped_a.count == 1 and ped_a.mean_speed_m_s == 1.0
    assert veh_b.count == 1 and veh_b.mean_speed_m_s == 20.0


def test_close_due_windows_only_closes_windows_that_have_actually_ended() -> None:
    gate = PrivacyGate([], window_s=30.0)
    agg = VisionPrivacyAggregator(gate, min_cohort=2)
    agg.ingest(_point("a", t=T0))
    agg.ingest(_point("b", t=T0 + timedelta(seconds=35)))  # next window
    due = close_due_windows(agg, T0 + timedelta(seconds=29))
    assert due == []  # first window (ends at T0+30) has not ended yet
    due = close_due_windows(agg, T0 + timedelta(seconds=31))
    assert len(due) == 1 and isinstance(due[0], SuppressedWindow)  # only 1 object: below floor
    assert agg.open_window_keys() == [
        ("camera-x", "pedestrian", gate.window_index(T0 + timedelta(seconds=35)))
    ]


# --- output event: schema conformance and no leakage ---------------------------------


def test_emitted_event_conforms_to_observation_envelope_and_carries_no_track_identity() -> None:
    schema = load_validator("observation-envelope")
    result = WindowAggregate("camera-x", "pedestrian", T0, T0 + timedelta(seconds=30), 4, 1.35, 0)
    event = to_observation_event(result, _device(), "run-1", 0, "2026-09-18.1", "edge-vision/1")
    schema.validate(event)
    assert event["event_type"] == AGGREGATE_EVENT_TYPE
    assert event["privacy_classification"] == "aggregated"
    assert event["retention_class"] == "short"
    assert event["truth_label"] == "simulated"
    blob = str(event)
    for forbidden in ("track_id", "x=", "'x':", '"x":', "raw_track"):
        assert forbidden not in blob
    names = {m["name"] for m in event["measurements"]}
    assert names == {"count_pedestrian", "mean_speed"}


def test_vision_devices_are_valid_device_records_distinct_from_the_sensor_catalog() -> None:
    from edge.contracts import load_validator as load_device_validator

    if not NOD_FILE.is_file():
        pytest.skip("network not built")
    validator = load_device_validator("device")
    devices = edge_camera_devices(NOD_FILE)
    assert len(devices) == len(CAMERA_JUNCTIONS) == 3
    ids = {d["device_id"] for d in devices}
    assert ids == {f"camera-{j}" for j in CAMERA_JUNCTIONS}
    for device in devices:
        validator.validate(device)
        assert device["device_type"] == "edge_camera"
    zones = default_privacy_zones(NOD_FILE)
    assert len(zones) == 3


# --- grounded in a real recorded pedestrian trajectory --------------------------------


def _read_real_pedestrian_track(track_id: str) -> list[tuple[datetime, float, float, float]]:
    root = ET.parse(FCD_FILE).getroot()
    points = []
    for timestep in root.findall("timestep"):
        t = float(timestep.get("time"))
        for person in timestep.findall("person"):
            if person.get("id") == track_id:
                points.append(
                    (
                        T0 + timedelta(seconds=t),
                        float(person.get("x")),
                        float(person.get("y")),
                        float(person.get("speed")),
                    )
                )
    return points


@pytest.fixture(scope="module")
def real_pedestrian_track():
    if not FCD_FILE.is_file():
        pytest.skip("P03.02 FCD output not generated (run demand/run_container.sh)")
    points = _read_real_pedestrian_track("p03-02-verify-ped-0015")
    if not points:
        pytest.skip("expected real pedestrian track not present in this FCD output")
    return points


def test_real_trajectory_is_on_the_expected_sidewalk(real_pedestrian_track) -> None:
    xs = [p[1] for p in real_pedestrian_track]
    ys = [p[2] for p in real_pedestrian_track]
    assert len(real_pedestrian_track) > 50
    assert max(ys) - min(ys) < 1.0  # walks a straight sidewalk (roughly constant y)
    assert max(xs) > min(xs) + 50  # covers real distance, not a single point


def test_privacy_zone_suppresses_a_real_recorded_pedestrian_segment(real_pedestrian_track) -> None:
    xs = [p[1] for p in real_pedestrian_track]
    zone_center_x = (min(xs) + max(xs)) / 2.0
    zone_y = real_pedestrian_track[0][2]
    zone = PrivacyZone("real-zone", zone_center_x, zone_y, radius_m=25.0)
    gate = PrivacyGate([zone], window_s=60.0)

    suppressed, admitted = [], []
    for t, x, y, _speed in real_pedestrian_track:
        outcome = gate.admit(_point("p03-02-verify-ped-0015", cls="pedestrian", t=t, x=x, y=y))
        (suppressed if isinstance(outcome, SuppressedPoint) else admitted).append((x, y))

    assert suppressed and admitted, "the zone must catch some real points and miss others"
    assert all(zone.contains(x, y) for x, y in suppressed)
    assert all(not zone.contains(x, y) for x, y in admitted)


def test_real_track_ephemeral_id_rotates_and_never_reveals_the_real_id(
    real_pedestrian_track,
) -> None:
    gate = PrivacyGate([], window_s=60.0)
    real_id = "p03-02-verify-ped-0015"
    first_t = real_pedestrian_track[0][0]
    last_t = real_pedestrian_track[-1][0]
    assert last_t - first_t > timedelta(seconds=60), (
        "fixture must span >1 window for this to be meaningful"
    )

    early_id = gate.ephemeral_id(real_id, gate.window_index(first_t))
    late_id = gate.ephemeral_id(real_id, gate.window_index(last_t))
    assert early_id != late_id
    assert real_id not in early_id and real_id not in late_id


def test_real_trajectory_through_the_full_aggregator_respects_all_three_guarantees(
    real_pedestrian_track,
) -> None:
    xs = [p[1] for p in real_pedestrian_track]
    zone = PrivacyZone("real-zone", (min(xs) + max(xs)) / 2.0, real_pedestrian_track[0][2], 20.0)
    gate = PrivacyGate([zone], window_s=60.0)
    agg = VisionPrivacyAggregator(
        gate, min_cohort=1
    )  # 1 real object: proves the zone path, not cohort math
    for t, x, y, speed in real_pedestrian_track:
        agg.ingest(_point("p03-02-verify-ped-0015", cls="pedestrian", t=t, x=x, y=y, speed=speed))

    last_window = gate.window_index(real_pedestrian_track[-1][0])
    results = [
        agg.close_window("camera-x", "pedestrian", w)
        for w in range(gate.window_index(T0), last_window + 1)
    ]
    results = [r for r in results if r is not None]
    assert any(isinstance(r, WindowAggregate) for r in results)  # windows outside the zone reported
    assert agg.zone_suppressed_count > 0  # windows/points inside the zone were dropped
    for r in results:
        if isinstance(r, WindowAggregate):
            assert r.mean_speed_m_s >= 0.0
