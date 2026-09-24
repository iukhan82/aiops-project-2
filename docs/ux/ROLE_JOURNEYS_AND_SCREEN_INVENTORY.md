# Role journeys, screen and state inventory

Task P08.01. This maps every operating role, task, error, freshness state and
action state in the operator UI to a capability. It is the binding input to the
wireframes (P08.02), the design handoff (P08.03), the frontend (P08.04-P08.09)
and the acceptance pass (P08.10).

The tables below the "Generated" marker come from one machine-readable file,
`source-code/frontend/src/config/inventory.json`. That file is also what the
backend reads to decide who may call what (`backend/api/authz.py`, P08.04) and
what the frontend reads to decide which navigation entries and routes exist.
There is deliberately no second copy to drift. `python
source-code/frontend/tools/inventory.py` proves the file against the role model
in `docs/security/ROLES_AND_ACTION_AUTHORITY.md` (via `backend/roles.py`) and
against the endpoints the FastAPI apps really serve, and fails if either
disagrees or the generated tables here are stale.

## Principles the screens must obey

These come from `docs/PROJECT_CONTEXT.md` and
`design-system/.../MASTER.md`; each is testable and each is tested in P08.10.

1. **Truth is labelled.** Every displayed value shows its `truth_label`
   (`simulated`, `measured`, `inferred`, `predicted`, `operator_entered`,
   `verified`) and its freshness (`fresh`, `stale`, `unknown`) with the time it
   was observed. Nothing cached is shown as live (UX-02).
2. **A recommendation is not a command is not an outcome.** They are different
   records with different screens and different state names; a recommendation is
   never rendered as something that was done.
3. **Status is never colour alone.** Every state has a text label and a shape or
   icon; every chart has a table alternative.
4. **Absent, not disabled, for authority a role does not hold.** A role that
   cannot request/approve/execute does not see a greyed control it can never use.
   Where the role holds the authority but policy may still refuse, the control is
   present and the refusal is shown with its reason.
5. **A critical action needs an explicit confirmation step** that restates the
   target, reason, expected effect, constraints, expiry and policy status, and it
   is disabled while executing so it cannot be submitted twice (UX-03).
6. **Never silent success.** Every command ends in a visible state; failure and
   rollback are as prominent as success.
7. **Demo is visibly separate.** The demo screen has its own banner, identity and
   API and shares no table with operations.
8. **Complex control is desktop-first.** The field view supports status, route
   and ETA, not condensed control panels (UX-04).

<!-- BEGIN GENERATED: inventory -->

### Roles and capabilities

| Capability | operator | supervisor | dispatcher | incident_commander | field_responder | auditor | demo_operator |
|---|---|---|---|---|---|---|---|
| `map.view` | yes | yes | yes | yes | yes | yes | yes |
| `analytics.view` | yes | yes | yes | yes | - | yes | yes |
| `incidents.view` | yes | yes | yes | yes | yes | yes | - |
| `incidents.manage` | yes | yes | - | yes | - | - | - |
| `emergency.view` | yes | yes | yes | yes | yes | yes | - |
| `emergency.dispatch` | - | - | yes | yes | - | - | - |
| `routes.view` | yes | yes | yes | yes | yes | - | - |
| `recommendations.view` | yes | yes | yes | yes | - | yes | - |
| `commands.view` | yes | yes | yes | yes | - | yes | - |
| `commands.request` | yes | - | yes | - | - | - | - |
| `commands.review` | yes | yes | - | yes | - | - | - |
| `outcomes.view` | yes | yes | yes | yes | - | yes | - |
| `audit.view` | - | - | - | - | - | yes | - |
| `ops.view` | yes | yes | - | yes | - | yes | - |
| `handover.view` | yes | yes | yes | yes | yes | yes | - |
| `handover.write` | yes | yes | yes | yes | - | - | - |
| `demo.control` | - | - | - | - | - | - | yes |
| `audit.export` | - | - | - | - | - | yes | - |
| `commands.override` | - | - | - | yes | - | - | - |

Home screen per role: `operator` -> `map`; `supervisor` -> `actions-commands`; `dispatcher` -> `dispatch`; `incident_commander` -> `incidents`; `field_responder` -> `field`; `auditor` -> `audit`; `demo_operator` -> `demo`.

### Screens

