# Roles, safety classes and action authority

Task P02.08. This finalizes the platform's *operational* RBAC roles and the
seven-way authority separation required by
`docs/REFERENCE_ARCHITECTURE.md`'s command safety path. It is distinct from
the sixteen project-local agent skill codes in `TASK_REGISTER.md`'s "Role
owners" table, which describe who builds the platform, not who operates it.
P09.02 provisions these as Keycloak realm roles; `policy-decision/v1`'s
`input.role` field and `command/v1`'s `requested_by`/`approved_by` fields
carry them at runtime.

## Operational roles

| Role | Kind | Authority classes held |
|---|---|---|
| `operator` | Human, traffic operations | REQUEST (SC-0, SC-1) |
| `supervisor` | Human, traffic operations | REQUEST, APPROVE (SC-0, SC-1); APPROVE only (SC-2, alongside `incident_commander`) |
| `dispatcher` | Human, emergency | REQUEST (SC-2, emergency-only actions) |
| `incident_commander` | Human, emergency | REQUEST, APPROVE, OVERRIDE (SC-2) |
| `field_responder` | Human, emergency, read-only | none (view-only; no REQUEST/APPROVE/EXECUTE/OVERRIDE) |
| `auditor` | Human, cross-domain, read-only | AUDIT only |
| `demo_operator` | Human, presentation | DEMO only, scoped to the scenario-control API (P05.09), never the operational command path |
| `system:optimization-engine` | Service identity | RECOMMEND (traffic actions) |
| `system:emergency-engine` | Service identity | RECOMMEND (emergency actions) |
| `system:command-executor` | Service identity | EXECUTE only; the only identity permitted to call an infrastructure adapter |
| `system:aiops-remediation` | Service identity | REQUEST, EXECUTE (platform-remediation actions only, never traffic/emergency actions), bounded by P10.08 |

A role held does not imply authority over every safety class; the safety
class tables below are the binding constraint.

## The seven authority classes

1. **RECOMMEND** - produce a `recommendation/v1` record. System capability
   only; no human role holds this directly. A recommendation cannot invoke
   an adapter (`docs/REFERENCE_ARCHITECTURE.md` section 5).
2. **REQUEST** - convert a recommendation, or an operator's own judgment,
   into a `command/v1` record with `status: requested`. Human role only.
3. **APPROVE** - move a `command/v1` record's `policy_decision` toward
   `approved`. For SC-1 and SC-2 (below), the approver must be a different
   person than the requester (four-eyes); `command/v1`'s `requested_by` and
   `approved_by` must differ, checked at policy-evaluation time, not just by
   convention.
4. **EXECUTE** - call the registered adapter for an approved command.
   `system:command-executor` only; no human role executes directly, per
   `docs/REFERENCE_ARCHITECTURE.md`'s command safety path.
5. **OVERRIDE** - bypass a normal-path gate (e.g., act before independent
   outcome verification completes, or countermand an in-flight command).
   Reserved to `incident_commander` for SC-2 only, always logged with a
   mandatory justification string, and always independently audited
   (P09.05). No override exists for SC-0/SC-1; those are cancelled and
   resubmitted through the normal path instead.
6. **DEMO** - start/stop/reset/replay a simulation scenario through the
   separate authorized scenario-control API (P05.09). Structurally
   unable to reach `command/v1`, `recommendation/v1` or any adapter; a demo
   action and an operational action can never share an identity or an
   audit trail entry type.
7. **AUDIT** - read incidents, commands, policy decisions and outcomes.
   Read-only; holding AUDIT never grants REQUEST, APPROVE, EXECUTE or
   OVERRIDE, and holding any of those never grants AUDIT by itself - the
   `auditor` role is granted independently.

## Safety classes

| Class | Definition | Example `action_type` | REQUEST | APPROVE | EXECUTE | OVERRIDE |
|---|---|---|---|---|---|---|
| SC-0 | Informational, non-signal, fully reversible | `variable_message_sign` (advisory only) | `operator` | Not required (requester's REQUEST is sufficient) | `system:command-executor` | Not available; cancel and resubmit |
| SC-1 | Traffic-influencing, reversible, within `recommendation/v1.safety_bounds` | `signal_plan_change`, `diversion`, `transit_priority` | `operator` | `supervisor`, different person than requester | `system:command-executor` | Not available; cancel and resubmit |
| SC-2 | Safety-critical or emergency-affecting | `emergency_preemption`, any action during an active `incident/v1` with `severity: critical` | `operator` or `dispatcher` | `supervisor` or `incident_commander`, different person than requester, mandatory (never auto-approved) | `system:command-executor` | `incident_commander` only, justification and audit mandatory |

`recommendation/v1.action_type` and `command/v1.action_type` share one enum;
the safety class is derived from that `action_type` plus whether an active
critical `incident/v1` or `emergency-call/v1` references the same target,
not stored as a separate field an operator could edit.

## Enforcement mapping

- `policy-decision/v1.input.role` must be one of the human/service roles
  above; OPA denies (per `docs/security/` threat model, P09.03) any role not
  in this table.
- `command/v1.requested_by` != `command/v1.approved_by` is a mandatory OPA
  check for SC-1 and SC-2; a match is a policy denial, not a warning.
- `system:command-executor` is the only identity with adapter network
  reachability (P09.04 network policy); this makes EXECUTE-by-human
  structurally impossible, not just procedurally forbidden.
- Demo/scenario-control identities (P05.09) hold no Keycloak role that
  intersects the operational-role list above; a shared identity between DEMO
  and any other class is a provisioning defect, not an accepted design.
- `incident_commander` OVERRIDE actions must reference a `command/v1` and
  record a justification; P09.05 makes this audit entry append-only.

## Revision

This mapping is binding for P07.05 (command approval/policy), P08 role
journeys, P09.02 (Keycloak roles) and P09.03 (OPA enforcement). A change
here after those tasks start requires updating all of them in the same
change, not independently.
