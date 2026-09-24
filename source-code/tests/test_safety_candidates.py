"""P06.05: stalled-vehicle fusion and overlay candidate rules on hand-built events.
Held-out numbers and platform behaviour are proven by
backend/analytics/verify_safety_candidates.py (docs/evidence/p06_05_safety.json)."""

from datetime import datetime, timedelta, timezone

from backend.analytics.congestion import Episode
from backend.analytics.overlay_candidates import detect_overlay_candidates
from backend.analytics.stall_candidates import AlarmSpan, FusionParams, alarm_spans, fuse

T0 = datetime(2026, 9, 18, 9, 0, 0, tzinfo=timezone.utc)
SEG = "int-a1_int-a2"


def span(k_alarms: int, p: float = 0.95, onset_s: int = 0, seg: str = SEG) -> AlarmSpan:
    onset = T0 + timedelta(seconds=onset_s)
    return AlarmSpan(
        seg,
        onset,
        onset + timedelta(seconds=30 * k_alarms),
        [p] * k_alarms,
        [f"ev-{i}" for i in range(k_alarms)],
    )


def episode(seg: str = SEG, onset_s: int = 0, end_s: int = 300) -> Episode:
    return Episode(
        segment=seg,
        corridor_id="corridor-a",
        direction="east",
        order=1,
        onset=T0 + timedelta(seconds=onset_s),
        detected_at=T0 + timedelta(seconds=onset_s + 60),
        clear=T0 + timedelta(seconds=end_s),
        peak_occupancy=0.6,
        min_speed=1.0,
        evidence=["cong-ev"],
        last_seen=T0 + timedelta(seconds=end_s),
    )


def test_k_min_consecutive_alarms_required() -> None:
    assert fuse([span(1)], [], FusionParams(k_min=2)) == []
    (c,) = fuse([span(2)], [], FusionParams(k_min=2))
    assert (
        c["kind"] == "stalled_vehicle"
        and c["network_element_id"] == SEG
        and c["detected_at"] == T0 + timedelta(seconds=60)
    )


def test_corroboration_raises_confidence_by_noisy_or_and_keeps_both_sources() -> None:
    (solo,) = fuse([span(3, 0.8)], [], FusionParams(k_min=2))
    (both,) = fuse([span(3, 0.8)], [episode()], FusionParams(k_min=2))
    assert solo["confidence"] == 0.8 and not solo["attributes"]["corroborated"]
    assert both["confidence"] > solo["confidence"] and both["attributes"]["corroborated"]
    assert [s["source"] for s in both["attributes"]["sources"]] == ["edge_model", "loop_congestion"]
    assert "cong-ev" in both["evidence_event_ids"] and "ev-0" in both["evidence_event_ids"]


def test_episode_on_another_segment_does_not_corroborate() -> None:
    (c,) = fuse([span(3)], [episode(seg="int-a2_int-a3")], FusionParams(k_min=2))
    assert not c["attributes"]["corroborated"]


def test_require_corroboration_drops_weak_solo_alarms_but_keeps_confident_ones() -> None:
    p = FusionParams(k_min=2, require_corroboration=True, solo_probability=0.9)
    assert fuse([span(3, 0.8)], [], p) == []
    assert len(fuse([span(3, 0.95)], [], p)) == 1
    assert len(fuse([span(3, 0.8)], [episode()], p)) == 1


def test_abstentions_never_become_spans() -> None:
    def derived(decision: str) -> dict:
        m = [
            {"name": "decision", "value": decision},
            {"name": "inference_mode", "value": "model"},
            {"name": "blockage_probability", "value": 0.7},
        ]
        return {
            "observation_time": "2026-09-18T09:00:30Z",
            "location": {"lane_id": f"{SEG}_0"},
            "measurements": m,
            "provenance": {"source_event_ids": ["e"]},
        }

    assert alarm_spans([derived("abstain"), derived("clear")]) == []
    assert len(alarm_spans([derived("alarm")])) == 1


def test_alarm_gap_over_a_minute_splits_the_span() -> None:
    def derived(at_s: int) -> dict:
        m = [
            {"name": "decision", "value": "alarm"},
            {"name": "inference_mode", "value": "model"},
            {"name": "blockage_probability", "value": 0.9},
        ]
        t = (T0 + timedelta(seconds=at_s)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "observation_time": t,
            "location": {"lane_id": f"{SEG}_0"},
            "measurements": m,
            "provenance": {"source_event_ids": [f"e{at_s}"]},
        }

    assert len(alarm_spans([derived(30), derived(60), derived(90)])) == 1
    assert len(alarm_spans([derived(30), derived(60), derived(200), derived(230)])) == 2