| Screen | Path | Capability | Live | Designed states | Optional by capability | Task |
|---|---|---|---|---|---|---|
| Sign in (`login`) | `/login` | public | no | signing-in, redirecting, error | - | P08.04 |
| Not permitted (`forbidden`) | `/forbidden` | public | no | forbidden | - | P08.04 |
| Session ended (`session-expired`) | `/session-expired` | public | no | expired | - | P08.04 |
| Page not found (`not-found`) | `*` | public | no | not-found | - | P08.04 |
| Live operations map (`map`) | `/map` | `map.view` | yes | loading, empty, error, forbidden, stale, reconnecting, replay, paused | `incidents.list`, `emergency.calls.list` | P08.05 |
| Corridor analytics (`analytics-corridors`) | `/analytics/corridors` | `analytics.view` | yes | loading, empty, error, forbidden, stale, reconnecting, forecast-abstained, quality-suspect | - | P08.06 |
| Intersection analytics (`analytics-intersections`) | `/analytics/intersections` | `analytics.view` | yes | loading, empty, error, forbidden, stale, reconnecting | - | P08.06 |
| Device health (`analytics-devices`) | `/analytics/devices` | `analytics.view` | yes | loading, empty, error, forbidden, stale, reconnecting, certificate-expiring | - | P08.06 |
| Incidents (`incidents`) | `/incidents` | `incidents.view` | yes | loading, empty, error, forbidden, stale, reconnecting | - | P08.07 |
| Incident investigation (`incident-detail`) | `/incidents/:incidentId` | `incidents.view` | yes | loading, empty, error, forbidden, not-found, stale, conflict | `incidents.transition`, `incidents.owner`, `incidents.notes`, `recommendations.list`, `commands.list` | P08.07 |
| Emergency dispatch (`dispatch`) | `/dispatch` | `emergency.view` | yes | loading, empty, error, forbidden, stale, reconnecting | `emergency.calls.create` | P08.07 |
| Call, units and routes (`dispatch-detail`) | `/dispatch/:callId` | `emergency.view` | yes | loading, empty, error, forbidden, not-found, stale, no-route, conflict | `emergency.calls.assign`, `emergency.calls.transition`, `emergency.assignments.transition`, `routes.query`, `emergency.assignments.route` | P08.07 |
| Field view (`field`) | `/field` | `emergency.view` | yes | loading, empty, error, forbidden, stale, reconnecting, offline | `incidents.list`, `routes.query` | P08.07 |
| Recommendations (`actions-recommendations`) | `/actions/recommendations` | `recommendations.view` | yes | loading, empty, error, forbidden, stale, expired, superseded | `recommendations.request` | P08.08 |
| Commands and approvals (`actions-commands`) | `/actions/commands` | `commands.view` | yes | loading, empty, error, forbidden, stale, reconnecting | `commands.create`, `commands.review` | P08.08 |
| Command detail (`actions-command-detail`) | `/actions/commands/:commandId` | `commands.view` | yes | loading, error, forbidden, not-found, stale, conflict | `commands.review`, `outcomes.list`, `commands.override` | P08.08 |
| Verified outcomes (`actions-outcomes`) | `/actions/outcomes` | `outcomes.view` | no | loading, empty, error, forbidden | - | P08.08 |
| Audit trail (`audit`) | `/audit` | `audit.view` | no | loading, empty, error, forbidden | `audit.export` | P08.09 |
| Platform status (`operations`) | `/operations` | `ops.view` | yes | loading, error, forbidden, stale, degraded | - | P08.09 |
| Shift handover (`handover`) | `/handover` | `handover.view` | no | loading, empty, error, forbidden, conflict | `handovers.create`, `handovers.acknowledge`, `incidents.list`, `commands.list`, `emergency.calls.list` | P08.09 |
| Demo controls (`demo`) | `/demo` | `demo.control` | no | loading, error, forbidden, bound-reached | - | P08.09 |

### API endpoints

