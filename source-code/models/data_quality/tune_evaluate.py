"""P10.10: choose the data-quality detector's numbers on the selection seeds, open the test seeds once, write the package.

    python source-code/models/data_quality/tune_evaluate.py                     # the real run: opens the test seeds ONCE
    python source-code/models/data_quality/tune_evaluate.py --selection-only    # development: never touches the test seeds
    python source-code/models/data_quality/tune_evaluate.py --reopen-reason TEXT   # a second opening, only with a recorded reason

Nothing is trained: the five rules have a handful of numbers between them (`Params`). Which seeds do what:

* SELECTION = the train and validation seeds together (9 seeds). Each class's numbers are chosen in turn - stuck, then missing/out-of-order
  (they share the gap grace), then drift - by this rule: no false ticket of that class on the selection seeds, then the highest recall,
  then the shortest median delay. Drift cannot reach zero false tickets, so its rule is: the highest recall that keeps the OVERALL strict
  false-incident share on the selection seeds at or below `SELECTION_MARGIN` (3 %, under the 5 % target, so the test has room to differ).
* VALIDATION (its 3 seeds) is reported on its own, from the chosen numbers.
* TEST (3 seeds) is opened once for the final measurement (`test_split_ledger.json`) and only reported. Nothing here is tuned to it.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from models.data_quality import detector, scoring, streams  # noqa: E402
from models.evaluation.test_gate import record_opening  # noqa: E402

PACKAGE = SOURCE_ROOT / "models" / "data_quality" / "params.json"
FEATURE_VERSION = "dq-rules/1"
SELECTION_MARGIN = 0.03
TARGET = 0.05
GRID = {
    "stuck_run": [4, 6, 8, 12],
    "missing": list(itertools.product([180.0, 240.0, 300.0, 420.0], [120.0, 150.0, 240.0])),
    "drift": list(itertools.product([1.0, 1.5, 2.0, 3.0], [6.0, 8.0, 12.0, 16.0])),
}
CODE_FILES = ("streams.py", "detector.py", "scoring.py", "tune_evaluate.py")


def code_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parent
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in CODE_FILES}


def dataset_sha256() -> str:
    """The generator and everything it draws on: its code, the device list, the seeds, the per-class count."""
    root = Path(__file__).resolve().parent
    blob = json.dumps(
        {
            "streams.py": hashlib.sha256((root / "streams.py").read_bytes()).hexdigest(),
            "devices": hashlib.sha256(streams.DEVICES_FILE.read_bytes()).hexdigest(),
            "seeds": streams.SEEDS,
            "per_class": 4,
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def load(split_seeds: list[int]) -> dict[int, tuple]:
    return {seed: streams.scenario(seed) for seed in split_seeds}


def measure(params: detector.Params, data: dict[int, tuple], only: str | None = None) -> dict:
    """Score every seed and add the counts up; the shares are computed on the totals, not averaged per seed."""
    total = {k: 0 for k in ("incidents", "true", "fragment", "wrong_class", "spurious", "faults")}
    per_class = {c: {"faults": 0, "detected": 0, "delays": []} for c in streams.CLASSES}
    hours = 0.0
    matched_faults = []
    for seed, (events, faults, devices) in data.items():
        incidents = detector.run(params, events, devices)
        h = scoring.clean_device_hours(
            faults, len(devices), streams.STEPS, streams.CADENCE_S, streams.CALIBRATION_STEPS
        )
        r = scoring.score(incidents, faults, h, only=only)
        hours += h
        for k in total:
            total[k] += r[k]
        for c in streams.CLASSES:
            per_class[c]["faults"] += r["per_class"][c]["faults"]
            per_class[c]["detected"] += r["per_class"][c]["detected"]
        # delays by class need the raw values; recompute from the match list
        only_faults = [f for f in faults if only in (None, f.fault_class)]
        for k in r["matched_faults"]:
            f = only_faults[k]
            first = min(
                i["opened_at"] for i in incidents if i["device_id"] == f.device_id and i["fault_class"] == f.fault_class and f.onset - 1.0 <= i["opened_at"] <= f.end + scoring.GRACE_S
            )  # fmt: skip
            per_class[f.fault_class]["delays"].append(first - f.onset)
        matched_faults.extend(
            (seed, f, k in r["matched_faults"]) for k, f in enumerate(only_faults)
        )
    n = total["incidents"]
    false_count = total["fragment"] + total["wrong_class"] + total["spurious"]
    summary = {
        **total,
        "strict_false_share": false_count / n if n else 0.0,
        "overlap_false_share": total["spurious"] / n if n else 0.0,
        "spurious_per_clean_device_hour": total["spurious"] / hours if hours else 0.0,
        "clean_device_hours": round(hours, 1),
        "recall": total["true"] / total["faults"] if total["faults"] else None,
        "per_class": {
            c: {
                "faults": v["faults"], "detected": v["detected"],
                "recall": v["detected"] / v["faults"] if v["faults"] else None,
                "median_delay_s": float(np.median(v["delays"])) if v["delays"] else None,
                "p95_delay_s": float(np.percentile(v["delays"], 95)) if v["delays"] else None,
            }
            for c, v in per_class.items()
        },
    }  # fmt: skip
    summary["_faults"] = matched_faults
    return summary


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    if n == 0:
        return [0.0, 1.0]
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def clean(summary: dict) -> dict:
    return {k: v for k, v in summary.items() if not k.startswith("_")}


def choose_class(
    candidates: list[tuple[object, dict]], classes: tuple[str, ...]
) -> tuple[object, dict]:
    """No false ticket of these classes, then highest recall of these classes, then shortest median delay."""

    def key(item):
        _, m = item
        false = m["false_by_class"]
        det = sum(m["per_class"][c]["detected"] for c in classes)
        delay = np.mean([m["per_class"][c]["median_delay_s"] or 1e9 for c in classes])
        return (sum(false[c] for c in classes), -det, delay)

    return min(candidates, key=key)


def main() -> int:  # noqa: PLR0915
    selection_data = load(streams.SEEDS["train"] + streams.SEEDS["validation"])
    print(f"selection seeds: {list(selection_data)}", flush=True)

    def evaluate(params: detector.Params) -> dict:
        m = measure(params, selection_data)
        # attribute false incidents to the class they claim (one pass, not five)
        counts = dict.fromkeys(streams.CLASSES, 0)
        for _, (events, faults, devices) in selection_data.items():
            incidents = detector.run(params, events, devices)
            for c in streams.CLASSES:
                r = scoring.score(incidents, faults, 1.0, only=c)
                counts[c] += r["fragment"] + r["wrong_class"] + r["spurious"]
        m["false_by_class"] = counts
        return m

    record: dict[str, list] = {"stuck_run": [], "missing": [], "drift": []}
    params = detector.Params()

    candidates = []
    for length in GRID["stuck_run"]:
        p = detector.Params(**{**params.as_dict(), "stuck_run": length})
        m = evaluate(p)
        candidates.append((length, m))
        record["stuck_run"].append({"stuck_run": length, "false": m["false_by_class"]["stuck"], "detected": m["per_class"]["stuck"]["detected"], "faults": m["per_class"]["stuck"]["faults"], "median_delay_s": m["per_class"]["stuck"]["median_delay_s"]})  # fmt: skip
    best, _ = choose_class(candidates, ("stuck",))
    params = detector.Params(**{**params.as_dict(), "stuck_run": best})
    print("stuck_run ->", best, flush=True)

    candidates = []
    for silence, grace in GRID["missing"]:
        p = detector.Params(
            **{**params.as_dict(), "silence_after_s": silence, "gap_grace_s": grace}
        )
        m = evaluate(p)
        candidates.append(((silence, grace), m))
        record["missing"].append({"silence_after_s": silence, "gap_grace_s": grace, "false": {c: m["false_by_class"][c] for c in ("missing", "out_of_order")}, "detected": {c: m["per_class"][c]["detected"] for c in ("missing", "out_of_order")}, "median_delay_s": {c: m["per_class"][c]["median_delay_s"] for c in ("missing", "out_of_order")}})  # fmt: skip
    (silence, grace), _ = choose_class(candidates, ("missing", "out_of_order"))
    params = detector.Params(
        **{**params.as_dict(), "silence_after_s": silence, "gap_grace_s": grace}
    )
    print("silence_after_s, gap_grace_s ->", silence, grace, flush=True)

    candidates = []
    for k, h in GRID["drift"]:
        p = detector.Params(**{**params.as_dict(), "cusum_k": k, "cusum_h": h})
        m = evaluate(p)
        candidates.append(((k, h), m))
        record["drift"].append({"cusum_k": k, "cusum_h": h, "drift_detected": m["per_class"]["drift"]["detected"], "drift_faults": m["per_class"]["drift"]["faults"], "overall_strict_false_share": round(m["strict_false_share"], 4), "false_incidents": m["false_by_class"]["drift"]})  # fmt: skip
    within = [c for c in candidates if c[1]["strict_false_share"] <= SELECTION_MARGIN]
    pool = within or [min(candidates, key=lambda c: c[1]["strict_false_share"])]
    (k, h), _ = max(
        pool, key=lambda c: (c[1]["per_class"]["drift"]["detected"], -c[1]["strict_false_share"])
    )
    params = detector.Params(**{**params.as_dict(), "cusum_k": k, "cusum_h": h})
    print(
        "cusum_k, cusum_h ->",
        k,
        h,
        "(within margin)" if within else "(NO candidate within the margin)",
        flush=True,
    )

    selection = measure(params, selection_data)
    validation_data = load(streams.SEEDS["validation"])
    validation = measure(params, validation_data)
    if (
        "--selection-only" in sys.argv
    ):  # development: look at the chosen numbers without opening the test seeds
        print(
            json.dumps(
                {
                    "params": params.as_dict(),
                    "selection": clean(selection),
                    "validation": clean(validation),
                },
                indent=1,
                default=str,
            )
        )
        return 0

    reason = (
        sys.argv[sys.argv.index("--reopen-reason") + 1] if "--reopen-reason" in sys.argv else None
    )
    record_opening(
        "final_comparison",
        dataset_sha256(),
        FEATURE_VERSION,
        {
            "model_id": "data-quality-rules",
            "model_version": "1.0.0",
            "params": params.as_dict(),
            "selected_on": "train+validation seeds",
        },
        reopen_reason=reason,
    )
    test_data = load(streams.SEEDS["test"])
    test = measure(params, test_data)

    def report(summary: dict) -> dict:
        out = clean(summary)
        n = out["incidents"]
        false_count = out["fragment"] + out["wrong_class"] + out["spurious"]
        out["strict_false_share_wilson95"] = wilson(false_count, n)
        out["recall_wilson95"] = wilson(out["true"], out["faults"])
        return out

    drift_by_severity = {
        "relative_offset_lt_0.25": [0, 0],
        "0.25_to_0.35": [0, 0],
        "ge_0.35": [0, 0],
    }
    drift_by_channel = {"counts": [0, 0], "continuous": [0, 0]}
    for _, f, detected in test["_faults"]:
        if f.fault_class != "drift":
            continue
        size = abs(f.detail["relative_offset"])
        bucket = (
            "relative_offset_lt_0.25"
            if size < 0.25
            else "0.25_to_0.35"
            if size < 0.35
            else "ge_0.35"
        )
        kind = (
            "counts" if f.detail["channel"] in ("vehicle_count", "cyclist_count") else "continuous"
        )
        for table, name in ((drift_by_severity, bucket), (drift_by_channel, kind)):
            table[name][1] += 1
            table[name][0] += int(detected)

    package = {
        "model_id": "data-quality-rules",
        "model_version": "1.0.0",
        "feature_version": FEATURE_VERSION,
        "purpose": "Raise data-quality incidents from observation content: missing, stuck, drift, duplicate, out-of-order (acceptance target FA-02).",
        "kind": "transparent rules; nothing is learned",
        "params": params.as_dict(),
        "selection": {
            "seeds": streams.SEEDS["train"] + streams.SEEDS["validation"],
            "rule": "per class: no false ticket, then highest recall, then shortest median delay; drift: highest recall with the overall strict false-incident share on the selection seeds at or below the margin",
            "margin": SELECTION_MARGIN,
            "grid": record,
        },
        "incident_rule": {
            "merge_gap_s": params.merge_gap_s,
            "grace_s": scoring.GRACE_S,
            "explained_by": detector.EXPLAINED_BY,
        },
        "truth_rule": "strict: an incident is true only if it is the first of the right class on the faulty device to open in [onset, end + grace]; fragments, wrong-class and spurious incidents are all counted as false",
        "metrics": {
            "selection": report(selection),
            "validation": report(validation),
            "test": report(test),
            "test_drift_recall_by_severity": drift_by_severity,
            "test_drift_recall_by_channel": drift_by_channel,
        },
        "fa_02": {
            "target": "strict false-incident share <= 5% on held-out fault scenarios",
            "test_strict_false_share": test["strict_false_share"],
            "test_strict_false_share_wilson95": wilson(
                test["fragment"] + test["wrong_class"] + test["spurious"], test["incidents"]
            ),
            "met": test["strict_false_share"] <= TARGET,
        },
        "dataset": {
            "sha256": dataset_sha256(),
            "seeds": streams.SEEDS,
            "faults_per_class_per_seed": 4,
            "devices": len(streams.load_devices()),
            "steps": streams.STEPS,
            "cadence_s": streams.CADENCE_S,
            "calibration_steps": streams.CALIBRATION_STEPS,
        },
        "provenance": {
            "code_sha256": code_hashes(),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
        "limitations": [
            "The streams are synthetic: only the device set and the levels of the continuous channels come from the P03.03 simulation. Count rates, the daily shape, noise and the shared component are assumptions, so every number here is a measurement on injected faults over seeded streams, not on a real device fleet.",
            "Faults are injected one at a time per device, never overlapping, from a fixed catalogue of five classes with fixed magnitude ranges. A fault milder than the mildest injected one, or a new kind, may pass unseen.",
            "Drift is judged against same-type peers after an hour of fault-free commissioning per device. With only three weather stations and three road-condition sensors each device has two peers, and a fault that moves two of three devices together cannot be told apart from the world changing.",
            "A sensor frozen at exactly zero looks like an empty road and is not flagged (an all-zero tuple is never 'stuck').",
            "The stuck rule needs every channel to repeat exactly. The generated readings carry four decimals and continuous noise, so a natural exact repeat never happens and a short run length is enough (the selection chose the shortest offered); a real sensor quantised to 0.1 with slow change repeats naturally and would need a longer run and a re-tune.",
            "Drift is the weak class: recall is well below the others and the median delay is tens of minutes (the fault is a slow ramp), so the measured false-incident share rests mostly on the four exact-signature classes.",
            "Incidents are in-memory records with an open/resolved status and no database table, API or console view yet; hypotheses are not offered (verified_cause is always null).",
            "Test set: 3 seeds x 4 faults per class = 12 faults per class; per-class recall intervals are wide and are reported, not hidden.",
        ],
    }
    PACKAGE.write_text(json.dumps(package, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    fa = package["fa_02"]
    print(
        f"test: {test['incidents']} incidents, strict false share {fa['test_strict_false_share']:.3f} {fa['test_strict_false_share_wilson95']} -> FA-02 {'met' if fa['met'] else 'NOT met'}; recall {test['recall']:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
