"""P06.05: specification test of the overlay-signature detectors.

    python source-code/backend/analytics/evaluate_overlays.py

No threshold here is fitted to data: they are safety/physics constants fixed in
overlay_candidates.py before this ran, so there is no validation/test selection
and the test ledger is not used. What is measured, and reported as a
specification test rather than detection skill (the signatures are synthetic):

a) P03.05's real overlay scenarios: each of collision / wrong-way / flood /
   visibility yields exactly one candidate with the right device, onset and
   clear time; the signal-fault overlay yields none (a device fault).
b) Seeded injection over all nine dataset seeds (fresh onsets, durations,
   devices, confidences) - recall and timing - plus hard negatives that must
   not raise anything: invalid-quality flags, sub-floor confidence, moderate
   visibility, wet-but-not-flooded road.
c) A flag stuck for two hours is demoted and marked suspect; a flood flag over
   dry-road friction is demoted; both stay below 0.5 confidence.
d) Device/platform faults (P03.06, all splits) and the P03.03 normal run raise
   no safety candidate.
"""

from __future__ import annotations

import copy
import json
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.kpis import parse_time  # noqa: E402
from backend.analytics.overlay_candidates import detect_overlay_candidates  # noqa: E402

DATASET = SOURCE_ROOT / "models" / "intelligence_dataset" / "output" / "run-a"
SCEN = SOURCE_ROOT / "simulator" / "scenarios" / "output" / "run-a"
SENSORS = SOURCE_ROOT / "simulator" / "sensors" / "output" / "run-a"
FAULTS = SOURCE_ROOT / "simulator" / "datasets" / "output" / "run-a"
REGISTRY = SOURCE_ROOT / "models" / "registry" / "overlay-detectors"
ANCHOR = datetime(2026, 9, 18, 9, 0, 0, tzinfo=timezone.utc)
SAFETY_KINDS = {"collision", "wrong_way", "flooding", "low_visibility"}
TYPE_TO_KIND = {
    "collision": "collision",
    "wrong_way": "wrong_way",
    "flood": "flooding",
    "visibility": "low_visibility",
}


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def m(name, value, unit, quality="suspect", confidence=0.8):
    return {
        "name": name,
        "value": value,
        "unit": unit,
        "quality": quality,
        "confidence": confidence,
    }