| Id | Method and path | Capability | Status |
|---|---|---|---|
| `health` | `GET /api/v1/health` | public | implemented |
| `me` | `GET /api/v1/me` | any signed-in role | implemented |
| `topology` | `GET /api/v1/network/topology` | `map.view` | implemented |
| `devices.list` | `GET /api/v1/devices` | `map.view` | implemented |
| `devices.detail` | `GET /api/v1/devices/{device_id}` | `analytics.view` | implemented |
| `network-state` | `GET /api/v1/network-state/{network_element_type}/{network_element_id}` | `map.view` | implemented |
| `network-state.list` | `GET /api/v1/network-state` | `map.view` | implemented |
| `kpis.corridors` | `GET /api/v1/kpis/corridors` | `map.view` | implemented |
| `forecasts.corridors` | `GET /api/v1/forecasts/corridors` | `analytics.view` | implemented |
| `candidates.list` | `GET /api/v1/candidates` | `analytics.view` | implemented |
| `observations.list` | `GET /api/v1/observations` | `map.view` | implemented |
| `incidents.list` | `GET /api/v1/incidents` | `incidents.view` | implemented |
| `incidents.detail` | `GET /api/v1/incidents/{incident_id}` | `incidents.view` | implemented |
| `incidents.transition` | `POST /api/v1/incidents/{incident_id}/transition` | `incidents.manage` | implemented |
| `incidents.owner` | `POST /api/v1/incidents/{incident_id}/owner` | `incidents.manage` | implemented |
| `incidents.notes` | `POST /api/v1/incidents/{incident_id}/notes` | `incidents.manage` | implemented |
| `emergency.calls.list` | `GET /api/v1/emergency/calls` | `emergency.view` | implemented |
| `emergency.calls.detail` | `GET /api/v1/emergency/calls/{call_id}` | `emergency.view` | implemented |
| `emergency.calls.create` | `POST /api/v1/emergency/calls` | `emergency.dispatch` | implemented |
| `emergency.calls.assign` | `POST /api/v1/emergency/calls/{call_id}/assignments` | `emergency.dispatch` | implemented |
| `emergency.calls.transition` | `POST /api/v1/emergency/calls/{call_id}/transition` | `emergency.dispatch` | implemented |
| `emergency.assignments.transition` | `POST /api/v1/emergency/assignments/{assignment_id}/transition` | `emergency.dispatch` | implemented |
| `emergency.assignments.route` | `POST /api/v1/emergency/assignments/{assignment_id}/route` | `emergency.dispatch` | implemented |
| `routes.query` | `GET /api/v1/routes` | `routes.view` | implemented |
| `recommendations.list` | `GET /api/v1/recommendations` | `recommendations.view` | implemented |
| `recommendations.request` | `POST /api/v1/recommendations/{recommendation_id}/request` | `commands.request` | implemented |
| `commands.list` | `GET /api/v1/commands` | `commands.view` | implemented |
| `commands.detail` | `GET /api/v1/commands/{command_id}` | `commands.view` | implemented |
| `commands.create` | `POST /api/v1/commands` | `commands.request` | implemented |
| `commands.review` | `POST /api/v1/commands/{command_id}/review` | `commands.review` | implemented |
| `commands.override` | `POST /api/v1/commands/{command_id}/override` | `commands.override` | implemented |
| `outcomes.list` | `GET /api/v1/outcomes` | `outcomes.view` | implemented |
| `outcomes.detail` | `GET /api/v1/outcomes/{outcome_id}` | `outcomes.view` | implemented |
| `audit.list` | `GET /api/v1/audit` | `audit.view` | implemented |
| `audit.export` | `GET /api/v1/audit/export` | `audit.export` | implemented |
| `ops.status` | `GET /api/v1/ops/status` | `ops.view` | implemented |
| `handovers.list` | `GET /api/v1/handovers` | `handover.view` | implemented |
| `handovers.create` | `POST /api/v1/handovers` | `handover.write` | implemented |
| `handovers.acknowledge` | `POST /api/v1/handovers/{handover_id}/acknowledge` | `handover.write` | implemented |
| `live` | `WEBSOCKET /api/v1/live` | `map.view` | implemented |
| `demo.runs.start` | `POST /scenario-control/v1/runs` | `demo.control` | implemented |
| `demo.runs.detail` | `GET /scenario-control/v1/runs/{run_id}` | `demo.control` | implemented |
| `demo.runs.replay` | `POST /scenario-control/v1/runs/{run_id}/replay` | `demo.control` | implemented |
| `demo.runs.reset` | `POST /scenario-control/v1/runs/{run_id}/reset` | `demo.control` | implemented |
| `demo.runs.list` | `GET /scenario-control/v1/runs` | `demo.control` | implemented |
| `demo.audit` | `GET /scenario-control/v1/audit` | `demo.control` | implemented |
| `demo.health` | `GET /scenario-control/v1/health` | public | implemented |

### Journeys

**`operator`** - Notice a problem on the network, understand it, and get a safe action taken.

1. `map`: Watch layers for congestion, closures and stale sensors; open the accessible list when the map is not the right tool.
2. `incidents`: Open the highest-severity unacknowledged incident.
3. `incident-detail`: Read evidence and ranked hypotheses; acknowledge and take ownership.
4. `actions-recommendations`: Compare alternatives (benefit, harm, bounds); choose one and request it.
5. `actions-command-detail`: Follow the command through approval, execution and the verified outcome.

