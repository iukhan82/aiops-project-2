"""P10.08: the remediation worker - takes a live platform incident to a bounded action, or to a person.

    python source-code/backend/aiops/remediation_worker.py --once
    python source-code/backend/aiops/remediation_worker.py --interval 15

Each cycle, in this order (`Worker.cycle`):

1. Housekeeping. A request nobody approved in time expires; a request stuck `executing` after its adapter's timeout belongs
   to a worker that died, so it is closed `abandoned` (outcome unknown, counted as an attempt).
2. Judge what was executed. A request whose adapter finished is `verified` only when the alerts that motivated it have
   cleared AND stayed clear for the action's sustain time, read from Prometheus - not from the adapter's return code. If they
   do not clear within the verification timeout, or Prometheus cannot be read, the request is `failed`; nothing is ever
   assumed recovered.
3. Act on incidents. For each live incident nobody owns, the playbook's candidate actions are put to the policy engine in
   order. `approved` runs the action now; `needs_approval` records the request and waits for a person; `plan_only` records the
   plan and escalates; `denied` records why (once) and, when the reason is a spent budget, moves on to the next candidate. When
   every candidate is permanently spent and the incident is still live, the incident is ESCALATED to a person - the worker
   never leaves an unresolved incident silently unattended.

Fail closed: if the policy engine cannot answer, the cycle does nothing (`policy_unavailable`). One remediation runs at a
time, and the database enforces it. A person who acknowledges or takes over an incident stops the automation on it.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend import pdp  # noqa: E402
from backend.aiops import platform_signals, remediation  # noqa: E402
from backend.aiops.correlation import ALERT_MODEL  # noqa: E402
from backend.aiops.remediation import ACTIONS, PLATFORM_ACTOR, Candidate  # noqa: E402
from backend.aiops.remediation_adapters import NotExecutable, default_adapters  # noqa: E402
from backend.repositories import remediation as repo  # noqa: E402

ALERT_STEP_S = platform_signals.ALERT_STEP_S
# Reasons after which the next playbook candidate may still be tried; anything else ends the attempt for this incident.
NEXT_CANDIDATE_REASONS = {
    "action_attempts_spent",
    "rate_limited",
    "cooldown",
    "target_not_registered",
}
OPERATIONAL_EFFECTS = {"unavailable", "data_loss", "degraded"}
PERMANENT_REASONS = {"action_attempts_spent", "incident_attempt_budget_spent"}


def alert_activity(
    series: list[dict], names: set[str], since: datetime, now: datetime
) -> tuple[datetime | None, bool]:
    """When one of the alerts `names` was last pending or firing at or after `since`, and whether it is active now."""
    stamps = [
        ts
        for s in series
        if s["metric"].get("alertname") in names
        for ts, _ in s["values"]
        if since.timestamp() - ALERT_STEP_S <= ts <= now.timestamp()
    ]
    if not stamps:
        return None, False
    last = max(stamps)
    return datetime.fromtimestamp(last, tz=UTC), now.timestamp() - last <= 2 * ALERT_STEP_S


class Worker:
    def __init__(
        self,
        conn: psycopg.Connection,
        adapters: dict | None = None,
        decide=pdp.remediation_decision,  # noqa: ANN001
        fetch_alerts=platform_signals.fetch_alert_series,  # noqa: ANN001
        log=print,  # noqa: ANN001
        clock=None,  # noqa: ANN001
    ) -> None:
        self.conn = conn
        self.clock = clock  # None: times come from the cycle's `now` (replays and tests); else the real clock
        self.adapters = adapters if adapters is not None else default_adapters()
        self.decide = decide
        self.fetch_alerts = fetch_alerts
        self.log = log

    # ------------------------------------------------------------------------------------------------ one cycle
    def cycle(self, now: datetime | None = None) -> Counter:
        now = now or datetime.now(UTC)
        counts: Counter = Counter()
        counts["expired"] += repo.expire_stale(self.conn, now)
        counts["abandoned"] += repo.abandon_interrupted(
            self.conn, now, {a.id: a.execute_timeout_s for a in ACTIONS.values()}
        )
        self._verify_executed(now, counts)
        self._run_approved(now, counts)
        try:
            self._act_on_incidents(now, counts)
        except pdp.PolicyUnavailable as exc:
            counts["policy_unavailable"] += 1
            self.log(f"policy unavailable, nothing was done this cycle: {exc}")
        return counts

    # ------------------------------------------------------------------------------------------------ verification
    def _verify_executed(self, now: datetime, counts: Counter) -> None:
        for req in repo.in_status(self.conn, "executed"):
            spec = ACTIONS[req["action_id"]]
            names = {k.split("{")[0] for k in req["motivating_signals"]} | self._component_alerts(
                req
            )
            executed_at = req["executed_at"]
            try:
                series = self.fetch_alerts(executed_at - timedelta(seconds=ALERT_STEP_S), now)
            except Exception as exc:  # noqa: BLE001 - any failure to read means "cannot confirm"
                series, unreadable = [], str(exc)[:200]
            else:
                unreadable = None
            elapsed = (now - executed_at).total_seconds()
            if unreadable is not None:
                if elapsed >= spec.verify_timeout_s:
                    self._fail(
                        req,
                        spec,
                        now,
                        {
                            "reason": "prometheus could not be read, so recovery was never confirmed",
                            "error": unreadable,
                        },
                        counts,
                    )
                continue
            last_active, active_now = alert_activity(series, names, executed_at, now)
            healthy_since = (
                executed_at
                if last_active is None
                else max(executed_at, last_active + timedelta(seconds=ALERT_STEP_S))
            )
            healthy_for = (now - healthy_since).total_seconds()
            evidence = {
                "alerts": sorted(names),
                "still_active": active_now,
                "last_active_at": last_active.isoformat() if last_active else None,
                "healthy_for_s": round(max(0.0, healthy_for)),
                "required_s": spec.verify_sustain_s,
                "checked_at": now.isoformat(),
            }
            if not active_now and healthy_for >= spec.verify_sustain_s:
                repo.transition(
                    self.conn,
                    req["remediation_id"],
                    "verified",
                    actor=PLATFORM_ACTOR,
                    now=now,
                    detail=evidence,
                    verification={**evidence, "verdict": "recovered"},
                )
                counts["verified"] += 1
                self.log(
                    f"verified: {req['action_id']} on {req['target']} - {sorted(names)} clear for {round(healthy_for)} s"
                )
            elif elapsed >= spec.verify_timeout_s:
                self._fail(
                    req,
                    spec,
                    now,
                    {
                        **evidence,
                        "reason": "the alerts did not clear and stay clear within the verification timeout",
                    },
                    counts,
                )

    def _component_alerts(self, req: dict) -> set[str]:
        """Every operational alert about the incident's component, not only the ones that had fired when the action was chosen:
        an alert that needed a moment longer to fire (a `for:` delay) is still the same fault, and a restart that only silenced the
        first symptom has not recovered anything."""
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT incident_key FROM platform_incidents WHERE platform_incident_id = %s",
                (req["platform_incident_id"],),
            )
            row = cur.fetchone()
        if row is None:
            return set()
        return {
            name
            for name, model in ALERT_MODEL.items()
            if model.component == row["incident_key"] and model.effect in OPERATIONAL_EFFECTS
        }

    def _fail(self, req: dict, spec, now: datetime, evidence: dict, counts: Counter) -> None:  # noqa: ANN001
        repo.transition(
            self.conn,
            req["remediation_id"],
            "failed",
            actor=PLATFORM_ACTOR,
            now=now,
            detail=evidence,
            verification={**evidence, "verdict": "not recovered"},
        )
        counts["failed"] += 1
        self.log(f"failed: {req['action_id']} on {req['target']} - {evidence.get('reason')}")
        adapter = self.adapters.get(spec.adapter)
        if adapter is not None and hasattr(adapter, "undo"):
            adapter.undo(req["target"], req.get("result") or {})
            counts["undone"] += 1

    # ------------------------------------------------------------------------------------------------ execution
    def _run_approved(self, now: datetime, counts: Counter) -> None:
        for req in repo.in_status(self.conn, "approved"):
            self._execute(req, now, counts)

    def _facts(
        self,
        incident: dict,
        spec,
        target: str,
        params: dict,
        now: datetime,
        phase: str,
        approval: dict | None,
    ) -> dict:  # noqa: ANN001
        return {
            "phase": phase,
            "actor": PLATFORM_ACTOR,
            "action": {"id": spec.id, "target": target, "params": params},
            "incident": {"status": incident["status"]},
            "history": repo.history_facts(
                self.conn, incident["platform_incident_id"], spec.id, target, now, spec.window_s
            ),
            "approval": approval,
        }

    def _execute(self, req: dict, now: datetime, counts: Counter) -> None:
        spec = ACTIONS[req["action_id"]]
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM platform_incidents WHERE platform_incident_id = %s",
                (req["platform_incident_id"],),
            )
            incident = cur.fetchone()
        approval = (
            {"by": req["approved_by"], "role": req["approver_role"]} if req["approved_by"] else None
        )
        decision = self.decide(
            self._facts(incident, spec, req["target"], req["params"], now, "execute", approval)
        )
        if decision["decision"] != "approved":
            repo.transition(
                self.conn,
                req["remediation_id"],
                "expired",
                actor=PLATFORM_ACTOR,
                now=now,
                detail={
                    "reason": "the policy no longer permits it",
                    "policy": decision.get("reason"),
                },
            )
            counts["withdrawn"] += 1
            self.log(
                f"withdrawn before running: {req['action_id']} on {req['target']} - {decision.get('reason')}"
            )
            return
        try:
            repo.transition(
                self.conn,
                req["remediation_id"],
                "executing",
                actor=PLATFORM_ACTOR,
                now=now,
                detail={"policy_version": decision.get("policy_version")},
                started_at=now,
            )
        except repo.Busy:
            counts["busy"] += 1
            return
        adapter = self.adapters[spec.adapter]
        try:
            outcome = adapter.execute(
                req["target"], req["params"], timeout_s=spec.execute_timeout_s
            )
        except NotExecutable:
            outcome = None
        except Exception as exc:  # noqa: BLE001 - an adapter crash is a failed attempt, never a crashed worker
            outcome = None
            crashed = f"{type(exc).__name__}: {str(exc)[:200]}"
        else:
            crashed = None
        finished = self.clock() if self.clock else now
        if outcome is not None and outcome.ok:
            repo.transition(
                self.conn,
                req["remediation_id"],
                "executed",
                actor=PLATFORM_ACTOR,
                now=finished,
                detail=outcome.detail,
                executed_at=finished,
                result=outcome.detail,
            )
            counts["executed"] += 1
            self.log(f"executed: {req['action_id']} on {req['target']} (recovery not yet judged)")
        else:
            detail = (
                outcome.detail if outcome is not None else {"error": crashed or "not executable"}
            )
            repo.transition(
                self.conn,
                req["remediation_id"],
                "failed",
                actor=PLATFORM_ACTOR,
                now=finished,
                detail=detail,
                result=detail,
            )
            counts["failed"] += 1
            self.log(
                f"failed to run: {req['action_id']} on {req['target']} - {detail.get('error')}"
            )
            if hasattr(adapter, "undo"):
                adapter.undo(req["target"], detail)

    # ------------------------------------------------------------------------------------------------ deciding
    def _live_incidents(self) -> list[dict]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM platform_incidents WHERE status IN ('open', 'reopened') "
                "ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, opened_at"
            )
            return cur.fetchall()

    def _active_signals(self, incident_id: uuid.UUID) -> list[dict]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT signal_key, signal FROM platform_incident_signals WHERE platform_incident_id = %s AND active",
                (incident_id,),
            )
            return cur.fetchall()

    def _act_on_incidents(self, now: datetime, counts: Counter) -> None:
        for incident in self._live_incidents():
            history = repo.for_incident(self.conn, incident["platform_incident_id"])
            if any(
                r["status"] in ("awaiting_approval", "approved", "executing", "executed")
                for r in history
            ):
                continue  # one thing at a time per incident: wait for it to be approved, run or judged
            if any(
                r["status"] == "verified"
                and (now - r["finished_at"]).total_seconds() < remediation.RECOVERY_GRACE_S
                for r in history
            ):
                continue  # recovery was just confirmed; the correlator needs a moment to see it too
            active = self._active_signals(incident["platform_incident_id"])
            if not active:
                continue  # everything has cleared; the correlator will resolve the incident
            age = (now - incident["opened_at"]).total_seconds()
            if age >= remediation.ESCALATE_AFTER_S:
                self._escalate(
                    incident,
                    now,
                    f"live for {round(age / 60)} minutes (it may have flapped); automation has done all its bounds allow",
                    counts,
                )
                continue
            cands = remediation.candidates(incident["incident_key"], active)
            if not cands:
                waited = (now - incident["opened_at"]).total_seconds()
                if (
                    incident["severity"] in ("high", "critical")
                    and waited >= remediation.UNREMEDIABLE_ESCALATION_S
                ):
                    self._escalate(
                        incident,
                        now,
                        "no remediation is registered for this incident: a person must look at it",
                        counts,
                    )
                continue
            handled, exhausted = self._try_candidates(incident, cands, now, counts)
            if not handled and exhausted:
                self._escalate(
                    incident,
                    now,
                    "every registered remediation for this incident has been tried or is exhausted",
                    counts,
                )

    def _try_candidates(
        self, incident: dict, cands: list[Candidate], now: datetime, counts: Counter
    ) -> tuple[bool, bool]:
        """(an action was requested or planned, the incident is out of remediation options for good)."""
        spent = 0
        for cand in cands:
            facts = self._facts(
                incident, cand.spec, cand.rule.target, dict(cand.rule.params), now, "request", None
            )
            decision = self.decide(facts)
            outcome, reason = decision["decision"], decision.get("reason")
            history = facts["history"]
            if outcome == "denied":
                self._record_denial(incident, cand, decision, now, counts)
                if reason == "incident_attempt_budget_spent":
                    return False, True
                if reason == "action_attempts_spent":
                    spent += 1
                if reason in NEXT_CANDIDATE_REASONS:
                    continue
                return (
                    False,
                    False,
                )  # busy, held by a person, malformed, ...: stop for this incident
            attempt = history["attempts_for_action"] + 1
            key = f"{incident['platform_incident_id']}:{cand.spec.id}:{cand.rule.target}:{history['requests_for_action'] + 1}"
            if outcome == "plan_only":
                self._plan(incident, cand, decision, now, counts)
                return True, False
            status = "approved" if outcome == "approved" else "awaiting_approval"
            try:
                row = repo.create_request(
                    self.conn, incident_id=incident["platform_incident_id"], spec=cand.spec, target=cand.rule.target,
                    autonomy=decision["autonomy"], params=dict(cand.rule.params), status=status, attempt_no=attempt, key=key,
                    motivating=cand.motivating, decision=decision, now=now,
                )  # fmt: skip
            except repo.Busy:
                counts["busy"] += 1
                return True, False
            if row is None:
                return True, False  # this exact request was already recorded
            counts["requested" if status == "approved" else "awaiting_approval"] += 1
            self.log(
                f"{status}: {cand.spec.id} on {cand.rule.target} for {incident['incident_key']} ({cand.rule.why})"
            )
            if status == "approved":
                self._execute(row, now, counts)
            return True, False
        return False, spent == len(cands)

    def _record_denial(
        self, incident: dict, cand: Candidate, decision: dict, now: datetime, counts: Counter
    ) -> None:
        key = f"deny:{incident['platform_incident_id']}:{cand.spec.id}:{cand.rule.target}:{decision.get('reason')}"
        row = repo.create_request(
            self.conn, incident_id=incident["platform_incident_id"], spec=cand.spec, target=cand.rule.target,
            autonomy=cand.target.autonomy, params=dict(cand.rule.params), status="denied", attempt_no=0, key=key,
            motivating=cand.motivating, decision=decision, now=now,
        )  # fmt: skip
        if row is not None:
            counts["denied"] += 1
            self.log(f"denied: {cand.spec.id} on {cand.rule.target} - {decision.get('reason')}")

    def _plan(
        self, incident: dict, cand: Candidate, decision: dict, now: datetime, counts: Counter
    ) -> None:
        plan = self.adapters[cand.spec.adapter].plan(cand.rule.target, dict(cand.rule.params))
        key = f"plan:{incident['platform_incident_id']}:{cand.spec.id}:{cand.rule.target}"
        row = repo.create_request(
            self.conn, incident_id=incident["platform_incident_id"], spec=cand.spec, target=cand.rule.target,
            autonomy="plan_only", params=dict(cand.rule.params), status="planned", attempt_no=0, key=key,
            motivating=cand.motivating, decision={**decision, "plan": plan}, now=now,
        )  # fmt: skip
        if row is not None:
            counts["planned"] += 1
            self.log(f"planned only: {cand.spec.id} on {cand.rule.target} - escalating to a person")
            self._escalate(
                incident,
                now,
                f"{cand.spec.id} on {cand.rule.target} is plan-only: a person must carry it out",
                counts,
                plan=plan,
            )

    # ------------------------------------------------------------------------------------------------ escalation
    def _escalate(
        self, incident: dict, now: datetime, reason: str, counts: Counter, **extra
    ) -> None:
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute(
                "UPDATE platform_incidents SET status = 'escalated', updated_at = %s "
                "WHERE platform_incident_id = %s AND status IN ('open', 'reopened')",
                (max(now, incident["opened_at"]), incident["platform_incident_id"]),
            )
            if cur.rowcount != 1:
                return
            cur.execute(
                "INSERT INTO platform_incident_events (platform_incident_id, at, event, actor, detail) VALUES (%s, %s, 'escalated', %s, %s)",
                (
                    incident["platform_incident_id"],
                    now,
                    PLATFORM_ACTOR,
                    Jsonb({"reason": reason, **extra}),
                ),
            )
        counts["escalated"] += 1
        self.log(f"escalated to a person: {incident['incident_key']} - {reason}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--database", default=os.environ.get("POSTGRES_DB", "aiops"))
    args = parser.parse_args()

    from backend.demo import world

    world.load_platform_env()
    os.environ["POSTGRES_DB"] = args.database
    from database.migrate import dsn_from_env

    with psycopg.connect(dsn_from_env(role="svc_remediation_worker"), autocommit=True) as conn:
        worker = Worker(conn, clock=lambda: datetime.now(UTC))
        while True:
            counts = worker.cycle()
            print(f"{datetime.now(UTC):%H:%M:%S} {dict(counts) or 'nothing to do'}", flush=True)
            if args.once:
                return 0
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
