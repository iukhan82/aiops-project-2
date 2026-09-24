"""P06.06: the pedestrian/cyclist conflict indicator, its privacy structure and
the realized-PET truth, on hand-built trajectories with analytically known
answers. Held-out accuracy and the platform path are proven by
backend/analytics/evaluate_vru.py and verify_vru_conflicts.py
(docs/evidence/p06_06_vru_conflicts.json)."""

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from jsonschema import Draft202012Validator

from edge.vision_privacy import PrivacyGate, PrivacyZone
from edge.vru_conflict import (
    AGGREGATE_EVENT_TYPE,
    ConflictAggregator,
    ConflictParams,
    PairScorer,
    SiteConflictDetector,
    TrackSample,
    predicted_pet,
    suppression_event,
    to_observation_event,
)
from models.vru_dataset.truth import Piece, realized_interactions

ANCHOR = datetime(2026, 9, 18, 9, 0, 0, tzinfo=timezone.utc)
SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "contracts"
        / "observation-envelope"
        / "v1"
        / "schema.json"
    ).read_text(encoding="utf-8")
)
P = ConflictParams(vehicle_model="constant_velocity")


def test_predicted_pet_matches_the_geometry() -> None:
    # vehicle reaches (30, 0) at 3.0 s; pedestrian (30, -5) at 1.5 m/s reaches it at 3.33 s
    pet, tau_v, tau_p = predicted_pet((30.0, -5.0, 0.0, 1.5), (0.0, 0.0, 10.0, 0.0), 0.0, P)
    assert abs(tau_v - 3.0) < 0.15 and abs(tau_p - 10 / 3) < 0.2 and abs(pet - 1 / 3) < 0.25


def test_parallel_and_diverging_paths_do_not_cross() -> None:
    assert predicted_pet((0.0, 3.0, 1.4, 0.0), (0.0, 0.0, 10.0, 0.0), 0.0, P) is None
    assert (
        predicted_pet((30.0, 5.0, 0.0, 1.5), (0.0, 0.0, 10.0, 0.0), 0.0, P) is None
    )  # walking away from the road


def test_turning_vehicle_is_only_caught_with_the_turn_rate_model() -> None:
    vehicle = (0.0, 0.0, 8.0, 0.0)
    pedestrian = (25.0, 12.0, 0.0, -1.4)  # crosses y=0 well past x=25 if the car goes straight
    straight = predicted_pet(pedestrian, vehicle, 0.0, P)
    left_turn = predicted_pet(pedestrian, vehicle, 0.25, P)
    assert left_turn is not None and (straight is None or straight[1] != left_turn[1])


def frames(vehicle_path, pedestrian_path):
    out = {}
    for t, (vp, pp) in enumerate(zip(vehicle_path, pedestrian_path, strict=True)):
        out[float(t)] = [
            TrackSample(float(t), "veh-1", "vehicle", *vp),
            TrackSample(float(t), "ped-1", "pedestrian", *pp),
        ]
    return out


def run(vehicle_path, pedestrian_path, params=P) -> list:
    detector = SiteConflictDetector("int-x", params)
    for t, samples in frames(vehicle_path, pedestrian_path).items():
        detector.update(t, samples)
    return detector.events


def test_close_encounter_raises_one_severe_event_and_a_late_one_none() -> None:
    vehicle = [(10.0 * t, 0.0) for t in range(8)]  # x = 0..70 at 10 m/s, reaches x=40 at t=4
    close = [(40.0, -6.0 + 1.5 * t) for t in range(8)]  # reaches y=0 at t=4
    (event,) = run(vehicle, close)
    assert event.mode == "pedestrian" and event.min_pet_s <= 1.5 and event.severity == "severe"
    late = [(40.0, -14.0 + 1.5 * t) for t in range(8)]  # reaches y=0 at t=9.3: PET ~5 s
    assert run(vehicle, late) == []


def test_a_stopped_vehicle_or_a_waiting_pedestrian_raises_nothing() -> None:
    standing_car = [(40.0 - 0.1 * t, 0.0) for t in range(8)]
    assert run(standing_car, [(40.0, -6.0 + 1.5 * t) for t in range(8)]) == []
    assert run([(10.0 * t, 0.0) for t in range(8)], [(40.0, -6.0)] * 8) == []


def test_tracks_that_disappear_are_forgotten() -> None:
    detector = SiteConflictDetector("int-x", P)
    detector.update(0.0, [TrackSample(0.0, "a", "pedestrian", 0.0, 0.0)])
    detector.update(20.0, [TrackSample(20.0, "b", "vehicle", 0.0, 0.0)])
    assert "a" not in detector._hist and "a" not in detector._cls and "a" not in detector._last_seen


def test_pair_scorer_is_a_plain_logistic_function() -> None:
    scorer = PairScorer(
        {
            "features": ["a", "b"],
            "mean": [1.0, 0.0],
            "scale": [2.0, 1.0],
            "coef": [1.0, -1.0],
            "intercept": 0.0,
        }
    )
    assert abs(scorer.predict({"a": 1.0, "b": 0.0}) - 0.5) < 1e-9
    assert scorer.predict({"a": 5.0, "b": 0.0}) > 0.5 > scorer.predict({"a": 5.0, "b": 3.0})


def aggregator(zones=None, min_cohort=3, window_s=300.0) -> ConflictAggregator:
    return ConflictAggregator(PrivacyGate(zones or [], window_s, b"test-salt"), ANCHOR, min_cohort)


