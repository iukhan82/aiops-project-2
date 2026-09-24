"""Evaluation metrics, the test-split ledger, and dataset integrity."""

import json
from pathlib import Path

import numpy as np
import pytest

from models.evaluation.metrics import (
    cluster_bootstrap,
    confusion,
    episode_metrics,
    failure_cases,
    prf,
    sliced,
)
from models.evaluation.test_gate import TestSplitReopened, record_opening

DATASET = Path(__file__).resolve().parents[1] / "models" / "dataset" / "output" / "run-a"


def _row(run, device, minute, incident=None, scale=6.0, dist=None):
    return {
        "run_id": run,
        "device_id": device,
        "interval_end": f"2026-09-18T09:{minute:02d}:00.000Z",
        "incident_id": incident,
        "run_spec": f"blockage-{scale}",
        "demand_scale": scale,
        "stall_distance_from_loop_m": dist,
    }


def test_confusion_and_prf_hand_computed() -> None:
    y = np.array([1, 1, 1, 0, 0, 0, 0, 0])
    p = np.array([1, 1, 0, 1, 0, 0, 0, 0])
    c = confusion(y, p)
    assert c == {"tp": 2, "fp": 1, "fn": 1, "tn": 4}
    m = prf(c)
    assert m["precision"] == pytest.approx(2 / 3) and m["recall"] == pytest.approx(2 / 3)
    assert m["f1"] == pytest.approx(2 / 3) and m["fpr"] == pytest.approx(1 / 5)
    assert prf({"tp": 0, "fp": 0, "fn": 0, "tn": 0})["f1"] == 0.0


def test_episode_metrics_delay_missed_incidents_and_false_alarm_episodes() -> None:
    # device d1: rows at minutes 1..8; incident A positive rows minutes 3,4,5 (onset 150 s)
    meta = [_row("r1", "d1", m, incident="A" if 3 <= m <= 5 else None) for m in range(1, 9)]
    y = np.array([1 if 3 <= m <= 5 else 0 for m in range(1, 9)])
    # alarm first fires at minute 4 (detected, late), plus an isolated false alarm at 7
    pred = np.zeros(8, dtype=int)
    pred[3] = 1
    pred[6] = 1
    # a second incident B on d2 that is never alarmed
    meta += [_row("r1", "d2", m, incident="B") for m in (2, 3)]
    y = np.concatenate([y, [1, 1]])
    pred = np.concatenate([pred, [0, 0]])
    incidents = {"A": {"onset_s": 150.0}, "B": {"onset_s": 100.0}}

    e = episode_metrics(meta, y, pred, incidents)
    assert e["incidents"] == 2 and e["incidents_detected"] == 1
    assert e["incident_recall"] == 0.5
    assert e["median_detection_delay_s"] == 240.0 - 150.0  # first alarm row ends at minute 4
    assert e["missed_incident_ids"] == ["B"]
    assert e["false_alarm_episodes"] == 1  # only the minute-7 alarm; minute 4 overlaps a positive


def test_alarm_run_touching_a_positive_row_is_not_a_false_alarm() -> None:
    meta = [_row("r", "d", m, incident="A" if m == 5 else None) for m in range(1, 9)]
    y = np.array([1 if m == 5 else 0 for m in range(1, 9)])
    pred = np.array([0, 0, 0, 1, 1, 1, 0, 0])  # spans the positive row at minute 5
    e = episode_metrics(meta, y, pred, {"A": {"onset_s": 270.0}})
    assert e["false_alarm_episodes"] == 0 and e["incidents_detected"] == 1


def test_cluster_bootstrap_is_seeded_and_brackets_the_point_estimate() -> None:
    rng = np.random.default_rng(0)
    meta, y, pred = [], [], []
    for run in range(12):
        for k in range(20):
            label = int(rng.random() < 0.3)
            meta.append(_row(f"r{run}", "d", k))
            y.append(label)
            pred.append(label if rng.random() < 0.8 else 1 - label)
    y, pred = np.array(y), np.array(pred)
    a = cluster_bootstrap(meta, y, pred, samples=200)
    b = cluster_bootstrap(meta, y, pred, samples=200)
    assert a == b
    point = prf(confusion(y, pred))
    for key in ("precision", "recall", "f1"):
        assert a[key][0] <= point[key] <= a[key][1]


def test_sliced_reports_sample_sizes_per_condition() -> None:
    meta = [_row("r", "d", m, scale=s) for m, s in enumerate([1.0, 1.0, 6.0, 6.0, 6.0], start=1)]
    y = np.array([0, 1, 1, 1, 0])
    pred = np.array([0, 1, 1, 0, 0])
    by = {s["value"]: s for s in sliced(meta, y, pred, "demand_scale")}
    assert by[1.0]["n"] == 2 and by[6.0]["n"] == 3
    assert by[6.0]["recall"] == pytest.approx(0.5)