class Factory:
    """Builds envelope events from the real P03.05 overlay events as templates."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.seed = seed
        overlays = jsonl(SCEN / "overlay_events.jsonl")
        self.template = {
            t: next(e for e in overlays if e["correlation_id"].endswith(t))
            for t in ("collision", "wrong_way", "flood", "visibility")
        }
        self.loops = [d for d in jsonl(DATASET / "devices.jsonl")]
        sensors = jsonl(SENSORS / "devices.jsonl")
        self.road = [d for d in sensors if d["device_type"] == "road_condition_sensor"]
        self.weather = [d for d in sensors if d["device_type"] == "weather_station"]
        self.n = 0

    def event(self, kind: str, device: dict, at: datetime, measurements: list[dict]) -> dict:
        e = copy.deepcopy(self.template[kind])
        self.n += 1
        loc = device["location"]
        e.update(
            device_id=device["device_id"],
            event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"overlay-eval:{self.seed}:{self.n}")),
            observation_time=iso(at),
            ingest_time=iso(at + timedelta(seconds=0.25)),
            sequence_number=self.n,
            measurements=measurements,
        )
        e["location"] = {
            k: v
            for k, v in loc.items()
            if k
            in (
                "coordinate_reference",
                "latitude",
                "longitude",
                "corridor_id",
                "lane_id",
                "intersection_id",
            )
        }
        e["event_type"] = {
            "collision": "traffic.loop_detector.count",
            "wrong_way": "traffic.loop_detector.count",
            "flood": "road.condition_sensor.reading",
            "visibility": "weather.station.reading",
        }[kind]
        return e


def positives(f: Factory) -> tuple[list[dict], list[dict]]:
    events, truth = [], []
    busy: dict[tuple[str, str], list[tuple[datetime, datetime]]] = {}
    for kind in ("collision", "wrong_way", "flood", "visibility"):
        for _ in range(6):
            while True:  # one device holds one hazard at a time: overlapping injections would be a single state
                dev = f.rng.choice(
                    f.loops
                    if kind in ("collision", "wrong_way")
                    else f.road
                    if kind == "flood"
                    else f.weather
                )
                on = ANCHOR + timedelta(seconds=f.rng.randint(600, 9000))
                end = on + timedelta(seconds=f.rng.randint(60, 900))
                taken = busy.setdefault((kind, dev["device_id"]), [])
                if all(
                    end + timedelta(seconds=120) < a or b + timedelta(seconds=120) < on
                    for a, b in taken
                ):
                    taken.append((on, end))
                    break
            conf = round(f.rng.uniform(0.5, 0.95), 2)
            q = f.rng.choice(["valid", "suspect"])
            if kind == "collision":
                a = [
                    m("vehicle_count", 0, "count", "suspect", 0.4),
                    m("stopped_vehicle_flag", True, "boolean", q, conf),
                ]
                b = [
                    m("vehicle_count", 2, "count", "valid", 0.97),
                    m("stopped_vehicle_flag", False, "boolean", "valid", 0.95),
                ]
            elif kind == "wrong_way":
                a = [
                    m("direction_conflict", True, "boolean", q, conf),
                    m("mean_speed", -8.5, "m_s-1", "suspect", 0.6),
                ]
                b = [m("direction_conflict", False, "boolean", "valid", 0.95)]
            elif kind == "flood":
                a = [
                    m("surface_state", "flooded", "category", q, conf),
                    m("friction_estimate", 0.18, "ratio", "suspect", 0.75),
                ]
                b = [
                    m("surface_state", "wet", "category", "valid", 0.9),
                    m("friction_estimate", 0.6, "ratio", "valid", 0.88),
                ]
            else:
                a = [m("visibility_distance", round(f.rng.uniform(40, 190), 0), "m", q, conf)]
                b = [m("visibility_distance", 9000.0, "m", "valid", 0.9)]
            events += [f.event(kind, dev, on, a), f.event(kind, dev, end, b)]
            truth.append(
                {"kind": TYPE_TO_KIND[kind], "device_id": dev["device_id"], "onset": on, "end": end}
            )
    return events, truth


def negatives(f: Factory) -> list[dict]:
    ev = []
    for _ in range(6):
        loop = f.rng.choice(f.loops)
        at = ANCHOR + timedelta(seconds=f.rng.randint(600, 9000))
        ev.append(
            f.event(
                "collision", loop, at, [m("stopped_vehicle_flag", True, "boolean", "invalid", 0.9)]
            )
        )
        ev.append(
            f.event(
                "collision",
                loop,
                at + timedelta(seconds=30),
                [m("stopped_vehicle_flag", True, "boolean", "suspect", 0.2)],
            )
        )
        ev.append(
            f.event(
                "wrong_way",
                loop,
                at + timedelta(seconds=60),
                [m("direction_conflict", True, "boolean", "invalid", 0.9)],
            )
        )
        ev.append(
            f.event(
                "visibility",
                f.rng.choice(f.weather),
                at,
                [m("visibility_distance", 350.0, "m", "valid", 0.9)],
            )
        )
        ev.append(
            f.event(
                "flood",
                f.rng.choice(f.road),
                at,
                [
                    m("surface_state", "wet", "category", "valid", 0.9),
                    m("friction_estimate", 0.35, "ratio", "valid", 0.8),
                ],
            )
        )
    return ev


def run_seed(seed: int) -> dict:
    f = Factory(seed)
    pos_events, truth = positives(f)
    neg_events = negatives(f)
    # Positives and hard negatives run as separate passes: a wet-road or clear-visibility reading
    # arriving mid-hazard on the same device would (correctly) close that hazard.
    negative_candidates = detect_overlay_candidates(neg_events)
    cands = detect_overlay_candidates(pos_events)
    matched, timing = 0, []
    used = set()
    for t in truth:
        for i, c in enumerate(cands):
            if (
                i in used
                or c["kind"] != t["kind"]
                or c["attributes"]["device_id"] != t["device_id"]
            ):
                continue
            if c["onset_time"] == t["onset"] and c["clear_time"] == t["end"]:
                used.add(i)
                matched += 1
                timing.append(0.0)
                break
    per_kind = {}
    for k in SAFETY_KINDS:
        n_truth = sum(1 for t in truth if t["kind"] == k)
        n_match = sum(
            1
            for t in truth
            if t["kind"] == k
            and any(
                c["kind"] == k
                and c["attributes"]["device_id"] == t["device_id"]
                and c["onset_time"] == t["onset"]
                and c["clear_time"] == t["end"]
                for c in cands
            )
        )
        per_kind[k] = {"injected": n_truth, "detected_exactly": n_match}
    return {
        "seed": seed,
        "injected": len(truth),
        "matched": matched,
        "candidates": len(cands),
        "unexplained_candidates": len(cands) - len(used),
        "hard_negative_events": len(neg_events),
        "candidates_from_hard_negatives": len(negative_candidates),
        "per_kind": per_kind,
    }


def stuck_and_contradiction() -> dict:
    f = Factory(1)
    loop, road = f.loops[0], f.road[0]
    stuck = [
        f.event(
            "collision",
            loop,
            ANCHOR + timedelta(seconds=30 * i),
            [m("stopped_vehicle_flag", True, "boolean", "suspect", 0.85)],
        )
        for i in range(240)
    ]
    flood = [
        f.event(
            "flood",
            road,
            ANCHOR,
            [
                m("surface_state", "flooded", "category", "valid", 0.9),
                m("friction_estimate", 0.7, "ratio", "valid", 0.8),
            ],
        )
    ]
    control = [
        f.event(
            "collision",
            f.loops[1],
            ANCHOR,
            [m("stopped_vehicle_flag", True, "boolean", "valid", 0.9)],
        ),
        f.event(
            "collision",
            f.loops[1],
            ANCHOR + timedelta(seconds=120),
            [m("stopped_vehicle_flag", False, "boolean", "valid", 0.9)],
        ),
    ]
    (s,) = detect_overlay_candidates(stuck)
    (c,) = detect_overlay_candidates(flood)
    (ok,) = detect_overlay_candidates(control)
    return {
        "stuck_flag_candidates": 1,
        "stuck_flag_suspect": s["attributes"]["suspect_stuck"],
        "stuck_flag_confidence": s["confidence"],
        "contradicted_flood_confidence": c["confidence"],
        "uncontradicted_reference_confidence": ok["confidence"],
    }


def main() -> int:
    overlays = jsonl(SCEN / "overlay_events.jsonl")
    truth = jsonl(SCEN / "ground_truth.jsonl")
    cands = detect_overlay_candidates(overlays)
    p03 = {}
    for t in truth:
        kind = TYPE_TO_KIND.get(t["scenario_type"])
        if kind is None:
            continue
        hit = [
            c
            for c in cands
            if c["kind"] == kind
            and c["onset_time"] == parse_time(t["onset"])
            and c["clear_time"] == parse_time(t["end"])
        ]
        p03[kind] = {
            "candidates_with_exact_onset_and_clear": len(hit),
            "device_matches": bool(hit)
            and hit[0]["attributes"]["device_id"] in t["affected_entities"],
        }
    signal_candidates = [c for c in cands if c["kind"] not in SAFETY_KINDS]

    seeds = json.loads((DATASET / "dataset_manifest.json").read_text(encoding="utf-8"))[
        "split_seeds"
    ]
    by_split = {split: [run_seed(s) for s in ss] for split, ss in seeds.items()}
    flat = [r for rs in by_split.values() for r in rs]
    faults = [
        e
        for split in ("train", "validation", "test")
        for e in jsonl(FAULTS / split / "fault_events.jsonl")
    ]
    normal = jsonl(SENSORS / "observations.jsonl")
    report = {
        "schema": "overlay-detector-specification-test-v1",
        "detector": "rule:overlay/1",
        "nature": "specification test on synthetic signatures; no thresholds were fitted, so no validation/test selection applies",
        "p03_05_overlays": p03,
        "p03_05_signal_overlay_safety_candidates": len(signal_candidates),
        "seeded_injection_by_split": by_split,
        "injection_totals": {
            "injected": sum(r["injected"] for r in flat),
            "detected_exactly": sum(r["matched"] for r in flat),
            "unexplained_candidates_in_positive_pass": sum(
                r["unexplained_candidates"] for r in flat
            ),
            "hard_negative_events": sum(r["hard_negative_events"] for r in flat),
            "candidates_from_hard_negatives": sum(
                r["candidates_from_hard_negatives"] for r in flat
            ),
        },
        "stuck_and_contradiction": stuck_and_contradiction(),
        "fault_streams_safety_candidates": len(detect_overlay_candidates(faults)),
        "fault_events_checked": len(faults),
        "p03_normal_run_safety_candidates": len(detect_overlay_candidates(normal)),
        "p03_normal_events_checked": len(normal),
        "limitations": [
            "Signatures are the explicit flags P03.05's overlay generator writes; passing shows correct handling around a flag, not the ability to find one in raw physics.",
            "Only the collision/wrong-way loop overlays sit on real SUMO telemetry (for corroboration); flood/visibility are stand-alone series.",
            "No claim about real sensors, real collisions or real weather.",
        ],
    }
    REGISTRY.mkdir(parents=True, exist_ok=True)
    (REGISTRY / "evaluation.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "p03_05_overlays",
                    "injection_totals",
                    "stuck_and_contradiction",
                    "fault_streams_safety_candidates",
                    "p03_normal_run_safety_candidates",
                )
            },
            indent=1,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
