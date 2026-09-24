# P07.04 - traffic recommendation/constraint engine

`engine.py` (shared `Alternative`/`SafetyBounds` shape and hard enforcement), `diversion.py`,
`signal_plan.py`, `recommendation_service.py` (ties a live incident to a stored recommendation),
`backend/repositories/recommendations.py`, migration 0018 (contract-complete columns), `GET
/api/v1/recommendations`, `verify_recommendations.py`.

Every generator (diversion here; signal-plan-change here; transit priority and emergency
pre-emption in P07.07/P07.08, reusing this same engine) emits the same `contracts/recommendation/v1`
shape: named+valued+unit `predicted_benefit`/`predicted_harm` metrics per alternative, a confidence,
and `safety_bounds` (`min_pedestrian_clearance_s`, `max_signal_deviation_s`). `enforce()` is not
advisory - it **drops** any alternative that would violate a bound (never silently clips a number
back into legality, which would report a different action than was evaluated) and raises
`UnsafeAlternative` if that leaves nothing, so a recommendation can never carry an option that would
itself be unsafe to approve unmodified. This is the first of two independent safety checks; P07.05's
execution-time policy is the second, at approval time.

**Diversion** reuses P07.02's real router, not a separate model: for a closed/hazardous segment it
compares the router's own detour ETA against the same trip with no closure, and scales that delay by
the segment's real recent corridor throughput (P06.01) - or a stated default when nothing has been
measured recently, never a silently invented number. Three alternatives, always: full diversion,
VMS-advisory-only, and no action.

**Signal-plan-change** is honestly scoped: there is no per-movement signal-plan model in this
platform yet (P03.03's signal devices report SPaT telemetry, not a base plan an optimizer could
rewrite), so this is a bounded green-time reallocation - up to `max_signal_deviation_s` borrowed from
the cross-street phase, pedestrian clearance held fixed - with benefit/harm derived from the
corridor's own real measured `delay_s`/`queue_fraction` under a stated one-for-one reallocation
approximation, not a calibrated queueing model.

`verify_recommendations.py` (real Postgres, 17 checks): diversion against a real closed segment
(genuinely routes around it, using the live router); signal-plan-change against a real freshly
measured corridor KPI; an over-bound (30 s > 20 s max) alternative is dropped by enforcement, not
merely flagged, verified by inspecting what actually survives; an all-unsafe alternative set raises
`UnsafeAlternative`; superseding (a fresher recommendation for the same incident supersedes the
prior live one - exercised through the real service wiring, catching a real UUID/str comparison bug
along the way) and expiry both persist correctly; the API serves `recommendation/v1`-schema-valid
records. Unit tests: `tests/test_recommendation_engine.py` (7, pure enforcement logic). Evidence:
`docs/evidence/p07_04_recommendations.json`.

## P07.05 - command approval and execution-time policy

`policy.py` (pure `evaluate()` decision function + `build_context`/`evaluate_command` I/O boundary),
`backend/roles.py` (the binding P02.08 role/safety-class model, one place code reads it from),
`backend/repositories/commands.py` (state machine, idempotency, `requested_by_role`/`approved_by_role`),
`command_service.py` (`request_command`/`review_command`/`deny_command`), `GET
/api/v1/commands[/{id}]`, `verify_commands.py`.

This is the **second** independent safety check - P07.04's `enforce()` is the first, at
recommendation-generation time; this one runs again at approval time, against whatever is true
*right now*, because a recommendation generated minutes ago may no longer be safe to execute.
`evaluate()` itself is a pure function over a `PolicyContext` snapshot (17 unit tests, no I/O), so
every denial reason - expired, four-eyes, role, invalid target, mismatched/stale recommendation,
stale evidence - is independently testable; `build_context` is the one place that touches the
database, and `evaluate_command` wraps it so **any** failure there (a real outage or an injected one)
becomes `policy_unavailable` rather than a crash or, worse, a silent approval.