def test_failure_cases_lists_misses_with_their_evidence() -> None:
    names = ("occ_last", "speed_min")
    meta = [_row("r", "d", m, incident="A", dist=70) for m in (1, 2)]
    X = np.array([[0.01, 9.0], [0.02, 8.0]], dtype=np.float32)
    y = np.array([1, 1])
    pred = np.array([0, 0])
    fc = failure_cases(meta, X, names, y, pred, {"A": {"onset_s": 30.0}})
    miss = fc["missed_incidents_worst"][0]
    assert miss["stall_distance_from_loop_m"] == 70 and miss["positive_intervals"] == 2
    assert miss["max_occ_during_incident"] == pytest.approx(0.02)
    assert miss["min_speed_during_incident"] == pytest.approx(8.0)


def test_test_split_ledger_allows_one_selecting_opening_only(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    record_opening("final_comparison", "abc", "f/1", {"model": "m1"}, path=ledger)
    with pytest.raises(TestSplitReopened):
        record_opening("final_comparison", "abc", "f/1", {"model": "m2"}, path=ledger)
    # a different dataset is a different opening
    record_opening("final_comparison", "def", "f/1", {"model": "m2"}, path=ledger)
    # an explicit reason is recorded, not silent
    record_opening(
        "final_comparison", "abc", "f/1", {"model": "m3"}, reopen_reason="bug fix", path=ledger
    )
    entries = json.loads(ledger.read_text(encoding="utf-8"))["entries"]
    assert [e.get("reopen_reason") for e in entries if e["dataset_sha256"] == "abc"] == [
        None,
        "bug fix",
    ]


def test_test_split_ledger_logs_non_selecting_uses_without_blocking(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    for _ in range(3):
        record_opening("runtime_parity", "abc", "f/1", {"ok": True}, path=ledger)
    entries = json.loads(ledger.read_text(encoding="utf-8"))["entries"]
    assert len([e for e in entries if e["purpose"] == "runtime_parity"]) == 1  # latest only


def test_committed_test_ledger_records_no_unexplained_reopening() -> None:
    ledger = Path(__file__).resolve().parents[1] / "models" / "registry" / "test_split_ledger.json"
    if not ledger.is_file():
        pytest.skip("ledger is created by P04.03's final comparison")
    entries = json.loads(ledger.read_text(encoding="utf-8"))["entries"]
    finals = [e for e in entries if e["purpose"] == "final_comparison"]
    by_key: dict[tuple, int] = {}
    for e in finals:
        key = (e["dataset_sha256"], e["feature_version"])
        by_key[key] = by_key.get(key, 0) + 1
        assert by_key[key] == 1 or e.get("reopen_reason"), "unexplained test-split reopening"


# --- dataset integrity (skipped when the SUMO-built dataset is absent) --------------------


@pytest.fixture(scope="module")
def dataset():
    if not (DATASET / "dataset_manifest.json").is_file():
        pytest.skip("edge dataset not built (run models/dataset/run_container.sh)")
    from models.dataset import loader

    return loader


def test_dataset_manifest_verifies_against_disk(dataset) -> None:
    assert dataset.verify_manifest() == []


def test_dataset_splits_use_disjoint_seeds_and_run_ids(dataset) -> None:
    manifest = dataset.load_manifest()
    seeds = manifest["split_seeds"]
    all_seeds = [s for v in seeds.values() for s in v]
    assert len(all_seeds) == len(set(all_seeds)) == 9
    seen: dict[str, str] = {}
    event_ids: dict[str, str] = {}
    for split in dataset.SPLITS:
        for run_dir in dataset.run_dirs(DATASET, split):
            assert run_dir.name not in seen, "run_id reused across splits"
            seen[run_dir.name] = split
            assert f"-{split}-" in run_dir.name
            for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines():
                eid = json.loads(line)["event_id"]
                assert event_ids.setdefault(eid, split) == split, "event_id crosses a split"
    assert len(seen) == 108


def test_dataset_labels_are_separate_from_events_and_come_from_measured_incidents(dataset) -> None:
    incidents = dataset.load_incidents()
    assert len(incidents) == 216
    invalid = [i["incident_id"] for i in incidents.values() if not i["valid"]]
    assert len(invalid) <= 2, f"too many unmeasurable incidents: {invalid}"  # blocker never arrived
    run_dir = dataset.run_dirs(DATASET, "train")[-1]
    event = json.loads((run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert not {"label", "incident_id", "scenario", "incident"} & set(event)
    assert not any("incident" in m["name"] or "label" in m["name"] for m in event["measurements"])
    for i in incidents.values():
        assert len(i["blocker_vehicle_ids"]) == 2
        if i["valid"]:
            assert i["onset_s"] < i["end_s"]


def test_dataset_had_no_teleports_or_collisions(dataset) -> None:
    runs = dataset.load_manifest()["runs"]
    assert len(runs) == 108
    assert all(r["teleports"] == 0 and r["collisions"] == 0 for r in runs)


def test_feature_table_is_built_by_edge_code_and_has_positives_in_every_split(dataset) -> None:
    table = dataset.build_feature_table()
    assert table.feature_version == "loop-window/2" and table.X.shape[1] == 24
    assert not np.isnan(table.X).any()
    for split in dataset.SPLITS:
        mask = table.mask(split=split)
        assert mask.sum() > 5000 and table.y[mask].sum() > 50
