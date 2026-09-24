"""P07.09: independent outcome verification and rollback.

`classify` is a pure function (no I/O), so every classification rule is
directly unit-testable. "Independent" is structural, not a naming convention:
`verify_and_rollback` refuses to run when the verifier is the command's
requester, approver or executor (the same identity reasoning P07.05's
four-eyes rule applies), because a self-graded outcome is not verification.

Classification (`contracts/outcome/v1`), in this order:
1. **unknown** - either window has no real measurement. Never defaulted to
   "effective": missing evidence is reported as missing, the same discipline
   SAFE-02/SAFE-03 apply to commands that lack current evidence. It is
   escalated (`escalation_reason`), never left silently unresolved.
2. **unsafe** - the metric moved the wrong way by more than
   `safety_regression_threshold`. Triggers rollback through P05.08's own
   state machine (`executed` -> `rolled_back`), and where the action is
   physically reversible the real simulator is told to undo it and the result
   is read back before the command is called rolled back:
   - signal / diversion adapters: a live-session `undo` (cancel the remaining
     phase extension / reopen the closed lanes), confirmed from TraCI;
   - pre-emption / transit-priority: their corridor run already restored its
     own intersections when it finished (P07.07/P07.08), so nothing remains to
     reverse;
   - VMS: no simulator actuation exists (P07.06), so nothing to reverse.
   If a reversible action's undo is unavailable or cannot be confirmed, the
   command is **not** marked rolled back: it stays `executed` and the outcome
   is escalated. A rollback that did not verifiably happen is never claimed.
3. **effective** - the metric improved by more than
   `effectiveness_improvement_threshold`.
4. **ineffective** - neither: no rollback, no escalation; the action simply
   did not help.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.observability import traced  # noqa: E402
from backend.repositories.commands import get_command, transition_command  # noqa: E402
from backend.repositories.outcomes import executors_of, record_outcome  # noqa: E402
from backend.roles import require_verifier  # noqa: E402

SELF_REVERSIBLE_ADAPTERS = {"emergency_preemption_adapter", "transit_priority_adapter"}
NON_ACTUATING_ADAPTERS = {"vms_adapter"}


@dataclass(frozen=True)
class Metric:
    name: str
    value: float | None
    unit: str


@dataclass(frozen=True)
class ClassificationResult:
    classification: str  # effective | ineffective | unsafe | unknown
    reason: str
    delta: float | None


def classify(
    pre: Metric,
    post: Metric,
    safety_regression_threshold: float,
    effectiveness_improvement_threshold: float,
    higher_is_worse: bool = True,
) -> ClassificationResult:
    """`higher_is_worse=True` for a metric like delay or waiting time (lower is better); pass False for one
    like throughput (higher is better)."""
    if pre.value is None or post.value is None:
        return ClassificationResult(
            "unknown", f"missing measurement (pre={pre.value}, post={post.value})", None
        )
    raw_delta = post.value - pre.value
    worse_by = (
        raw_delta if higher_is_worse else -raw_delta
    )  # positive = worse, negative = better, either convention
    if worse_by > safety_regression_threshold:
        return ClassificationResult(
            "unsafe",
            f"{pre.name} worsened by {worse_by:.2f} {pre.unit} (> {safety_regression_threshold})",
            raw_delta,
        )
    if worse_by < -effectiveness_improvement_threshold:
        return ClassificationResult(
            "effective",
            f"{pre.name} improved by {-worse_by:.2f} {pre.unit} (> {effectiveness_improvement_threshold})",
            raw_delta,
        )
    return ClassificationResult(
        "ineffective",
        f"{pre.name} changed by {worse_by:.2f} {pre.unit}, within the noise band",
        raw_delta,
    )


def mean_corridor_metric(
    conn: psycopg.Connection,
    corridor_id: str,
    kpi_name: str,
    unit: str,
    window: tuple[datetime, datetime],
    direction: str | None = None,
) -> Metric:
    """The mean of one KPI from the platform's own `corridor_kpis` (P06.01) over a window. `None` when the
    window holds no rows - a real telemetry gap, which `classify` turns into `unknown`."""
    clauses, params = (
        ["corridor_id = %s", "window_start >= %s", "window_start < %s", "kpis ? %s"],
        [corridor_id, window[0], window[1], kpi_name],
    )
    if direction:
        clauses.append("direction = %s")
        params.append(direction)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT avg((kpis->>%s)::float8) FROM corridor_kpis WHERE {' AND '.join(clauses)}",
            [kpi_name, *params],
        )
        (value,) = cur.fetchone()
    return Metric(kpi_name, None if value is None else float(value), unit)


def verify_and_rollback(
    conn: psycopg.Connection,
    command_id: str,
    pre: Metric,
    post: Metric,
    pre_window: tuple[datetime, datetime],
    post_window: tuple[datetime, datetime],
    verifier: str,
    now: datetime,
    safety_regression_threshold: float,
    effectiveness_improvement_threshold: float,
    higher_is_worse: bool = True,
    undo: Callable[[dict], dict] | None = None,
    extra_detail: dict | None = None,
) -> tuple[str, ClassificationResult]:
    """`undo(command)` performs the physical reversal for an adapter whose action the simulator can
    reverse and returns at least `{"restored": bool}` read back from the simulator. `extra_detail` is
    stored beside the classification (e.g. the simulated-time bounds of each window)."""
    with traced("control.verify_and_rollback", correlation_id=command_id):
        return _verify_and_rollback(
            conn, command_id, pre, post, pre_window, post_window, verifier, now,
            safety_regression_threshold, effectiveness_improvement_threshold,
            higher_is_worse, undo, extra_detail,
        )  # fmt: skip


def _verify_and_rollback(
    conn: psycopg.Connection,
    command_id: str,
    pre: Metric,
    post: Metric,
    pre_window: tuple[datetime, datetime],
    post_window: tuple[datetime, datetime],
    verifier: str,
    now: datetime,
    safety_regression_threshold: float,
    effectiveness_improvement_threshold: float,
    higher_is_worse: bool = True,
    undo: Callable[[dict], dict] | None = None,
    extra_detail: dict | None = None,
) -> tuple[str, ClassificationResult]:
    require_verifier(verifier)
    command = get_command(conn, command_id)
    if command is None:
        raise ValueError(f"unknown command {command_id}")
    if verifier in {command["requested_by"], command["approved_by"]} | executors_of(
        conn, command_id
    ):
        raise ValueError(
            f"verifier {verifier!r} must be independent of the command's requester, approver and executor"
        )
    if command["status"] != "executed":
        raise ValueError(
            f"command {command_id} is {command['status']!r}, not 'executed' - nothing to verify yet"
        )

    result = classify(
        pre, post, safety_regression_threshold, effectiveness_improvement_threshold, higher_is_worse
    )
    rollback_triggered, escalation_reason, undo_detail = False, None, None
    if result.classification == "unsafe":
        adapter = command["target_adapter"]
        if adapter in NON_ACTUATING_ADAPTERS:
            undo_detail, confirmed = (
                {
                    "kind": "none",
                    "note": "the adapter has no simulator actuation, so there is nothing to reverse",
                },
                True,
            )
        elif adapter in SELF_REVERSIBLE_ADAPTERS:
            undo_detail, confirmed = (
                {
                    "kind": "self_restored",
                    "note": "the corridor run restored its own intersections at completion",
                },
                True,
            )
        elif undo is None:
            undo_detail, confirmed = (
                {"kind": "unavailable", "note": "no simulator undo was provided"},
                False,
            )
        else:
            undo_detail = {"kind": "simulator_undo", **undo(command)}
            confirmed = bool(undo_detail.get("restored"))
        if confirmed:
            transition_command(
                conn,
                command_id,
                "rolled_back",
                verifier,
                f"outcome verification: {result.reason}",
                at=now,
                rolled_back=True,
            )
            rollback_triggered = True
        else:
            escalation_reason = f"unsafe outcome but the rollback could not be confirmed ({undo_detail.get('note') or undo_detail.get('error') or 'undo not restored'})"
    elif result.classification == "unknown":
        escalation_reason = f"insufficient evidence to classify: {result.reason}"

    detail = {
        "metric": pre.name,
        "unit": pre.unit,
        "classification_reason": result.reason,
        "delta": result.delta,
        "higher_is_worse": higher_is_worse,
        "thresholds": {
            "safety_regression": safety_regression_threshold,
            "effectiveness_improvement": effectiveness_improvement_threshold,
        },
        "undo": undo_detail,
        **(extra_detail or {}),
    }
    outcome_id = record_outcome(
        conn,
        command_id,
        pre_window,
        post_window,
        [pre],
        [post],
        result.classification,
        verifier,
        now,
        rollback_triggered,
        escalation_reason,
        detail,
    )
    return outcome_id, result