**Safety classes are derived, never stored.** `docs/security/ROLES_AND_ACTION_AUTHORITY.md` (P02.08)
is the binding source; `backend/roles.py` is its one code representation. Each `action_type` has a
base class (SC-0 `variable_message_sign`; SC-1 `signal_plan_change`/`diversion`/`transit_priority`;
SC-2 `emergency_preemption`), and an active **critical** incident referencing the same target raises
*any* action on it to SC-2 (`derive_safety_class`, checked against the real `incidents` table, not a
flag an operator could edit). REQUEST authority is checked at the door
(`command_service.request_command`): an `operator` may request SC-0/SC-1, a `dispatcher` only SC-2; a
role that cannot request the class is refused before any row is written, and the role is stored on the
command so approval-time policy can re-check it. **Four-eyes** applies to SC-1/SC-2 only (SC-0's own
requester may approve it - an advisory sign needs no second person) and is structural, not a UI
convention: the requester and approver are compared as data, and a command reviewed by its own
requester is genuinely denied (`policy_denied`). **APPROVE** is `supervisor` for SC-0/SC-1,
`supervisor` or `incident_commander` for SC-2 - never `operator`/`dispatcher`, and four-eyes still
holds even for an `incident_commander`. **EXECUTE** is enforced as an identity check
(`backend/roles.require_executor`) inside every adapter service
(`simulator_adapters.py`/`live_session.py`/`preemption_service.py`/`transit_priority_service.py`): only
`system:command-executor` may call an adapter, refused for any human actor including the approver.

**Fresh evidence (SAFE-03)** reuses the exact freshness sources P07.02's router already treats as live
(a segment's own recent corridor KPI window, or an intersection/corridor's recent device telemetry) -
a target with no recent measurement is denied `stale_evidence` and marked `retryable`, never silently
acted on. **Idempotency** is the database's own `UNIQUE` constraint on `idempotency_key` (migration
0004), not just a repository convention: `create_command` checks first for speed, but a concurrent
duplicate that wins the race still gets caught by the constraint and resolved to the existing command,
never a second row.

`verify_commands.py` (real Postgres, 31 checks, on the real seeded network): every denial reason
above, each forced for real (an unauthorized role really is rejected, a target that does not exist on
the real network really is invalid, a corridor with genuinely no recent KPI row really is denied); the
full P02.08 role/safety-class matrix (a dispatcher refused SC-1, SC-0 needing no second person, SC-2
approved only by supervisor/incident_commander with four-eyes still enforced, a real critical incident
escalating a diversion to SC-2 and changing who may approve it, a human reviewer's own `deny_command`
requiring the same approve authority, EXECUTE refused to every non-`system:command-executor` identity);
a real policy outage (the DB-touching step patched to raise) leaves the command `requested` -
`policy_unavailable`, not approved, and the same command reviews normally once the "outage" clears;
resubmitting an idempotency key returns the existing command and the database still holds exactly one
row for it; the repository's own state machine rejects an illegal transition independent of what
policy would say; the API serves `command/v1`-schema-valid records. Evidence:
`docs/evidence/p07_05_commands.json`.

## P07.06 - simulator signal/diversion/VMS adapters

`simulator/control_adapters/` (adapters.py, run_action.py, run_container.sh - real TraCI, see its
own README), `simulator_adapters.py` (host wiring), `verify_simulator_adapters.py`.