def meas(name: str, value, quality: str = "valid", confidence: float = 0.9) -> dict:
    return {
        "name": name,
        "value": value,
        "unit": "flag",
        "quality": quality,
        "confidence": confidence,
    }


def loop_event(k: int, flag=None, conflict=None, speed=None, quality: str = "valid") -> dict:
    m = []
    if flag is not None:
        m.append(meas("stopped_vehicle_flag", flag, quality))
    if conflict is not None:
        m.append(meas("direction_conflict", conflict, quality))
    if speed is not None:
        m.append(meas("mean_speed", speed))
    return {
        "event_id": f"l{k}",
        "event_type": "traffic.loop_detector.count",
        "device_id": "loop-int-a1_int-a2-0",
        "observation_time": (T0 + timedelta(seconds=30 * k)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "location": {"lane_id": f"{SEG}_0"},
        "measurements": m,
    }


def test_collision_flag_opens_extends_and_clears_one_candidate() -> None:
    evs = [
        loop_event(1, flag=True),
        loop_event(2, flag=True),
        loop_event(3, flag=True),
        loop_event(4, flag=False),
    ]
    (c,) = detect_overlay_candidates(evs)
    assert c["kind"] == "collision" and c["network_element_id"] == SEG
    assert c["onset_time"] == T0 + timedelta(seconds=30) and c["clear_time"] == T0 + timedelta(
        seconds=120
    )
    assert c["evidence_event_ids"] == ["l1", "l2", "l3"]


def test_invalid_reading_never_raises_a_candidate() -> None:
    assert (
        detect_overlay_candidates(
            [
                loop_event(1, flag=True, quality="invalid"),
                loop_event(2, flag=True, quality="invalid"),
            ]
        )
        == []
    )


def test_wrong_way_with_negative_speed_is_corroborated() -> None:
    (c,) = detect_overlay_candidates(
        [loop_event(1, conflict=True, speed=-3.0), loop_event(2, conflict=False)]
    )
    assert (
        c["kind"] == "wrong_way"
        and c["attributes"]["negative_speed_support"]
        and c["confidence"] > 0.9
    )


def test_stuck_flag_held_far_longer_than_an_incident_is_demoted_without_corroboration() -> None:
    evs = [loop_event(k, flag=True) for k in range(1, 100)]
    (c,) = detect_overlay_candidates(evs)
    assert c["attributes"]["suspect_stuck"] and c["confidence"] < 0.5


def road_event(k: int, state: str, friction: float | None) -> dict:
    m = [meas("surface_state", state)]
    if friction is not None:
        m.append(meas("friction_estimate", friction))
    return {
        "event_id": f"r{k}",
        "event_type": "road.condition_sensor.reading",
        "device_id": "road-condition-corridor-a-0",
        "observation_time": (T0 + timedelta(seconds=60 * k)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "location": {"corridor_id": "corridor-a"},
        "measurements": m,
    }


def test_flood_flag_over_dry_friction_contradicts_itself() -> None:
    (real,) = detect_overlay_candidates([road_event(1, "flooded", 0.15)])
    (fake,) = detect_overlay_candidates([road_event(1, "flooded", 0.8)])
    assert (
        real["kind"] == fake["kind"] == "flooding"
        and real["confidence"] > 0.9
        and fake["confidence"] < real["confidence"] / 1.5
    )


def test_visibility_below_threshold_opens_and_above_clears() -> None:
    def weather(k: int, vis: float) -> dict:
        return {
            "event_id": f"w{k}",
            "event_type": "weather.station.reading",
            "device_id": "weather-station-corridor-a-0",
            "observation_time": (T0 + timedelta(seconds=60 * k)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "location": {"corridor_id": "corridor-a"},
            "measurements": [
                {
                    "name": "visibility_distance",
                    "value": vis,
                    "unit": "m",
                    "quality": "valid",
                    "confidence": 0.9,
                }
            ],
        }

    (c,) = detect_overlay_candidates(
        [weather(1, 800), weather(2, 150), weather(3, 90), weather(4, 900)]
    )
    assert (
        c["kind"] == "low_visibility"
        and c["clear_time"] == T0 + timedelta(seconds=240)
        and c["evidence_event_ids"] == ["w2", "w3"]
    )
    assert detect_overlay_candidates([weather(1, 300), weather(2, 300)]) == []
