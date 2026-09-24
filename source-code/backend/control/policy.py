"""P07.05 / P09.03: execution-time policy for a command (`contracts/command/v1`).

This is the second independent safety check (P07.04's `enforce()` is the
first, at recommendation-generation time). `build_context` gathers the facts
(the policy *information* point: target validity, live evidence freshness, the
linked recommendation's current state, whether a critical incident is active
on the target) and the DECISION is made by Open Policy Agent
(`source-code/policy/aiops/command/command.rego`, asked through
`backend/pdp.py`), at approval time and again, by the executor, immediately
before the adapter is driven (`execution_gate`). Any failure to obtain a
decision - building the facts, reaching the engine, a malformed answer - is
caught and turned into `policy_unavailable`, never silently treated as
approved (SAFE-02: a protected action lacking current evidence, a valid policy
decision, or fresh state is denied, never executed).

`evaluate` below is the Python REFERENCE for the same rules: a **pure**
decision function over a `PolicyContext` snapshot - no I/O - that is no longer
consulted at run time. It stays because it is the oracle the differential test
(`policy/verify_policy.py`) compares the Rego against, over thousands of
generated contexts, so the policy the engine enforces cannot drift from the
policy that was reviewed, and because every denial reason is directly
unit-testable without an engine.

Roles and safety classes are the binding P02.08 model (`backend/roles.py`): the
safety class is derived from the action type, raised to SC-2 when an active
*critical* incident references the same target; SC-1 and SC-2 need a second
person; SC-0 does not.

Checked in order, first failure wins:
1. not expired (`expires_at`);
2. four-eyes (SC-1, SC-2) - the approver is not the requester;
3. the approver's role may approve this safety class;
4. the requester's role was allowed to request this safety class (skipped only
   for commands created before roles were recorded);
5. the target (`adapter` + `entity_id`) is a real, currently registered thing;
6. when a `recommendation_id` is given, it exists, matches this `action_type`,
   and has not expired/been superseded (bounds were already enforced when it
   was generated - P07.04's `enforce()` - so this re-confirms the command
   traces back to a bounds-checked recommendation, not a fabricated one);
7. the target's live evidence is fresh (SAFE-03: stale network-state must
   never be silently treated as current).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend import pdp  # noqa: E402
from backend.redaction import redact  # noqa: E402
from backend.analytics.correlation import Topology  # noqa: E402
from backend.analytics.kpi_service import load_segments  # noqa: E402
from backend.roles import (  # noqa: E402
    APPROVE_ROLES,
    EXECUTOR,
    FOUR_EYES_CLASSES,
    REQUEST_ROLES,
    safety_class,
)

LIVE_INCIDENT_STATUSES = ("open", "acknowledged", "investigating", "escalated", "reopened")
KNOWN_ADAPTERS = {
    "signal_controller_adapter",
    "diversion_adapter",
    "vms_adapter",
    "transit_priority_adapter",
    "emergency_preemption_adapter",
}
ADAPTER_TARGET_KIND = {
    "signal_controller_adapter": "intersection",
    "diversion_adapter": "segment",
    "vms_adapter": "segment",
    "transit_priority_adapter": "corridor",
    "emergency_preemption_adapter": "corridor",
}
FRESH_EVIDENCE_MAX_AGE_S = 900.0  # matches backend/routing/live_state.py's live budget


@dataclass(frozen=True)
class PolicyContext:
    """`phase` is "approval" (a reviewer is approving the command) or "execution" (the executor is about to drive the adapter). At
    execution the approver is the person and role recorded when the command was approved, and may be None for a command that was never
    approved. `safety_class` must equal what `critical_incident_on_target` and the action type derive (`context_to_input` checks)."""

    action_type: str
    target_adapter: str
    target_entity_id: str
    requested_by: str
    expires_at: datetime
    approver: str | None
    approver_role: str | None
    now: datetime
    target_exists: bool
    target_evidence_fresh: bool
    recommendation_id: str | None
    recommendation_action_type: (
        str | None
    )  # None if recommendation_id is None or the recommendation was not found
    recommendation_status: str | None
    safety_class: str = "SC-1"
    requester_role: str | None = None
    critical_incident_on_target: bool = False
    phase: str = "approval"
    actor: str | None = None
    command_status: str | None = None


@dataclass(frozen=True)
class PolicyResult:
    decision: str  # approved | denied | expired | policy_unavailable
    error_code: str | None = None
    message: str | None = None
    policy_version: str | None = None
    opa_decision_id: str | None = None

    def as_error_record(self) -> dict | None:
        if self.decision == "approved":
            return None
        retryable = self.error_code in ("stale_evidence", "adapter_unreachable")
        return {"error_code": self.error_code, "message": self.message, "retryable": retryable}


def evaluate(ctx: PolicyContext) -> PolicyResult:
    execution = ctx.phase == "execution"
    if execution:
        if ctx.actor != EXECUTOR:
            return PolicyResult(
                "denied",
                "policy_denied",
                f"{ctx.actor!r} may not execute a command: only {EXECUTOR!r} holds EXECUTE authority",
            )
        if ctx.command_status != "approved":
            return PolicyResult(
                "denied",
                "policy_denied",
                f"command is {ctx.command_status!r}, not 'approved' - refusing to execute",
            )
        if ctx.approver is None:
            return PolicyResult(
                "denied", "policy_denied", "no approval is recorded for this command"
            )
    if ctx.now >= ctx.expires_at:
        return PolicyResult(
            "expired", "expired", f"command expired at {ctx.expires_at.isoformat()}"
        )
    if ctx.safety_class in FOUR_EYES_CLASSES and ctx.approver == ctx.requested_by:
        return PolicyResult(
            "denied",
            "policy_denied",
            "the requester cannot also approve their own command (four-eyes required)",
        )
    if ctx.approver_role not in APPROVE_ROLES.get(ctx.safety_class, set()):
        return PolicyResult(
            "denied",
            "policy_denied",
            f"role {ctx.approver_role!r} may not approve a {ctx.safety_class} action ({ctx.action_type!r})",
        )
    if ctx.requester_role is not None and ctx.requester_role not in REQUEST_ROLES.get(
        ctx.safety_class, set()
    ):
        return PolicyResult(
            "denied",
            "policy_denied",
            f"role {ctx.requester_role!r} may not request a {ctx.safety_class} action ({ctx.action_type!r})",
        )
    if ctx.target_adapter not in KNOWN_ADAPTERS:
        return PolicyResult("denied", "invalid_target", f"unknown adapter {ctx.target_adapter!r}")
    if not ctx.target_exists:
        return PolicyResult(
            "denied",
            "invalid_target",
            f"{ctx.target_entity_id!r} is not a real, currently registered target for {ctx.target_adapter!r}",
        )
    if ctx.recommendation_id is not None and not execution:
        if ctx.recommendation_status is None:
            return PolicyResult(
                "denied", "invalid_target", f"recommendation {ctx.recommendation_id} does not exist"
            )
        if ctx.recommendation_action_type != ctx.action_type:
            return PolicyResult(
                "denied",
                "policy_denied",
                "command action_type does not match its recommendation's action_type",
            )
        if ctx.recommendation_status not in ("proposed", "requested"):
            return PolicyResult(
                "denied",
                "policy_denied",
                f"recommendation is {ctx.recommendation_status!r}, no longer actionable",
            )
    if not ctx.target_evidence_fresh:
        return PolicyResult(
            "denied",
            "stale_evidence",
            f"live evidence for {ctx.target_entity_id!r} is not fresh (SAFE-03)",
        )
    return PolicyResult("approved")


def _target_exists(
    conn: psycopg.Connection, adapter: str, entity_id: str, geometry: str, topo: Topology
) -> bool:
    kind = ADAPTER_TARGET_KIND.get(adapter)
    if kind is None:
        return False
    if kind == "segment":
        return entity_id in topo.segments
    if kind == "intersection":
        return entity_id in topo.junction
    if kind == "corridor":
        return entity_id in topo.corridor
    return False


def _evidence_fresh(
    conn: psycopg.Connection,
    adapter: str,
    entity_id: str,
    geometry: str,
    topo: Topology,
    now: datetime,
) -> bool:
    """A signal target needs recent device telemetry; a segment target (diversion, VMS) needs a
    recent corridor KPI window for its own segment's corridor; a corridor target (transit priority,
    emergency pre-emption - entity_id is already a corridor_id) needs one for itself directly -
    the same live-evidence sources backend/routing/live_state.py already uses to decide what is
    "live", reused here rather than a second definition of freshness."""
    cutoff = now - timedelta(seconds=FRESH_EVIDENCE_MAX_AGE_S)
    kind = ADAPTER_TARGET_KIND.get(adapter)
    with conn.cursor() as cur:
        if kind == "segment":
            seg = topo.segments.get(entity_id)
            if seg is None:
                return False
            corridor_id = seg.corridor_id
            if corridor_id is None:
                return True  # a cross-street segment has no corridor KPI; freshness is not applicable, not denied
            cur.execute(
                "SELECT 1 FROM corridor_kpis WHERE corridor_id = %s AND direction = %s AND geometry_version = %s AND window_start > %s LIMIT 1",
                (corridor_id, seg.direction, geometry, cutoff),
            )
            return cur.fetchone() is not None
        if kind == "corridor":
            cur.execute(
                "SELECT 1 FROM corridor_kpis WHERE corridor_id = %s AND geometry_version = %s AND window_start > %s LIMIT 1",
                (entity_id, geometry, cutoff),
            )
            return cur.fetchone() is not None
        cur.execute(
            "SELECT 1 FROM observation_events e JOIN devices d ON d.device_id = e.device_id "
            "WHERE (d.intersection_id = %s OR d.corridor_id = %s) AND e.observation_time > %s LIMIT 1",
            (entity_id, entity_id, cutoff),
        )
        return cur.fetchone() is not None


def critical_incident_on_target(
    conn: psycopg.Connection, adapter: str, entity_id: str, geometry: str, topo: Topology
) -> bool:
    """P02.08: any action during an active incident with severity `critical` that references the same target is SC-2."""
    kind = ADAPTER_TARGET_KIND.get(adapter)
    if kind is None:
        return False
    target = topo.segments_of(kind, entity_id)
    if not target:
        return False
    with conn.cursor() as cur:
        cur.execute(
            "SELECT network_element_type, network_element_id FROM incidents WHERE severity = 'critical' AND status = ANY(%s) "
            "AND duplicate_of IS NULL AND geometry_version = %s",
            (list(LIVE_INCIDENT_STATUSES), geometry),
        )
        return any(target & topo.segments_of(etype, eid) for etype, eid in cur.fetchall())


def derive_safety_class(
    conn: psycopg.Connection, action_type: str, adapter: str, entity_id: str, geometry: str
) -> str:
    topo = Topology(load_segments(conn, geometry), 3)
    return safety_class(
        action_type, critical_incident_on_target(conn, adapter, entity_id, geometry, topo)
    )


def build_context(
    conn: psycopg.Connection,
    command: dict,
    approver: str | None,
    approver_role: str | None,
    now: datetime,
    geometry: str,
    phase: str = "approval",
    actor: str | None = None,
) -> PolicyContext:
    """The one I/O boundary for the facts. Any failure here should reach the caller as a policy outage, not a crash -
    see `evaluate_command` below, which is what actually wraps this in try/except."""
    segments = load_segments(conn, geometry)
    topo = Topology(segments, 3)
    target_exists = command["target_adapter"] in KNOWN_ADAPTERS and _target_exists(
        conn, command["target_adapter"], command["target_entity_id"], geometry, topo
    )
    fresh = target_exists and _evidence_fresh(
        conn, command["target_adapter"], command["target_entity_id"], geometry, topo, now
    )
    rec_action_type = rec_status = None
    if command["recommendation_id"] is not None and phase == "approval":
        with conn.cursor() as cur:
            cur.execute(
                "SELECT action_type, status FROM recommendations WHERE recommendation_id = %s",
                (command["recommendation_id"],),
            )
            row = cur.fetchone()
        if row is not None:
            rec_action_type, rec_status = row
    critical = critical_incident_on_target(
        conn, command["target_adapter"], command["target_entity_id"], geometry, topo
    )
    return PolicyContext(
        command["action_type"],
        command["target_adapter"],
        command["target_entity_id"],
        command["requested_by"],
        command["expires_at"],
        approver,
        approver_role,
        now,
        target_exists,
        fresh,
        command["recommendation_id"] if phase == "approval" else None,
        rec_action_type,
        rec_status,
        safety_class(command["action_type"], critical),
        command.get("requested_by_role"),
        critical,
        phase,
        actor,
        command["status"] if phase == "execution" else None,
    )


def current_geometry(conn: psycopg.Connection) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT version FROM geometry_versions WHERE superseded_by IS NULL ORDER BY effective_from DESC LIMIT 1"
        )
        row = cur.fetchone()
    return row[0] if row else None


def context_to_input(ctx: PolicyContext) -> dict:
    """The facts, in the shape `aiops/command/command.rego` reads. The safety class is not sent: the policy derives it from the action
    type and the critical-incident flag, so a context whose `safety_class` disagrees with them is a programming error, not an input."""
    if ctx.safety_class != safety_class(ctx.action_type, ctx.critical_incident_on_target):
        raise ValueError(
            "the context's safety class does not follow from its action type and incident flag"
        )
    facts: dict = {
        "phase": ctx.phase,
        "action_type": ctx.action_type,
        "critical_incident_on_target": ctx.critical_incident_on_target,
        "now": ctx.now.isoformat(),
        "expires_at": ctx.expires_at.isoformat(),
        "target": {
            "adapter": ctx.target_adapter,
            "entity_id": ctx.target_entity_id,
            "exists": ctx.target_exists,
            "evidence_fresh": ctx.target_evidence_fresh,
        },
        "requester": {"id": ctx.requested_by, "role": ctx.requester_role},
        "approver": {"id": ctx.approver, "role": ctx.approver_role},
        "recommendation": None
        if ctx.recommendation_id is None
        else {
            "id": str(ctx.recommendation_id),
            "action_type": ctx.recommendation_action_type,
            "status": ctx.recommendation_status,
        },
    }
    if ctx.phase == "execution":
        facts["actor"] = ctx.actor
        facts["command_status"] = ctx.command_status
    return facts


def record_decision(
    conn: psycopg.Connection,
    point: str,
    entity_type: str,
    entity_id: str | None,
    policy_package: str,
    decision: str,
    reason: str | None,
    facts: dict,
    result: dict | None,
) -> None:
    """Append one decision to `policy_decisions` (`contracts/policy-decision/v1`): what was asked, what the engine answered, under
    which policy version. Best effort by design - the caller already has its decision, and a recording failure must not turn a
    permitted or refused action into a different one - but a failure is loud on stderr."""
    try:
        with (
            conn.transaction(),
            conn.cursor() as cur,
        ):  # a savepoint: a failed insert must not abort the caller's transaction
            cur.execute(
                "INSERT INTO policy_decisions (opa_decision_id, point, entity_type, entity_id, policy_package, policy_version, "
                "decision, reason, input) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    (result or {}).get("decision_id"),
                    point,
                    entity_type,
                    entity_id,
                    policy_package,
                    (result or {}).get("policy_version"),
                    decision,
                    reason,
                    Jsonb(redact(facts, mode="audit")),
                ),
            )
    except psycopg.Error as exc:
        print(
            f"could not record the policy decision ({exc.__class__.__name__}): {exc}",
            file=sys.stderr,
        )


def _ask_engine(
    conn: psycopg.Connection, ctx: PolicyContext, point: str, command_id: str | None
) -> PolicyResult:
    facts = context_to_input(ctx)
    try:
        answer = pdp.command_decision(facts)
    except pdp.PolicyUnavailable as exc:
        record_decision(
            conn,
            point,
            "command",
            command_id,
            "aiops.command",
            "unavailable",
            str(exc),
            facts,
            None,
        )
        return PolicyResult("policy_unavailable", "internal", f"policy evaluation failed: {exc}")
    record_decision(
        conn,
        point,
        "command",
        command_id,
        "aiops.command",
        answer["decision"],
        answer.get("message"),
        facts,
        answer,
    )
    return PolicyResult(
        answer["decision"],
        answer.get("error_code"),
        answer.get("message"),
        answer.get("policy_version"),
        answer.get("decision_id"),
    )


def evaluate_command(
    conn: psycopg.Connection,
    command: dict,
    approver: str,
    approver_role: str,
    now: datetime | None,
    geometry: str,
) -> PolicyResult:
    now = now or datetime.now(timezone.utc)
    try:
        ctx = build_context(conn, command, approver, approver_role, now, geometry)
        return _ask_engine(conn, ctx, "command_approval", str(command["command_id"]))
    except Exception as exc:  # noqa: BLE001 - a policy outage must fail closed regardless of *why* evaluation broke
        return PolicyResult("policy_unavailable", "internal", f"policy evaluation failed: {exc}")


def execution_gate(
    conn: psycopg.Connection, command: dict, now: datetime | None, geometry: str
) -> PolicyResult:
    """The executor's own check, made against fresh facts immediately before the adapter is driven. The approval is not taken on trust:
    the engine re-derives that the recorded approver was a different person in a role that could approve this class, that the command has
    not expired, that the target still exists and its evidence is still fresh, and that the caller is the one identity with EXECUTE."""
    now = now or datetime.now(timezone.utc)
    try:
        ctx = build_context(
            conn,
            command,
            command.get("approved_by"),
            command.get("approved_by_role"),
            now,
            geometry,
            phase="execution",
            actor=EXECUTOR,
        )
        return _ask_engine(conn, ctx, "command_execution", str(command["command_id"]))
    except Exception as exc:  # noqa: BLE001 - fail closed: a gate that cannot decide does not open
        return PolicyResult("policy_unavailable", "internal", f"policy evaluation failed: {exc}")


def authorise(
    conn: psycopg.Connection,
    kind: str,
    action_type: str,
    adapter: str,
    entity_id: str,
    geometry: str,
    roles: list[str],
    actor: str,
    record_as: tuple[str, str | None],
) -> dict:
    """May one of `roles` request (kind "request") or review (kind "review") this action? The engine answers, naming the role that holds
    the authority and the derived safety class. Raises `pdp.PolicyUnavailable` when it cannot answer - the caller refuses with 503.
    `record_as` is what the decision record is filed under: a request has no command yet, so it is filed under what was asked about."""
    topo = Topology(load_segments(conn, geometry), 3)
    critical = critical_incident_on_target(conn, adapter, entity_id, geometry, topo)
    facts = {
        "kind": kind,
        "action_type": action_type,
        "critical_incident_on_target": critical,
        "roles": sorted(roles),
    }
    try:
        answer = pdp.command_authority(kind, action_type, critical, sorted(roles))
    except pdp.PolicyUnavailable as exc:
        record_decision(
            conn,
            f"command_{kind}",
            record_as[0],
            record_as[1],
            "aiops.command",
            "unavailable",
            str(exc),
            {**facts, "actor": actor},
            None,
        )
        raise
    record_decision(
        conn,
        f"command_{kind}",
        record_as[0],
        record_as[1],
        "aiops.command",
        "permit" if answer["allow"] else "denied",
        answer["reason"],
        {**facts, "actor": actor, "target": {"adapter": adapter, "entity_id": entity_id}},
        answer,
    )
    return answer