Failure cases designed for: live feed drops: the map says it is reconnecting and shows the age of every value; a target has no fresh evidence: the command is denied as stale_evidence and says why; the policy engine is down: the command stays requested and is labelled policy unavailable, never approved.

**`supervisor`** - Be the second person: approve or refuse what an operator requested, and verify it worked.

1. `actions-commands`: Open the pending-approval queue (home).
2. `actions-command-detail`: Read target, reason, expected effect, constraints and expiry; approve or deny with a recorded reason after an explicit confirmation.
3. `actions-outcomes`: Check the independent outcome; an unsafe outcome shows the rollback and the physical undo evidence.
4. `handover`: Write the shift handover with open items.

Failure cases designed for: approving your own request: refused by policy and shown as four-eyes, not hidden; the command expired while open: shown as expired, approval disabled.

**`dispatcher`** - Get the right unit to the scene by the fastest safe route.

1. `dispatch`: Take or open a call (home); create one when it arrives.
2. `dispatch-detail`: Compare route alternatives with ETA and uncertainty; assign the unit; record status changes.
3. `actions-commands`: Request signal pre-emption for the selected route (SC-2, needs a second person).
4. `dispatch-detail`: Watch the unit and the pre-emption command until the unit is on scene.

Failure cases designed for: no open route: the screen says so and asks for a human decision; nothing is guessed; the unit becomes unavailable: the call stays open and can be reassigned.

**`incident_commander`** - Own critical incidents and approve safety-critical emergency actions.

1. `incidents`: Open the critical queue (home).
2. `incident-detail`: Escalate, assign and annotate; see linked recommendations and commands.
3. `actions-commands`: Approve an SC-2 command requested by someone else.

Failure cases designed for: requesting and approving as the same person: refused, four-eyes holds for commanders too.

**`field_responder`** - Know where to go, by which route, when to arrive, and what changed.

1. `field`: Read the assigned call, route, ETA and status (home). Read-only on purpose.

Failure cases designed for: connectivity lost: the view says offline and shows the age of what it last had, never presenting it as live.

**`auditor`** - Reconstruct who did what, when, and under which policy decision.

1. `audit`: Filter the append-only trail by actor, entity and time (home).
2. `actions-commands`: Open a command and read its policy decision, roles and state history.
3. `actions-outcomes`: Read the independent outcome and any rollback evidence.

Failure cases designed for: an attempt to act: every control is absent, not merely disabled, because the role holds none of REQUEST/APPROVE/EXECUTE/OVERRIDE.

**`demo_operator`** - Run a simulated scenario for a presentation without touching operations.

1. `demo`: Start, replay or reset a scenario (home), under a permanent 'simulated' banner.
2. `map`: Show the picture the scenario produced.

Failure cases designed for: too many concurrent runs: the bound is shown, not hidden.

<!-- END GENERATED: inventory -->

## Data freshness and truth labels

A value's display state combines two independent fields the API already returns.

| Display state | Meaning | Source | Shown as |
|---|---|---|---|
| Live | `freshness_status = fresh` and the live feed is connected | network-state, WebSocket | age in seconds, plain |
| Delayed | fresh by budget but older than the live cadence | network-state | age, with a "delayed" text label |
| Stale | `freshness_status = stale` | network-state, KPI `stale` flag | "stale" label with the last observation time; value de-emphasised, never hidden |
| Unknown | `freshness_status = unknown`, or a probe is unavailable | network-state, ops probes | "unknown"; no number is invented |
| Simulated | `truth_label = simulated` | every record | persistent "simulated" tag (this whole demo is simulated) |
| Predicted | `truth_label = predicted` | forecasts, ETA | "predicted" with the uncertainty band or +/- value |
| Inferred | `truth_label = inferred` | incident hypotheses, fused candidates | "inferred"; hypotheses are never shown as a verified cause |
| Operator entered | `truth_label = operator_entered` | calls, notes, handover | attributed to the person and role |
| Verified | `truth_label = verified` | independent outcomes | "verified" with the verifier and window |

The live feed adds three states of its own, shown once for the whole page: **connected**, **reconnecting** (with the time it was last connected and the fact that
values may be older than shown) and **paused** (the operator paused updates; a
banner says so and a control resumes).

## Session and authorization states

