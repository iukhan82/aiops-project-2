"""P10.10: the data-quality rules catch what they claim to, say nothing about clean data, and are scored strictly."""

import pytest

from models.data_quality import detector, scoring, streams

DEVICES = streams.load_devices()
LOOP = next(d for d, t in DEVICES.items() if t == "inductive_loop")
PARAMS = detector.Params(
    stuck_run=6, silence_after_s=180.0, gap_grace_s=120.0, cusum_k=1.5, cusum_h=12.0
)


def stream_of(seed=5):
    clean = streams.clean_streams(seed, DEVICES)
    return {d: [e.copy() for e in evs] for d, evs in clean.items()}


def deliver(by_device, extra=(), drop=()):
    events = [
        e
        for evs in by_device.values()
        for e in evs
        if (e.device_id, e.sequence_number) not in set(drop)
    ] + list(extra)
    return sorted(events, key=lambda e: (e.arrival, e.device_id, e.sequence_number))


def classes_of(incidents, device=LOOP):
    return sorted({i["fault_class"] for i in incidents if i["device_id"] == device})


def test_the_generator_is_deterministic_and_the_truth_has_the_five_classes_once_per_slot():
    events_a, faults_a, _ = streams.scenario(7)
    events_b, faults_b, _ = streams.scenario(7)
    assert [(e.event_id, e.arrival) for e in events_a] == [
        (e.event_id, e.arrival) for e in events_b
    ]
    assert faults_a == faults_b
    assert {c: sum(f.fault_class == c for f in faults_a) for c in streams.CLASSES} == dict.fromkeys(
        streams.CLASSES, 4
    )
    assert all(f.onset >= streams.FIRST_FAULT_STEP * streams.CADENCE_S for f in faults_a)


def test_faults_on_one_device_never_overlap():
    _, faults, _ = streams.scenario(8)
    for device in {f.device_id for f in faults}:
        spans = sorted((f.onset, f.end) for f in faults if f.device_id == device)
        assert all(a[1] < b[0] for a, b in zip(spans, spans[1:], strict=False))


def test_clean_streams_raise_no_incident():
    events = deliver(stream_of())
    assert detector.run(PARAMS, events, DEVICES) == []


def test_a_duplicate_is_found_and_only_as_a_duplicate():
    by_device = stream_of()
    extra = [
        by_device[LOOP][k].copy(arrival=by_device[LOOP][k].arrival + 1.0) for k in range(200, 204)
    ]
    assert classes_of(detector.run(PARAMS, deliver(by_device, extra), DEVICES)) == ["duplicate"]


def test_events_that_arrive_late_are_out_of_order_and_not_missing():
    by_device = stream_of()
    for k in range(200, 206, 2):
        by_device[LOOP][k].arrival += 1.5 * streams.CADENCE_S
    assert classes_of(detector.run(PARAMS, deliver(by_device), DEVICES)) == ["out_of_order"]


def test_a_gap_that_nothing_fills_is_missing():
    by_device = stream_of()
    drop = [(LOOP, k) for k in range(200, 206)]
    assert classes_of(detector.run(PARAMS, deliver(by_device, drop=drop), DEVICES)) == ["missing"]


def test_a_frozen_sensor_is_stuck_and_its_drift_against_peers_is_explained_by_it():
    by_device = stream_of()
    frozen = dict(by_device[LOOP][200].values)
    for k in range(200, 260):
        by_device[LOOP][k].values = dict(frozen)
    assert classes_of(detector.run(PARAMS, deliver(by_device), DEVICES)) == ["stuck"]


def test_a_sensor_frozen_at_zero_is_not_called_stuck():
    by_device = stream_of()
    for k in range(200, 230):
        by_device[LOOP][k].values = dict.fromkeys(by_device[LOOP][k].values, 0.0)
    assert "stuck" not in classes_of(detector.run(PARAMS, deliver(by_device), DEVICES))


def test_a_channel_that_walks_away_from_its_peers_is_drift():
    by_device = stream_of()
    for k in range(200, 300):
        by_device[LOOP][k].values["mean_speed"] *= 1 + 0.5 * min(1.0, (k - 199) / 60)
    assert classes_of(detector.run(PARAMS, deliver(by_device), DEVICES)) == ["drift"]


def test_when_the_whole_type_moves_together_no_device_is_blamed():
    by_device = stream_of()
    loops = [d for d, t in DEVICES.items() if t == "inductive_loop"]
    for d in loops:
        for k in range(200, 300):
            by_device[d][k].values["mean_speed"] *= 1.4
    assert detector.run(PARAMS, deliver(by_device), DEVICES) == []


def test_signals_of_one_device_and_class_close_together_are_one_incident():
    tracker = detector.IncidentTracker(900.0)
    tracker.add([detector.Signal("d", "missing", t, "x") for t in (0.0, 300.0, 600.0)])
    tracker.add([detector.Signal("d", "missing", 5000.0, "x")])
    assert [(i["signals"]) for i in tracker.incidents] == [3, 1]


def test_a_signal_is_scored_strictly():
    fault = streams.Fault("d", "stuck", 1000.0, 2000.0)
    incident = {"device_id": "d", "fault_class": "stuck", "opened_at": 1500.0}
    right = scoring.score([incident], [fault], 1.0)
    assert right["true"] == 1
    assert right["strict_false_share"] == 0
    twice = scoring.score([incident, {**incident, "opened_at": 1600.0}], [fault], 1.0)
    assert twice["fragment"] == 1
    assert twice["strict_false_share"] == pytest.approx(0.5)
    wrong = scoring.score([{**incident, "fault_class": "drift"}], [fault], 1.0)
    assert wrong["wrong_class"] == 1
    assert wrong["overlap_false_share"] == 0
    far = scoring.score([{**incident, "opened_at": 9000.0}], [fault], 1.0)
    assert far["spurious"] == 1
    other_device = scoring.score([{**incident, "device_id": "e"}], [fault], 1.0)
    assert other_device["spurious"] == 1
