"""P10.08: the registry of remediation actions the AIOps worker may take, and the playbook that picks one for an incident.

Nothing outside this registry can be executed: an action that is not registered, a target that is not listed for it, or a
parameter outside its bounds is refused by the policy engine (`policy/aiops/remediation`), whose data document is generated
from THIS file (`policy/build_data.py`), so the two cannot drift.

Every action is bounded on six axes, all recorded here and all enforced by the policy decision, not by the worker's good
manners:

- WHICH targets: an explicit list (or, for devices, a pattern), each with an autonomy level:
    `auto`       the worker may execute once the policy approves;
    `approval`   the policy answers `needs_approval`; a person approves; only then does the worker execute;
    `plan_only`  never executed by software - the worker records the plan and escalates to a person.
- HOW MUCH: numeric parameters carry hard bounds (a sampling ratio, a replica count, an override lifetime).
- HOW OFTEN: `max_attempts_per_incident`, a `cooldown_s` between attempts on one target, and `max_per_window` attempts on
  one target per `window_s`.
- HOW MANY AT ONCE: one remediation in flight across the whole platform (`MAX_IN_FLIGHT`).
- HOW LONG: `execute_timeout_s`; a worker that dies mid-action leaves the request `abandoned`, counted as an attempt.
- HOW IT IS JUDGED: recovery is verified independently of the adapter (see `remediation_worker.py`): the alerts that
  motivated the action must clear and stay clear for `verify_sustain_s`, or the attempt is `failed`.

The policy engine is deliberately NOT a target: a worker whose every decision depends on it cannot repair it (with the
engine down every decision fails closed), so its restart is left to Docker's `unless-stopped` policy and, failing that, to a
person - an incident about it is escalated by the worker like any other with no registered remediation.

Only platform actions live here. No entry may name a traffic or emergency action type (a test enforces it), and the actor
is the service identity `system:aiops-remediation`, which the action-authority document limits to platform remediation.

`failover_service`, `scale_service` and `quarantine_device` are registered and bounded but `plan_only`: this repository
runs on one host, so there is no replica to fail over to or scale, and device quarantine needs an enforcement point in
ingestion that does not exist yet. They are documented gaps, not silent no-ops - the worker records the plan and escalates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PLATFORM_ACTOR = "system:aiops-remediation"
KINDS = ("restart", "failover", "quarantine", "rollback", "sampling", "scale")
AUTONOMY = ("auto", "approval", "plan_only")
MAX_IN_FLIGHT = 1
MAX_ATTEMPTS_PER_INCIDENT = 4
APPROVAL_TTL_S = 900  # a request nobody approves within this long expires
ESCALATE_AFTER_S = 900  # an incident still live this long after it opened is handed to a person whatever automation has done
RECOVERY_GRACE_S = 300  # after a verified recovery the incident gets this long to clear before anything else is tried
UNREMEDIABLE_ESCALATION_S = 300  # a high or critical incident with no registered remediation is handed to a person after this
APPROVER_ROLES = ("operator", "supervisor")  # human roles that may approve an `approval` target
HELD_BY_PERSON = ("acknowledged", "escalated")  # incident statuses on which automation stands down


@dataclass(frozen=True)
class Target:
    id: str  # an exact target id, or a regular expression when `pattern`
    autonomy: str
    pattern: bool = False
    note: str = ""


@dataclass(frozen=True)
class ActionSpec:
    id: str
    kind: str
    adapter: str
    description: str
    targets: tuple[Target, ...]
    params: dict[str, tuple[float, float]] = field(
        default_factory=dict
    )  # name -> (min, max), inclusive
    max_attempts_per_incident: int = 1
    cooldown_s: int = 300
    window_s: int = 3600
    max_per_window: int = 3
    verify_sustain_s: int = 120
    verify_timeout_s: int = 600
    execute_timeout_s: int = 60
    reversible: bool = False

    def target(self, target_id: str) -> Target | None:
        import re

        for t in self.targets:
            if (t.pattern and re.fullmatch(t.id, target_id)) or t.id == target_id:
                return t
        return None


ACTIONS: dict[str, ActionSpec] = {
    a.id: a
    for a in (
        ActionSpec(
            id="restart_container",
            kind="restart",
            adapter="docker",
            description="Restart one named platform container and wait for it to report running.",
            targets=(
                Target("aiops-edge-runtime", "auto", note="stateless replay runtime"),
                Target("aiops-mqtt-broker", "approval", note="device connectivity"),
                Target("aiops-postgres", "approval", note="stateful: a person decides"),
                Target("aiops-kafka-broker", "approval", note="stateful: a person decides"),
                Target("aiops-keycloak", "approval", note="identity provider"),
            ),
            max_attempts_per_incident=2,
            cooldown_s=120,
            max_per_window=3,
            verify_sustain_s=120,
            execute_timeout_s=90,
        ),
        ActionSpec(
            id="rollback_model",
            kind="rollback",
            adapter="model_registry",
            description="Roll the edge model registry back to its previous verified version and restart its runtime.",
            targets=(Target("edge-runtime:traffic-safety-blockage", "auto"),),
            max_attempts_per_incident=1,
            cooldown_s=300,
            max_per_window=2,
            verify_sustain_s=120,
            execute_timeout_s=120,
            reversible=True,
        ),
        ActionSpec(
            id="set_trace_sampling",
            kind="sampling",
            adapter="trace_sampling",
            description="Lower the share of new traces kept, for a bounded time, to relieve telemetry pressure.",
            targets=(Target("otel-traces", "auto"),),
            params={"ratio": (0.01, 0.5), "ttl_s": (60, 900)},
            max_attempts_per_incident=1,
            cooldown_s=300,
            max_per_window=3,
            verify_sustain_s=180,
            execute_timeout_s=10,
            reversible=True,
        ),
        ActionSpec(
            id="scale_service",
            kind="scale",
            adapter="plan_only",
            description="Add replicas of a stateless service. Plan only: one host, no replica to add.",
            targets=(
                Target("api", "plan_only"),
                Target("ingestion", "plan_only"),
                Target("edge-runtime", "plan_only"),
            ),
            params={"replicas": (2, 3)},
            max_attempts_per_incident=1,
            cooldown_s=600,
            max_per_window=1,
        ),
        ActionSpec(
            id="failover_service",
            kind="failover",
            adapter="plan_only",
            description="Fail a stateful service over to a standby. Plan only: no standby exists on one host.",
            targets=(
                Target("postgres", "plan_only"),
                Target("kafka", "plan_only"),
                Target("mosquitto", "plan_only"),
            ),
            max_attempts_per_incident=1,
            cooldown_s=900,
            max_per_window=1,
        ),
        ActionSpec(
            id="quarantine_device",
            kind="quarantine",
            adapter="plan_only",
            description="Stop accepting a misbehaving device's events. Plan only: ingestion has no quarantine enforcement.",
            targets=(Target(r"device:[A-Za-z0-9_.\-]{1,64}", "plan_only", pattern=True),),
            max_attempts_per_incident=1,
            cooldown_s=600,
            max_per_window=1,
            reversible=True,
        ),
    )
}


@dataclass(frozen=True)
class Rule:
    """One playbook line: when an incident on `component` has any of `signals` active, try `action` on `target`.
    `clears` are the alerts whose absence, sustained, is what "recovered" means for this rule."""

    component: str
    signals: frozenset[str]
    action: str
    target: str
    clears: frozenset[str]
    params: dict[str, float] = field(default_factory=dict)
    why: str = ""


PLAYBOOK: tuple[Rule, ...] = (
    Rule(
        "edge-runtime",
        frozenset({"EdgeRuntimeDown"}),
        "restart_container",
        "aiops-edge-runtime",
        frozenset({"EdgeRuntimeDown"}),
        why="the edge runtime stopped answering scrapes; a restart is the first, cheapest step",
    ),
    Rule(
        "edge-runtime",
        frozenset({"EdgeModelErrors", "EdgeModelInactive"}),
        "rollback_model",
        "edge-runtime:traffic-safety-blockage",
        frozenset({"EdgeModelErrors", "EdgeModelInactive"}),
        why="the active model fails to load or run; return to the previous verified version",
    ),
    Rule(
        "edge-runtime",
        frozenset({"EdgeModelErrors", "EdgeModelInactive"}),
        "restart_container",
        "aiops-edge-runtime",
        frozenset({"EdgeModelErrors", "EdgeModelInactive"}),
        why="if the rollback did not help, a restart clears a wedged process",
    ),
    Rule(
        "postgres",
        frozenset({"PlatformTargetDown"}),
        "restart_container",
        "aiops-postgres",
        frozenset({"PlatformTargetDown"}),
        why="the database is unreachable; restarting a stateful service needs a person's approval",
    ),
    Rule(
        "postgres",
        frozenset({"PlatformTargetDown"}),
        "failover_service",
        "postgres",
        frozenset({"PlatformTargetDown"}),
        why="if a restart does not bring it back, the next step is a failover - planned for a person, not run",
    ),
    Rule(
        "ingestion",
        frozenset({"IngestionLatencyP95High", "NetworkStateFreshnessLow"}),
        "set_trace_sampling",
        "otel-traces",
        frozenset({"IngestionLatencyP95High", "NetworkStateFreshnessLow"}),
        params={"ratio": 0.1, "ttl_s": 600},
        why="telemetry overhead competes with ingestion; keep fewer traces for ten minutes",
    ),
    Rule(
        "api",
        frozenset({"ApiLatencyP95High"}),
        "scale_service",
        "api",
        frozenset({"ApiLatencyP95High"}),
        params={"replicas": 2},
        why="the API is slow under load; a second replica is planned for a person to approve",
    ),
)


@dataclass(frozen=True)
class Candidate:
    rule: Rule
    spec: ActionSpec
    target: Target
    motivating: tuple[str, ...]  # the active signal keys that matched


def candidates(root_component: str, active_signals: list[dict]) -> list[Candidate]:
    """Playbook lines that apply to an incident, in playbook order. `active_signals` are the incident's currently
    active signal rows (`signal`, `signal_key`)."""
    out = []
    for rule in PLAYBOOK:
        if rule.component != root_component:
            continue
        matched = tuple(
            sorted(s["signal_key"] for s in active_signals if s["signal"] in rule.signals)
        )
        if not matched:
            continue
        spec = ACTIONS[rule.action]
        target = spec.target(rule.target)
        if target is not None:
            out.append(Candidate(rule, spec, target, matched))
    return out
