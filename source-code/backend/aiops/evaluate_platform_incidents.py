"""P10.09 / acceptance target FA-03: how often a platform incident is a false alarm, on held-out normal and degraded runs.

    python source-code/backend/aiops/evaluate_platform_incidents.py

FA-03: "AIOps platform-incident false-positive rate <= 10% on held-out normal/degraded runs, measured against P10.05 datasets,
after correlation (P10.07)".

Method. Every run of the P10.05 TEST split (2 seeds x 7 scenarios x 2 replicates) is replayed step by step through the same
code the live correlator runs: the packaged P10.06 detector scores the run's eight signals, an alarm is attributed to the
signal(s) that moved (`platform_signals.anomaly_signals`), the P10.07 correlator groups them, and the incident repository (a
scratch database) opens, updates and resolves incidents exactly as it would live. Only steps from `FIRST_STEP` on are used
(before it the signals' rate windows are still filling; the detector was trained and its threshold chosen the same way).

An incident is TRUE only if it OPENED inside its run's fault window: no earlier than `LEAD_S` before it (an incident is back-dated
to the first step above the threshold) and no later than `OPEN_LAG_S` after its end (the 30 s rate window and the alarm persistence).
One that opens before the fault, after it, or in a run with no fault is FALSE. FA-03 is false incidents / all incidents.

The first version of this rule counted an incident TRUE when its lifetime merely overlapped the fault window. That gave 0 false of 27,
and was tightened before anything was reported, because it let a false alarm from the healthy baseline that happened to still be alive
when a fault arrived (and a recovery transient) pass as a detection. The overlap figure is still reported beside the strict one.
Also reported: false incidents per hour of normal run, and how many degraded runs produced at least one true incident.

Only the alert source is absent (the datasets carry the eight signals, not Prometheus alerts), so this measures the detector plus
the correlator, not the alert rules. The test split is opened here for a NON-SELECTING use (recorded in the ledger): nothing in the
detector, threshold or correlator rules was chosen or changed using it.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.aiops import correlation, platform_correlator, platform_signals  # noqa: E402
from backend.demo import world  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.repositories import platform_incidents as repo  # noqa: E402
from database.migrate import dsn_from_env, migrate  # noqa: E402
from models.evaluation.test_gate import record_opening  # noqa: E402
from models.operations_detector.evaluate import wilson  # noqa: E402
from models.operations_detector.features import (  # noqa: E402
    DATASET_ROOT,
    FEATURE_VERSION,
    FIRST_STEP,
    STEP_S,
    load_split,
)

ev = Evidence("P10.09", "p10_09_incident_quality", docs_name="p10_09_incident_quality")
DB = "aiops_p1009_quality"
FA_03_LIMIT = 0.10
OPEN_LAG_S = 30  # the 30 s rate window and the alarm persistence
LEAD_S = (
    10  # an incident is back-dated by (PERSISTENCE - 1) steps to the first step above threshold
)
T0 = datetime(2026, 6, 1, 8, 0, 0, tzinfo=UTC)


def fresh_database() -> None:
    os.environ["POSTGRES_DB"] = "aiops"
    world.use_database(DB)
    if world.database_exists(DB):
        world.drop_database(DB)
    with psycopg.connect(world._maintenance_dsn(), autocommit=True) as conn:  # noqa: SLF001
        conn.execute(f'CREATE DATABASE "{DB}"')
    with psycopg.connect(dsn_from_env()) as conn:
        migrate(conn)


def replay_run(conn: psycopg.Connection, admin: psycopg.Connection, detector, run) -> list[dict]:  # noqa: ANN001
    """Replay one run; returns its incidents as {opened, resolved} in run seconds."""
    times = [T0 + timedelta(seconds=i * STEP_S) for i in range(run.steps)]
    timeline = platform_signals.build_anomaly_timeline(detector, times, run.raw)
    admin.execute("DELETE FROM platform_incidents")
    for i in range(FIRST_STEP, run.steps):
        now = times[i]
        signals = platform_signals.anomaly_signals(timeline, now)
        repo.sync(conn, correlation.correlate(signals, now), now)
    # let the last incidents settle exactly as they would after the run ends
    end = times[-1]
    for extra in range(1, int(correlation.RESOLVE_AFTER_S // STEP_S) + 2):
        now = end + timedelta(seconds=extra * STEP_S)
        repo.sync(conn, [], now)
    rows = admin.execute(
        "SELECT platform_incident_id, opened_at, resolved_at, status FROM platform_incidents ORDER BY opened_at"
    ).fetchall()
    return [
        {
            "opened_s": (opened - T0).total_seconds(),
            "resolved_s": (resolved - T0).total_seconds() if resolved else None,
            "components": admin.execute(
                "SELECT components FROM platform_incidents WHERE platform_incident_id = %s", (pid,)
            ).fetchone()[0],
        }
        for pid, opened, resolved, _ in rows
    ]


def main() -> int:
    world.load_platform_env()
    detector = platform_correlator.load_detector()
    ev.check("the_packaged_p10_06_detector_is_available", detector is not None)
    if detector is None:
        return ev.finish()
    manifest = json.loads(
        (DATASET_ROOT / "run-a" / "dataset_manifest.json").read_text(encoding="utf-8")
    )
    runs = load_split("test")
    record_opening(
        "fa03_platform_incident_evaluation",
        manifest["dataset_sha256"],
        FEATURE_VERSION,
        {
            "task": "P10.09",
            "note": "non-selecting: detector, threshold and correlator rules were fixed before this run",
        },
    )
    started = time.time()
    fresh_database()
    os.environ["POSTGRES_DB"] = DB
    per_run, true_incidents, false_incidents = [], 0, 0
    normal_seconds, false_in_normal = 0.0, 0
    fault_runs, fault_runs_detected = 0, 0
    lenient_true = incident_total = 0
    with (
        psycopg.connect(dsn_from_env(role="svc_platform_correlator"), autocommit=True) as conn,
        psycopg.connect(dsn_from_env(), autocommit=True) as admin,
    ):
        for run in runs:
            found = replay_run(conn, admin, detector, run)
            has_fault = bool(run.fault.any())
            window = overlap = None
            if has_fault:
                idx = run.fault.nonzero()[0]
                window = (idx[0] * STEP_S - LEAD_S, idx[-1] * STEP_S + OPEN_LAG_S)
                overlap = (idx[0] * STEP_S, idx[-1] * STEP_S + correlation.RESOLVE_AFTER_S + STEP_S)
            verdicts = []
            for inc in found:
                true = bool(window and window[0] <= inc["opened_s"] <= window[1])
                verdicts.append(true)
                end = inc["resolved_s"] if inc["resolved_s"] is not None else float("inf")
                lenient_true += int(
                    bool(overlap and inc["opened_s"] <= overlap[1] and end >= overlap[0])
                )
                incident_total += 1
            t = sum(verdicts)
            f = len(verdicts) - t
            true_incidents += t
            false_incidents += f
            if has_fault:
                fault_runs += 1
                fault_runs_detected += 1 if t else 0
            else:
                normal_seconds += (run.steps - FIRST_STEP) * STEP_S
                false_in_normal += f
            per_run.append(
                {
                    "run": run.run_id,
                    "scenario": run.scenario,
                    "severity": round(run.severity, 2),
                    "incidents": len(found),
                    "true": t,
                    "false": f,
                    "components": sorted({c for inc in found for c in inc["components"]}),
                }
            )
            print(f"{run.run_id}: {len(found)} incident(s), {t} true, {f} false", flush=True)
    total = true_incidents + false_incidents
    rate = false_incidents / total if total else 0.0
    low, high = wilson(false_incidents, total) if total else (0.0, 1.0)
    ev.metrics["fa_03"] = {
        "test_runs": len(runs),
        "incidents": total,
        "true_incidents": true_incidents,
        "false_incidents": false_incidents,
        "false_incident_rate": rate,
        "wilson95": [low, high],
        "limit": FA_03_LIMIT,
        "overlap_rule_false_incident_rate": (incident_total - lenient_true) / incident_total
        if incident_total
        else None,
        "overlap_rule_false_incidents": incident_total - lenient_true,
        "false_incidents_per_hour_of_normal_run": (false_in_normal / (normal_seconds / 3600.0))
        if normal_seconds
        else None,
        "normal_run_hours": normal_seconds / 3600.0,
        "degraded_runs": fault_runs,
        "degraded_runs_with_a_true_incident": fault_runs_detected,
        "dataset_sha256": manifest["dataset_sha256"],
        "detector": detector.card.get("served_detector"),
        "per_run": per_run,
        "seconds": round(time.time() - started),
    }
    ev.check(
        "FA_03_platform_incident_false_positive_rate_is_at_most_10_percent_on_the_held_out_test_runs",
        total > 0 and rate <= FA_03_LIMIT,
        f"{false_incidents} false of {total} incidents = {rate:.1%} (Wilson 95% {low:.1%}-{high:.1%}); "
        f"{fault_runs_detected} of {fault_runs} degraded runs produced a true incident",
    )
    ev.notes["scope"] = (
        "detector plus correlator on the eight P10.05 signals; the alert rules are not part of this measurement; small sample (2 test seeds)"
    )
    os.environ["POSTGRES_DB"] = "aiops"
    world.use_database(DB)
    world.drop_database(DB)
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