Executes an **approved** command (P07.05's gate) against the real pinned SUMO image via TraCI - the
platform's real "field" boundary for this demo, a fresh isolated container per action, talking to
the backend over a file-based action/result protocol. Only a command whose target names a
`KNOWN_ADAPTERS`-registered adapter+kind is ever dispatched (checked again here, independent of
P07.05's own check - "verify again at the boundary that actually acts"). The command's concrete
parameters (a signal deviation in seconds, a VMS message) are **not** reconstructed from the command
record - the contract deliberately carries only `target.adapter`/`entity_id`, no magnitude - so
`execute_command` takes `params` explicitly from whatever selected the alternative.

`verify_simulator_adapters.py` (real Postgres + real Docker + real TraCI, 11 checks): a full
request -> P07.05 policy approval -> P07.06 execution pipeline against a real running SUMO
instance for all three adapters - the signal adapter genuinely moves a real traffic light's next
phase switch, the diversion adapter genuinely closes real general-traffic lanes (observed back from
SUMO, not assumed), the VMS adapter completes while honestly reporting no simulator actuation.
Idempotency is proven at the simulator layer itself, not merely re-asserted from P07.05's database
guarantee: a second execution of the same command is a byte-identical ledger replay, and SUMO is
never re-started. A target the simulator itself does not recognize fails the command, never silently
succeeds; an unapproved command and an unregistered adapter are both refused before any dispatch; a
human identity (e.g. the approving supervisor) is refused EXECUTE - only `system:command-executor`
may call `execute_command` (`backend/roles.require_executor`, P02.08). Unit tests:
`tests/test_simulator_adapters.py` (7, mocked `traci`, no real SUMO - the real actuation is what the
Postgres+Docker verification proves). Evidence: `docs/evidence/p07_06_simulator_adapters.json`.

## P07.07 - emergency green corridor/pre-emption

`preemption.py` (pure planning: corridor derivation, corridor-id selection, session lifecycle),
`simulator/control_adapters/phase_control.py` (the real TraCI movement-aware phase mechanism, shared
with P07.08), `simulator/control_adapters/signal_safety.py` (the independent runtime safety monitor,
also shared with P07.08/P07.10), `simulator/control_adapters/preemption_run.py` (the corridor scenario:
baseline/preempt/abort/`preempt_naive` modes), `preemption_service.py` (host wiring on P07.02's router,
P07.05's policy - `dispatcher` REQUEST, `incident_commander`/`supervisor` APPROVE, four-eyes - P07.06's
container-execution pattern), `verify_preemption.py`.

**P07.10 found this mechanism's first version unsafe and it was fixed here, not patched around.**
Driving every intersection to a fixed "corridor phase" (phase 0) made pre-emption slower than doing
nothing on a route that turns (a genuine `int-b1` pre-emption scenario measured pre-emption arriving
*later* than the unassisted baseline), because phase 0 does not serve every movement. And shortening
every phase on the way there - including the yellow and pedestrian-clearance phases - passed the
existing "transitions follow the program's own `next` sequence" check while still cutting a real
clearance interval short. Both defects were found by building an **independent** runtime monitor
(`signal_safety.SignalSafetyMonitor`) that reads the raw red/yellow/green state of every controlled
intersection at every simulation step and checks it against the network's own compiled design
(`load_design`, derived from the net file's own phases, `<request foes=...>` table and pedestrian
crossing links - never hand-typed numbers): conflicting protected greens, a green-to-red transition
with no yellow, a yellow or pedestrian green shorter than the design's own minimum, and a vehicle green
starting before the design's own pedestrian-clearance gap has elapsed.

**The fix, `phase_control.MovementPriority`:** the target phase is the one that gives the vehicle's
*actual* movement (`entry_edge -> exit_edge`, resolved from TraCI's own `getControlledLinks`, not
assumed) the most protected green - never a fixed index. Advancing toward it only ever shortens an
*actuated service phase* down to its own `minDur`; every fixed phase (yellow, all-red, pedestrian
green) always runs its full designed duration - the safe-by-construction property is now "the
mechanism can only spend time inside phases the program itself made shortenable," not "the mechanism
never skips a phase" (which the old, unsafe version also satisfied). `verify_preemption.py` proves
both properties independently of the mechanism's own bookkeeping: `signal_safety` must report zero
violations over the real run, and the target phase is checked against the network file to confirm it
actually gives the vehicle's movement a green.

**A negative control makes the fix's necessity demonstrable, not just asserted.** `preempt_naive`
(`NaivePhaseZero` in `preemption_run.py`, explicitly marked never used by the platform) reproduces the
original mechanism; `verify_preemption.py` runs it on the same scenario and shows it passes the old
phase-order check while the independent monitor catches it (a real `short_pedestrian_clearance`/
`short_yellow` violation in the real simulator, not a synthetic example).

**A paired, same-seed comparison** (identical deterministic starting signal state - a light serving the
cross street on a long green, the situation pre-emption exists for) shows a real, measured 6%
travel-time reduction for the emergency vehicle (81 s baseline -> 76 s with pre-emption) on this
scenario; P07.10's fifteen-trial evaluation across three scenarios and normal/incident conditions is
the properly powered measurement for ETA-03.

**Session state** holds exactly one intersection held at a time, following the corridor steps derived
from the vehicle's own real route (P07.02); `abort()` (or normal completion) restores every touched
intersection to its **real original program id** (`setProgram`, not a remembered phase/timer) -
confirmed in `verify_preemption.py` by reading the program back from TraCI after restoration, not
assumed. An aborted command always ends `failed`, never reported as a successful execution.

`verify_preemption.py` (real Postgres + real Docker + real TraCI, 12 checks): a real emergency call
(P07.01) with a real route (P07.02) requests pre-emption, approved by real policy
(`incident_commander`, real fresh-corridor-KPI evidence, four-eyes against the requesting
`dispatcher`), executed against a real running SUMO instance; the paired baseline/pre-emption
comparison; the target phase verified to serve the vehicle's actual movement; the independent safety
monitor reporting zero violations in both runs; the negative control; abort mid-corridor restores the
real original program. Unit tests: `tests/test_preemption.py` (8, pure corridor/session logic) and
`tests/test_signal_safety.py` (13: design minimums derived correctly, every real program replays
clean, and each rule - conflicting green, missing yellow, short yellow, short pedestrian green, short
pedestrian clearance - is shown to fire on a hand-built counterexample). Evidence:
`docs/evidence/p07_07_preemption.json`.

## P07.08 - transit priority and balanced corridor action

`simulator/control_adapters/transit_priority_run.py` (reuses P07.07's `phase_control`/`signal_safety`,
not duplicated), `transit_priority_service.py`, `verify_transit_priority.py`.

Same real, movement-aware, independently-monitored TraCI mechanism as P07.07's emergency pre-emption -
this action's own acceptance bar is **balanced** measurement, so both sides are reported honestly, not
just the transit number. `int-b1` is used deliberately: it is a genuine multi-approach intersection
with real vehicular cross traffic (confirmed with TraCI's own `getControlledLinks` during
development), unlike a plain through-intersection on this network (P03.01) whose minor phase is
pedestrian-only, not a competing vehicle movement - stated in the module docstring so the choice of
intersection is traceable to a real topology fact, not arbitrary.

A paired, same-seed comparison (identical deterministic starting signal state) measured a real
28% transit travel-time reduction (109 s -> 79 s) - and a cross-traffic mean-wait number that came
out *lower*, not higher, under priority (5.62 s -> 2.09 s), both runs sampled over the **same fixed
150 s window** after the bus departs so the comparison is like-for-like (the earlier version let the
priority run's own earlier arrival cut its own sampling window short, which could bias the harm number
without either run doing anything different). That is reported as measured, with the honest note that
a real, sometimes-counterintuitive number is more useful than a fabricated expected one.

`verify_transit_priority.py` (real Postgres + real Docker + real TraCI, 10 checks): a real
`transit_priority` command approved by real policy (`supervisor` role, real fresh corridor-b KPI
evidence); the paired benefit/harm comparison over a matched window; the target phase verified to give
the bus's actual movement a green; the independent safety monitor reporting zero violations in both
runs; every phase transition checked against the real compiled program. Unit tests:
`tests/test_transit_priority.py` (3, the corridor-id selection for a route that starts on a
cross-street edge before reaching its corridor - corridor derivation and session lifecycle are the
same functions P07.07 already tests). Evidence: `docs/evidence/p07_08_transit_priority.json`.

## P07.09 - independent outcome verification and rollback

`outcome_verification.py` (pure `classify`, `mean_corridor_metric`, `verify_and_rollback`),
`live_session.py` (host side of a long-lived simulator session, `execute_command_live`),
`../repositories/outcomes.py` (`contracts/outcome/v1` persistence), `simulator/control_adapters/
session_server.py` (the container side), `verify_outcomes.py`, API `GET /api/v1/outcomes[/{id}]`.

**Why a live session.** P07.06 runs each action in a fresh container, so an action's effect dies with
the process: nothing can be measured *around* it, and nothing can be physically undone afterwards.
Outcome verification needs one simulated world that stays alive across "measure before -> apply ->
measure after -> undo". The session server is that world, driven request by request over files
(`advance`, `apply`, `undo`, `state`) - lock-step, so the host decides exactly which window is "pre"
and which is "post". `apply` reuses P07.06's own `adapters.apply_*`; P07.06's per-action path is kept
unchanged.

**Classification** (`classify`, pure, in this order): `unknown` when either window has no
measurement (never defaulted to `effective`; escalated); `unsafe` when the metric worsened by more
than the safety threshold; `effective` when it improved by more than the effectiveness threshold;
otherwise `ineffective`. A window with no measurement is stored as a zero-valued `<metric>_sample_count`
(true, contract-valid) rather than an invented number.

**Rollback is claimed only when it verifiably happened.** An `unsafe` verdict moves the command
`executed -> rolled_back` through P05.08's own state machine, but only after the physical reversal is
confirmed: signal/diversion adapters run a live-session `undo` (cancel the remaining phase extension /
reopen the closed lanes) and the result is read back from TraCI; pre-emption and transit priority
already restored their own intersections at the end of their corridor run (P07.07/P07.08); VMS has no
simulator actuation, so nothing exists to reverse and the outcome says so. If a reversible action has
no undo, or its undo does not restore, the command **stays `executed`** and the outcome is escalated.

**Independence is structural.** Only `system:outcome-verifier` may record an outcome at all
(`backend/roles.require_verifier`, the same identity-check pattern EXECUTE uses); within that identity,
the verifier must also differ from the command's requester, approver and executor - refused in
`verify_and_rollback` before anything is actuated, and again independently in `record_outcome` (the
last gate before storage). An outcome also cannot be recorded for a command that is not
`executed`/`rolled_back`, with windows out of order, or verified before its post window closed.

**Thresholds are measured, not chosen.** `verify_outcomes.py` first runs the simulator with **no action**
on seeds the scenarios never use (3-5; nine consecutive-window deltas) and sets both thresholds to
`ceil(2 x max |delta|)` = 15 vehicle-seconds (observed noise band 7.39).

`verify_outcomes.py` (real Postgres + real Docker + real TraCI, 31 checks). What the real simulator showed:
- Closing the busy edge `int-b2_int-b3` (both general lanes, no rerouting - what P07.06's diversion
  adapter does) raised mean total edge waiting from 10.0 to 459.3 vehicle-s: `unsafe`, command
  `rolled_back` by the verifier, lanes read back from TraCI as closed before the undo and back to their
  original `['pedestrian', 'bicycle']` after it, and waiting fell to 89.0 in the next window.
- A 15 s signal extension at `int-a2` was `ineffective` (7.44 -> 15.93) - and a same-seed **no-action
  control** gives exactly the same 15.93, so the before/after difference is demand drift, not the action.
  That is the honest reading of a before/after window: it cannot separate an action from time drift
  smaller than the noise band, which is why the threshold is noise-derived.
- A paired same-seed transit-priority run (baseline 109 s -> priority 79 s) is `effective`.
- Cases that isolate the decision logic (a post-action telemetry gap read from `corridor_kpis` -> `unknown`
  and escalated; an unsafe VMS outcome on KPI fixture rows -> rolled back with "no physical undo"; an
  unavailable or failed undo -> stays `executed`, escalated) use **fixture KPI rows on a coherent replay
  timeline** (request, review, execution, windows and verification all consistent), and say so.

**LAT-04 (approval -> adapter acknowledgement, P95 < 3 s), measured two ways.** Warm live session:
P95 0.057 s over 24 real commands (the recommended runtime shape - a field adapter is a persistent
connection). Cold one-container-per-action path (P07.06): P95 2.70 s over only 5 runs (max 2.70, median
2.49) - under the target but with little margin and a small sample, and dominated by container start.

Limits stated plainly: the measurement source is the same simulator that acted (TraCI), not an
independent sensor feed; the stored windows are wall-clock timestamps of *when the simulator was
measured* (simulated-time bounds are stored in `detail` for the unsafe scenario); the before/after
design carries the drift caveat above. Unit tests: `tests/test_outcome_verification.py` (10). Evidence:
`docs/evidence/p07_09_outcomes.json`.

## P07.10 - ambulance, fire and police end-to-end scenarios

`emergency_flow.py` (`run_dispatch`: one call through the whole platform - live-state-aware route/ETA
(P07.02), assignment (P07.01), pre-emption request/approval/execution (P07.05-P07.07), independent
outcome verification (P07.09), plus a paired same-seed baseline with no pre-emption, all fail-closed on
every failure branch), `verify_scenarios.py`.

**Every number here is what the real simulator produced for the real platform code path** - no
scenario-specific shortcut. A dispatch's ETA comes from `backend/routing/route_service.route` reading
real `corridor_kpis` rows that `emergency_flow.publish_live_kpis` derived from a real no-vehicle
`snapshot` run of the same simulated world the emergency vehicle then departs into (the same demand,
same seed, the last 30 s before departure) - never network-file free-flow numbers or invented
congestion. The unit's *actual* travel time is whichever run really happened: pre-emption's real result
when the command was approved and executed, the baseline's real result otherwise (denied, expired,
policy outage, adapter failure) - the platform never claims a benefit that did not occur.

**This is where the pre-emption mechanism's unsafe first version was found** (see P07.07): building
this suite's independent `signal_safety.SignalSafetyMonitor` and running it over real scenarios
surfaced both the wrong-phase-target defect (pre-emption slower than nothing on a turning route) and
the shortened-clearance defect, neither visible from the old "phase order" check alone. Every dispatch
in this suite runs with the fixed mechanism and the monitor watching every controlled intersection at
every simulated second.

`verify_scenarios.py` (real Postgres + real Docker + real TraCI, 18 checks; three scenarios - ambulance
int-a1->int-a4, fire int-c1->int-b4, police int-a4->int-c1 - five paired trials each, normal and
under-incident, plus dedicated failure-limit and cross-agency runs):
- **ETA-01** (normal traffic): pooled MAE 8.4% (n=15; ambulance 3.65%, fire 6.91%, police 14.63%) -
  met against the 15% target.
- **ETA-02** (a real collision incident closes a segment on the route; the router learns of it from a
  real `incidents` row and genuinely reroutes around it, checked per trial): pooled MAE 10.55% (n=15) -
  met against the 25% target.
- **ETA-03**: 30 paired same-seed dispatches (normal + incident), mean reduction 8.13 s (6.1%), 19
  improved / 9 tied (the corridor was already green - most of this network's real traffic) / 2 worse by
  a small margin; a one-sided exact sign test on the untied pairs rejects "no effect" at p=0.0001. Every
  executed pre-emption is independently verified by P07.09 (17 effective, 13 ineffective, **zero**
  `unknown`) - the platform's own safety net, not just this evaluation's summary statistic.
- **SAFE-01**: zero violations from the independent monitor over 162,072 real intersection-state
  observations across every run in the suite, including the failure-limit runs.
- **Traffic outcomes**, paired: network-wide mean waiting rose 0.77 s on average under pre-emption
  (worst case +9.14 s, best case -3.05 s), cross-street mean waiting rose 0.30 s - reported, not hidden,
  because pre-emption is a real trade-off, not a free benefit.
- **Failure limits, each with its measured consequence**: heavy congestion (nearly double demand) still
  completes with 6.57% pooled MAE; no open route is reported honestly with zero commands created, never
  guessed; a policy-engine outage leaves the command `requested`/`policy_unavailable` and it later
  expires, the unit driving on unassisted the whole time; an unreachable adapter fails the command
  `adapter_unreachable`/retryable and the unit continues on the baseline path; stale evidence (no
  published live KPI) denies the pre-emption request outright. Every failure run also holds zero signal
  safety violations.
- A real **staged cross-agency response** (P07.03): police secures the scene, fire is held (a real
  `StagingViolation`) until police arrives, then proceeds, then EMS is released once fire is on scene.
- The recorded `emergency-call`/`emergency-unit-assignment` timeline (P07.01) matches the simulated
  travel time to the second, and the stored route alternative's ETA matches what was actually predicted
  at dispatch time - the operational record and the simulation agree because they are the same numbers,
  not reconciled after the fact.

No unit tests of its own: every function this module composes (`route_service.route`,
`request_preemption`/`execute_preemption`, `verify_and_rollback`, the emergency state machines) already
has direct unit coverage where it is defined; this module's own correctness is what the real-stack run
proves. Evidence: `docs/evidence/p07_10_scenarios.json`.

## The human side of the command path, and the two workers (P08.08)

A command is requested by one person, approved by a *different* person in a role its safety class allows, and executed by a service - never
by a person.

| Step | Who | Where |
|---|---|---|
| Request | an operating role (`REQUEST_ROLES[safety class]`), from a stored recommendation or directly; idempotent on a client key | `backend/api/routes_commands.py` |
| Approve or deny | a different person (`APPROVE_ROLES[safety class]`); the endpoint checks role and four-eyes *before* the policy runs, because a policy denial is recorded against the command | same |
| Policy | `policy.py` runs again at approval time against what is true now; if it cannot run the command stays `requested` with policy decision `policy_unavailable` and is never approved | `command_service.review_command` |
| Execute | `system:command-executor` (`executor_worker.py`): picks up approved commands, drives the adapter (the real simulator adapter in the pinned SUMO container), records what the adapter observed, expires commands nobody acted on in time | `executor_worker.py` |
| Verify | `system:outcome-verifier` (`verifier_worker.py`): once a command's after-window has closed, measures the corridor before and after with a noise band derived from the corridor's own window-to-window changes, and records an outcome; an unsafe result is rolled back with the undo confirmed | `verifier_worker.py`, `outcome_verification.py` |

Both workers write a heartbeat (`heartbeat.py`, table `service_heartbeats`) the platform-status screen reads: a worker that has never
reported is *unknown*, not healthy. Roles and who may do what: `backend/roles.py` and `docs/security/ROLES_AND_ACTION_AUTHORITY.md`.

**Limit, stated where it matters:** in the demo world the traffic is a replayed recording that does not react to commands, so the
verifier's classification of a command executed there describes the recorded traffic, and the outcome says so. Real-traffic effect is
what P07.09's lock-step simulator runs measured.

Verification: `python source-code/backend/api/verify_command_workflow.py` (real Keycloak, Postgres, the API under uvicorn, the real
executor and verifier) - evidence `docs/evidence/p08_08_evidence.json`.