DEVICE = {
    "device_id": "camera-int-x",
    "agency_scope": "city-traffic-ops",
    "location": {"latitude": 1.0, "longitude": 2.0, "intersection_id": "int-x"},
}


def test_below_the_k_anonymity_floor_the_whole_window_is_suppressed() -> None:
    agg = aggregator()
    for i in range(2):
        agg.note_exposure("int-x", "pedestrian", f"ped-{i}", 10.0)
    (result,) = agg.close_all()
    assert type(result).__name__ == "SuppressedConflictWindow" and result.below_threshold_count == 2
    event = suppression_event(result, DEVICE, "r", 1, "2026-09-18.1", "v")
    assert {m["name"] for m in event["measurements"]} == {
        "suppressed_window_pedestrian",
        "cohort_size_pedestrian",
    }
    Draft202012Validator(SCHEMA).validate(event)


def test_exported_event_is_schema_valid_and_carries_no_identity_or_position() -> None:
    agg = aggregator()
    for i in range(4):
        agg.note_exposure("int-x", "pedestrian", f"ped-secret-{i}", 10.0)
    detector = SiteConflictDetector("int-x", P)
    close = [(40.0, -6.0 + 1.5 * t) for t in range(8)]
    for t, samples in frames([(10.0 * t, 0.0) for t in range(8)], close).items():
        detector.update(t, samples)
    for e in detector.events:
        agg.note_conflict(e)
    (result,) = agg.close_all()
    event = to_observation_event(result, DEVICE, "r", 1, "2026-09-18.1", "v")
    Draft202012Validator(SCHEMA).validate(event)
    text = json.dumps(event)
    assert (
        event["event_type"] == AGGREGATE_EVENT_TYPE
        and event["privacy_classification"] == "aggregated"
        and event["truth_label"] == "inferred"
    )
    assert (
        "ped-secret" not in text
        and "ped-1" not in text
        and "veh-1" not in text
        and "test-salt" not in text
    )
    assert set(event["location"]) <= {
        "coordinate_reference",
        "latitude",
        "longitude",
        "intersection_id",
    }
    values = {m["name"]: m["value"] for m in event["measurements"]}
    assert values["exposure_pedestrian"] == 5 and values["conflicts_severe_pedestrian"] == 1


def test_identity_is_rehashed_every_window() -> None:
    gate = PrivacyGate([], 300.0, b"test-salt")
    first, second = gate.ephemeral_id("ped-1", 0), gate.ephemeral_id("ped-1", 0)
    assert gate.ephemeral_id("ped-1", 0) != gate.ephemeral_id("ped-1", 1)
    assert first == second
    assert PrivacyGate([], 300.0, b"other-salt").ephemeral_id("ped-1", 0) != gate.ephemeral_id(
        "ped-1", 0
    )


def test_privacy_zone_removes_the_sample_before_any_computation() -> None:
    agg = aggregator([PrivacyZone("z", 40.0, -3.0, 5.0)])
    inside = TrackSample(4.0, "ped-1", "pedestrian", 40.0, -2.0)
    outside = TrackSample(4.0, "ped-1", "pedestrian", 40.0, 20.0)
    assert agg.admit_sample(inside) is False and agg.admit_sample(outside) is True
    assert agg.zone_suppressed_samples == 1


def piece(
    obj_id: str, cls: str, path: list[tuple[float, float]], t0: float = 0.0, speed: float = 8.0
) -> Piece:
    xy = np.array(path, dtype=float)
    t = t0 + np.arange(len(path)) * 0.25
    return Piece(obj_id, cls, "x", "int-x", t, xy, np.full(len(path), speed))


def test_realized_pet_and_severity_from_crossing_paths() -> None:
    # pedestrian crosses x=40 (y from -3 to +3 over 4 s), vehicle passes x=40 along y=0 at 8 m/s
    ped_path = [(40.0, -3.0 + 1.5 * 0.25 * k) for k in range(17)]  # 4 s, crossing y=0 at t=2.0
    veh_path = [(30.0 + 2.0 * k, 0.0) for k in range(17)]  # 8 m/s, at x=40 at t=1.25
    (i,) = realized_interactions(
        [piece("ped-1", "ped", ped_path, speed=1.5), piece("veh-1", "veh", veh_path, speed=8.0)]
    )
    assert (
        abs(i.pet_s - 0.75) < 0.05 and i.severity == "severe" and i.is_conflict and not i.vru_first
    )


def test_crawling_vehicle_is_a_low_speed_interaction_not_a_conflict() -> None:
    ped_path = [(40.0, -3.0 + 1.5 * 0.25 * k) for k in range(17)]
    veh_path = [(37.0 + 0.5 * k, 0.0) for k in range(17)]  # 2 m/s, at x=40 at t=1.5
    (i,) = realized_interactions(
        [piece("ped-1", "ped", ped_path, speed=1.5), piece("veh-1", "veh", veh_path, speed=2.0)]
    )
    assert i.severity == "low_speed" and not i.is_conflict


def test_paths_that_never_cross_are_not_interactions() -> None:
    ped = [(40.0 + 0.4 * k, 2.5) for k in range(17)]  # walking alongside the road
    veh = [(30.0 + 2.0 * k, 0.0) for k in range(17)]
    assert (
        realized_interactions([piece("ped-1", "ped", ped, speed=1.5), piece("veh-1", "veh", veh)])
        == []
    )
