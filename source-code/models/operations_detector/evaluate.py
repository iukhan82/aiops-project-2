"""P10.06: alarm rule, threshold selection and the metrics.

Alarm rule (the same for every detector): an alarm is raised at a step when the score has been at or above the threshold for
`PERSISTENCE` consecutive steps (15 s), so one noisy sample cannot page anyone.

Threshold selection uses the VALIDATION split only: the lowest threshold whose false-alarm episodes per hour, counted over
every step outside a fault window (baseline, benign bursts, recovery), stay within `FALSE_ALARM_BUDGET_PER_HOUR`. The
test split never influences a threshold, a model choice or a feature (`train_evaluate.py` opens it once, in the ledger).

Events, not just steps. A degraded run is DETECTED when an alarm episode overlaps its fault window; the delay is from the
fault's onset to the first alarming step inside the window. A false-alarm episode is an alarm episode that touches no fault
window. Recall is reported with a Wilson interval, false alarms per hour with an exact Poisson interval.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats

from models.operations_detector.detectors import Prepared
from models.operations_detector.features import STEP_S

PERSISTENCE = 3
FALSE_ALARM_BUDGET_PER_HOUR = 3.0


def alarms(scores: np.ndarray, threshold: float, persistence: int = PERSISTENCE) -> np.ndarray:
    above = scores >= threshold
    out = np.zeros_like(above)
    run = 0
    for i, flag in enumerate(above):
        run = run + 1 if flag else 0
        out[i] = run >= persistence
    return out


def episodes(flags: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous True blocks as (first, last) step positions."""
    out, start = [], None
    for i, flag in enumerate(flags):
        if flag and start is None:
            start = i
        if not flag and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(flags) - 1))
    return out


@dataclass
class RunResult:
    run_id: str
    scenario: str
    severity: float
    detected: bool | None  # None for a run without a fault
    delay_s: float | None
    false_alarm_episodes: int
    quiet_seconds: float
    burst_false_alarms: int
    step_tp: int
    step_fp: int
    step_fn: int


def score_run(p: Prepared, scores: np.ndarray, threshold: float) -> RunResult:
    flags = alarms(scores, threshold)
    fault = p.fault
    blocks = episodes(flags)
    false_blocks = [b for b in blocks if not fault[b[0] : b[1] + 1].any()]
    detected, delay = None, None
    if fault.any():
        first_fault = int(np.flatnonzero(fault)[0])
        hit = np.flatnonzero(flags & fault)
        detected = bool(hit.size)
        delay = float((hit[0] - first_fault) * STEP_S) if hit.size else None
    return RunResult(
        run_id=p.run.run_id,
        scenario=p.run.scenario,
        severity=p.run.severity,
        detected=detected,
        delay_s=delay,
        false_alarm_episodes=len(false_blocks),
        quiet_seconds=float((~fault).sum() * STEP_S),
        burst_false_alarms=sum(1 for b in false_blocks if p.burst[b[0] : b[1] + 1].any()),
        step_tp=int((flags & fault).sum()),
        step_fp=int((flags & ~fault).sum()),
        step_fn=int((~flags & fault).sum()),
    )


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def poisson_rate_interval(
    events: int, hours: float, confidence: float = 0.95
) -> tuple[float, float]:
    alpha = 1 - confidence
    low = 0.0 if events == 0 else stats.chi2.ppf(alpha / 2, 2 * events) / 2
    high = stats.chi2.ppf(1 - alpha / 2, 2 * (events + 1)) / 2
    return (low / hours, high / hours)


def summarise(results: list[RunResult]) -> dict:
    faulty = [r for r in results if r.detected is not None]
    hits = [r for r in faulty if r.detected]
    quiet_hours = sum(r.quiet_seconds for r in results) / 3600.0
    false_total = sum(r.false_alarm_episodes for r in results)
    tp, fp, fn = (sum(getattr(r, k) for r in results) for k in ("step_tp", "step_fp", "step_fn"))
    delays = [r.delay_s for r in hits if r.delay_s is not None]
    by_type = {}
    for scenario in sorted({r.scenario for r in faulty}):
        group = [r for r in faulty if r.scenario == scenario]
        d = [r.delay_s for r in group if r.detected and r.delay_s is not None]
        by_type[scenario] = {
            "runs": len(group),
            "detected": sum(1 for r in group if r.detected),
            "median_delay_s": float(np.median(d)) if d else None,
        }
    return {
        "fault_runs": len(faulty),
        "detected": len(hits),
        "event_recall": len(hits) / len(faulty) if faulty else None,
        "event_recall_wilson95": wilson(len(hits), len(faulty)),
        "median_delay_s": float(np.median(delays)) if delays else None,
        "p90_delay_s": float(np.percentile(delays, 90)) if delays else None,
        "false_alarm_episodes": false_total,
        "quiet_hours": quiet_hours,
        "false_alarms_per_hour": false_total / quiet_hours if quiet_hours else None,
        "false_alarms_per_hour_poisson95": poisson_rate_interval(false_total, quiet_hours)
        if quiet_hours
        else None,
        "false_alarms_during_benign_bursts": sum(r.burst_false_alarms for r in results),
        "step_precision": tp / (tp + fp) if tp + fp else None,
        "step_recall": tp / (tp + fn) if tp + fn else None,
        "step_f1": 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else None,
        "by_fault_type": by_type,
    }


def select_threshold(prepared: list[Prepared], scores: list[np.ndarray]) -> tuple[float, dict]:
    """The lowest threshold (most sensitive) whose validation false-alarm rate is within budget."""
    quiet_hours = sum((~p.fault).sum() * STEP_S for p in prepared) / 3600.0
    normal_scores = np.concatenate([s[~p.fault] for p, s in zip(prepared, scores, strict=True)])
    candidates = np.unique(np.quantile(normal_scores, np.linspace(0.5, 1.0, 400)))
    best = None
    for threshold in candidates:
        results = [score_run(p, s, threshold) for p, s in zip(prepared, scores, strict=True)]
        rate = sum(r.false_alarm_episodes for r in results) / quiet_hours
        if rate <= FALSE_ALARM_BUDGET_PER_HOUR:
            best = (float(threshold), rate)
            break
    if best is None:  # even the highest normal score alarms too often: alarm above everything seen
        best = (float(candidates[-1]) + 1e-9, 0.0)
    results = [score_run(p, s, best[0]) for p, s in zip(prepared, scores, strict=True)]
    return best[0], summarise(results)