| State | Trigger | What the UI does |
|---|---|---|
| Signing in | no session | redirect to Keycloak (Authorization Code + PKCE); the UI never sees a password |
| Signed in | valid token | navigation shows only screens the role's capabilities allow |
| Expiring | token refresh pending | silent refresh; if it fails, `session-expired` with the intended destination preserved |
| Forbidden | signed in but lacks the capability | `forbidden` screen naming the missing capability |
| API 401 | token rejected | one refresh attempt, then `session-expired`; never a blank screen |
| API 403 | policy or capability refusal | the reason is shown in place; the action stays available if it may become allowed |

## Action lifecycles and how they are shown

Each label is distinct in text, shape and position; none relies on colour alone.

**Command** (`command/v1` `status` + `policy_decision`)

| State | Meaning | Who sees an action |
|---|---|---|
| Requested / pending | waiting for a second person | reviewers: approve, deny |
| Requested / policy unavailable | the policy engine was down; not approved, never silently approved | reviewers: retry review |
| Approved | policy approved; waiting for the executor | none (humans never execute) |
| Denied | reviewer or policy refused; reason and error code shown | requester: request again |
| Expired | not acted on before `expires_at` | requester: request again |
| Executing | the executor identity dispatched it | none |
| Executed | the adapter acknowledged; the outcome is still to be verified | none; outcome link shown |
| Failed | the adapter failed; error code and whether it is retryable | requester: request again if retryable |
| Rolled back | independent verification found it unsafe and it was undone | none; the outcome and undo evidence link |

**Recommendation:** proposed, requested (a command exists), superseded, expired.
**Outcome:** effective, ineffective, unsafe (rolled back), unknown (escalated - insufficient evidence, never shown as success).
**Incident:** open, acknowledged, investigating, escalated, resolved, reopened; a merged duplicate shows as resolved with a link to the surviving incident.
**Emergency call:** received, dispatched, unit assigned, en route, on scene, cleared, cancelled.
**Unit assignment:** assigned, acknowledged, en route, staged, on scene, clear, unavailable (with a cross-agency handover record where present).

## Error taxonomy

| Backend signal | Meaning | UI message and next step |
|---|---|---|
| `policy_denied` | role, four-eyes, class or reviewer refusal | the specific reason from the policy; who could approve instead |
| `expired` | past `expires_at` | "expired"; request again |
| `stale_evidence` (retryable) | no fresh evidence for the target (SAFE-03) | says which evidence is missing and its age; retry when fresh |
| `adapter_unreachable` (retryable) | the executor could not reach the adapter | failed, retryable |
| `invalid_target` | target is not a real registered thing | failed, not retryable, names the target |
| `conflict` | the record changed under the user | shows what changed; reload the record; no silent overwrite |
| `internal` | anything else | a correlation id, not a stack trace |
| HTTP 400 | invalid input or cursor | field-level message |
| HTTP 404 | unknown id | `not-found` state on that screen |
| HTTP 429 | a bound was reached (e.g. concurrent demo runs) | shows the bound |
| network / 5xx | API unreachable | error state with retry; last known data marked stale, not presented as current |

## Deliberately outside this UI

| Not here | Why | Where it lands |
|---|---|---|
| Override (bypass a gate, countermand in flight) | reserved to `incident_commander` for SC-2 with mandatory justification and independent audit | P09.05 |
| Public information feeds and executive dashboard | different audience and sanitisation rules | not in the academic baseline |
| AIOps remediation views | the platform-remediation loop does not exist yet | Phase 10 |
| Offline-capable responder PWA | field view is online read-only here; offline is marked `offline`, never faked | future |
| Editing or deleting audit rows | append-only by design | never |

## Acceptance traceability

| Target | Verified by |
|---|---|
| UX-01 WCAG 2.2 AA keyboard, contrast, reduced motion, per journey | P08.10 (axe-core plus keyboard-only journeys in a real browser) |
| UX-02 truth label and freshness on every value; no stale-as-live | P08.10 (assertions over every live screen; a forced-stale API response) |
| UX-03 confirmation step; distinct denied/pending/executing/failed/rollback states | P08.08 built, P08.10 tested on every action path |
| UX-04 laptop and projector widths, no clipped or unreachable critical control | P08.02 wireframes, P08.10 at 1366x768, 1920x1080, 390x844 and 400% zoom |
| LAT-05 backend event to rendered UI change P95 < 2 s | P08.10 |
| LOAD-04 ten concurrent authenticated sessions | P08.10 / P11.06 |
