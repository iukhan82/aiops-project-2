# Measurable acceptance targets

Task P02.01. These are planning targets, not results. Every row's Result
column stays `Not yet measured` until the named task produces inspectable
evidence; nothing here is satisfied by this document existing
(`AGENTS.md`: "Code/configuration alone is not runtime evidence"). Targets
are revisited if measured conditions (`docs/environment/RESOURCE_BUDGET.md`)
make a target unreachable on the available hardware; a revised target
records the reason and the date, it does not silently replace the original.

Every target names its measurement conditions (load level, sample size,
scenario) because a threshold without conditions is not measurable. Where a
target depends on an ADR that is still Provisional (`docs/decisions/`), that
dependency is noted explicitly.

## Latency

| ID | Target | Conditions | Measured by | Result |
|---|---|---|---|---|
| LAT-01 | Edge model inference P95 < 100 ms | Warm CPU inference, no discrete GPU, per `docs/environment/RESOURCE_BUDGET.md` | P04.09 | Not yet measured |
| LAT-02 | Edge-to-central event ingest P95 < 2 s | Observation time to durable central persistence, nominal load, MQTT+Kafka-compatible path (ADR-0002) | P05.05, P10.02 | **Met** on TEST: P95 = 27.8 ms (n=20), observation_time to committed `observation_events` row, timed directly against the real running platform's Postgres (the same measurement `ingestion_ingest_latency` now records as a metric - P10.03 wires it to a live Prometheus). One process, one event at a time, no concurrent load - not P11.06's nominal-load soak test. `docs/evidence/p10_02_slos.json` |
| LAT-03 | Safety candidate to correlated incident P95 < 5 s | From last contributing evidence event to incident record creation, nominal load | P06.08 | **Met** on TEST: P95 = 0.0 s, n=7 (event-driven correlation; a periodic-poll deployment must trigger on candidate write, not a timer - see `docs/evidence/p06_08_acceptance.json`) |
| LAT-04 | Command approval-to-adapter-acknowledgement P95 < 3 s | From operator approval to simulator adapter ack, excluding human approval wait time | P07.09 | **Met** on TEST: warm live adapter session P95 = 0.057 s (n=24). Cold one-container-per-action path (P07.06) P95 = 2.70 s (n=5, thin margin, dominated by container start) - see `docs/evidence/p07_09_outcomes.json` |
| LAT-05 | Operator UI live update P95 < 2 s | WebSocket/SSE push from backend event to rendered UI change, nominal load | P08.10 | **Met** (P08.10, TEST stack, nominal load = one session): P95 = 0.99 s, median 0.67 s, max 1.13 s over n=30 real detector readings written through the platform's own ingestion and timed from the event's observation time to the device's new reading rendered in the map's accessible list in real Chrome over the live WebSocket (so ingestion, storage, the push, browser batching and rendering are all inside the number; the client flushes live events every 250 ms and the server polls every 0.5 s). Simulated data, one machine. Five attempts were needed: two ran while Postgres was in crash recovery (a backend aborted on a glibc malloc assertion, twice; the workers reconnected each time), two failed on the first reading not rendering within 15 s while the host disk was stalling (checkpoint writes of 38-74 s in the same period; cause not isolated), the fifth ran clean and is the one reported, with its raw samples. See `docs/evidence/p08_10_ui_acceptance.json` |
| LAT-06 | AIOps detection-to-remediation-start P95 < 30 s | From platform-incident correlation to bounded remediation action dispatch | P10.09 | Not yet measured |

## Load and throughput

| ID | Target | Conditions | Measured by | Result |
|---|---|---|---|---|
| LOAD-01 | Sustain nominal event ingest with zero loss/duplication | Nominal load = 12 intersections x 3 corridors telemetry rate defined in P03.02/P03.03; measured over a 30-minute soak | P11.06 | Not yet measured |
| LOAD-02 | Sustain 2x nominal load with graceful backpressure, no data loss | Backpressure = bounded queueing/shedding per `docs/environment/RESOURCE_BUDGET.md`, not silent drop | P11.06 | Not yet measured |
| LOAD-03 | Identify a measured ceiling load and safe degraded mode at 5x nominal | Ceiling replaces this planning assumption once measured; degraded mode must not violate SAFE-01/SAFE-02 below | P11.06 | Not yet measured |
| LOAD-04 | Concurrent operator sessions: 10 without measurable UI latency regression (LAT-05) | Ten simulated concurrent authenticated sessions across roles | P08.10, P11.06 | **Met** (P08.10): 10 concurrent authenticated sessions across 7 roles (all seven; four of them holding the live map) - live-update P95 1.02 s vs 0.99 s alone (+33 ms; allowance 250 ms), n=30; screen data requests P95 687 ms vs 333 ms alone (about twice as slow, still under 0.7 s; the host disk was saturated at the time). Limits: ten sessions is a light load for one API process (per-request database connections, no pool); this is not P11.06's ceiling test. `docs/evidence/p08_10_ui_acceptance.json` |

## Accuracy

| ID | Target | Conditions | Measured by | Result |
|---|---|---|---|---|
| ACC-01 | Trained traffic/safety edge model beats the transparent baseline (P04.02) on held-out data on at least one primary metric, with the comparison reported even if it does not | Disjoint held-out split per P03.08; metric set defined at P04.03, not invented here | P04.09 | Not yet measured |
| ACC-02 | Short-horizon traffic forecast (5/15/30 min) beats or is explicitly rejected against its baseline (P06.02) | Held-out runs, per-horizon reporting, uncertainty included | P06.08 | **Measured and reported per (horizon, target)**: 5-min volume and 15-min volume/density significantly better than baseline (accepted); 15-min travel time and every 30-min target significantly worse (baseline served instead) - `models/registry/traffic-forecast/training_report.json` |
| ACC-03 | Congestion/spillback detection: report precision and recall separately per scenario class; no single blended "accuracy" number | Held-out scenario set from P03.05 | P06.08 | **Measured per class, unblended**: am-peak-blockage precision 1.00/recall 0.37, am-peak precision 1.00/recall 0.14, pm-double/midday 0 false alarms; stall fusion precision 0.38/recall 0.83; VRU conflict event precision 0.14/recall 0.13 - `docs/evidence/p06_08_acceptance.json` |
| ACC-04 | Operational anomaly detector: report precision and recall separately against normal/degraded labeled runs (P10.05) | Disjoint normal/degraded split, no leakage | P10.09 | Not yet measured |

No accuracy target implies a field-accuracy claim; all figures are synthetic-data results only, per `docs/PROJECT_CONTEXT.md`.

## False alarm

| ID | Target | Conditions | Measured by | Result |
|---|---|---|---|---|
| FA-01 | Traffic/safety incident false-positive rate <= 10% of raised incidents on the held-out scenario set | Counted after correlation/deduplication (P06.07), not raw per-detector alerts | P06.08 | **Met** on TEST: 0/7 = 0.0% (Wilson 95% 0.0-0.35). Small sample (2 blockage-seed runs) - read as "no false positives observed," not a tight bound - `docs/evidence/p06_08_incident_evaluation.json` |
| FA-02 | Data-quality incident false-positive rate <= 5% on held-out fault scenarios (P03.06) | Missing/stuck/drift/duplicate/out-of-order fault classes from the catalog | P06.08, **P10.07** | **Not measurable yet, honestly**: no data-quality incident detector exists. P10.07 builds it from OpenTelemetry/health signals; P03.06's fault catalog is ready as its held-out set. Carried forward, not fabricated. |
| FA-03 | AIOps platform-incident false-positive rate <= 10% on held-out normal/degraded runs | Measured against P10.05 datasets, after correlation (P10.07) | P10.09 | Not yet measured |

## Emergency ETA

| ID | Target | Conditions | Measured by | Result |
|---|---|---|---|---|
| ETA-01 | Predicted route ETA mean absolute error <= 15% of simulated actual travel time | Across the three mandatory scenarios (ambulance, fire, police), normal traffic | P07.10 | **Met** on TEST: pooled MAE = 8.4% (n=15, 5 trials x 3 scenarios), worst single trial 20.71%. Per scenario: ambulance 3.65%, fire 6.91%, police 14.63% - see `docs/evidence/p07_10_scenarios.json` |
| ETA-02 | Predicted route ETA mean absolute error <= 25% of simulated actual travel time under an active traffic/safety incident | Same scenarios, at least one concurrent incident condition | P07.10 | **Met** on TEST: pooled MAE = 10.55% (n=15) under a real collision incident that closes a segment and forces a real reroute. Per scenario: ambulance 9.91%, fire 8.0%, police 13.73% - `docs/evidence/p07_10_scenarios.json` |
| ETA-03 | Green-corridor/pre-emption measurably reduces simulated emergency travel time versus the same route without pre-emption | Paired same-seed comparison run, both timed | P07.10 | **Met** on TEST: 30 paired dispatches (normal + incident, 3 scenarios), mean reduction 8.13s (6.1%), 19 improved / 9 tied / 2 worse, one-sided sign test p=0.0001 (not explained by chance). Worst single pair -5s, within the outcome-verification safety tolerance. `docs/evidence/p07_10_scenarios.json` |

## Safety

| ID | Target | Conditions | Measured by | Result |
|---|---|---|---|---|
| SAFE-01 | Zero pedestrian-clearance or conflicting-movement violations across all scenario and fault runs | Verified against `signal-state/v1` conflict_group and clearance timing, every P03/P07 scenario replay | P07.10, P12.02 | **Met** on TEST: 0 violations over 162,072 real intersection-state observations (every controlled intersection, every simulated second, across all normal/incident/heavy-congestion/failure runs), judged by an independent runtime monitor (`simulator/control_adapters/signal_safety.py`) derived from the network's own compiled design, not the control mechanism's own bookkeeping - the same monitor caught a real unsafe defect in the pre-emption mechanism's first version during P07.10's own development (see P07.07). `docs/evidence/p07_10_scenarios.json` |
| SAFE-02 | 100% of protected actions (P07.05-P07.06) that lack current evidence, valid policy decision, or fresh state are denied, never executed | Negative test suite: stale/disconnected/unauthorized/expired command attempts | P09.10, P12.02 | Not yet measured |
| SAFE-03 | 100% of stale network-state or signal-state records (`freshness_status = stale`, per `source-code/contracts/`) trigger fail-safe behavior in dependent services, never silent use as fresh | Fault-injection tests per data-quality incident catalog | P06.08, **P07.05**, P12.02 | **Partial**: P05.06/07 prove `freshness_status` correctly flips to `stale` past budget and the API reports it; no dependent service yet refuses to act on it - that gate is P07.05's command policy. Carried forward. |

## Recovery

| ID | Target | Conditions | Measured by | Result |
|---|---|---|---|---|
| REC-01 | Edge outage recovery: 100% of durably buffered events replay in order, acknowledged, with zero accepted duplicates | Crash/restart and uplink-loss fault tests, per P04.07 | P12.02 | Not yet measured |
| REC-02 | Central service restart: observed state reconciled before new commands are accepted, 100% of restart tests | Per the availability invariant in `docs/REFERENCE_ARCHITECTURE.md` section 6 | P11.05, P12.02 | Not yet measured |
| REC-03 | Backup/restore: data, control and audit integrity preserved across at least one full restore drill | Measured RPO/RTO recorded, not assumed | P11.05 | Not yet measured |
| REC-04 | AIOps remediation achieves sustained recovery (independent post-window health) or a documented safe escalation, never a silent unresolved state | Controlled fault injection per P10.09 | P10.09, P12.02 | Not yet measured |

## UX and accessibility

| ID | Target | Conditions | Measured by | Result |
|---|---|---|---|---|
| UX-01 | WCAG 2.2 Level AA on keyboard navigation, contrast, and reduced-motion for all mandatory operator/dispatcher workflows | Manual and automated accessibility check per role journey (P08.01) | P08.10 | **Met on the tested scope** (P08.10): all screens each of the 7 roles may open (87 role-screen visits, detail screens opened from real rows) pass axe WCAG 2.0/2.1/2.2 A and AA with 0 violations, are reachable by keyboard from the skip link with a visible focus indicator on every stop and no trap, and no animation or transition over 50 ms runs on any of 15 screens under reduced motion (a control probe shows the check does see motion without the preference). Contrast is computed, not asserted (P08.03: 30 pairs, lowest 3.19:1 for non-text). Limits: automated checks cover part of WCAG; no manual screen-reader pass (NVDA/JAWS/VoiceOver) was done and none is claimed. `docs/evidence/p08_10_ui_acceptance.json` |
| UX-02 | Every displayed value shows its `truth_label` and `freshness_status`/staleness distinctly; zero instances of stale data rendered as live in review | Design/accessibility acceptance pass across all live views | P08.10 | **Met on the tested scope** (P08.04-P08.10): truth label and freshness beside every value on the live views - map elements (selection panel and the accessible list carry status, freshness and truth as columns), analytics windows, device health, calls, incidents, outcomes (`verified`, and `Recorded` for seeded histories) - and ages come from the clock, so a screen cannot go on looking live: with the API down and thirty minutes passing, 0 badges still say Fresh and every one says Stale with its observation time (`e2e/failures.spec.ts`); an unknown value says so and is never zero. Limit: the review is the automated pass across the built screens, not an external design review. `docs/evidence/p08_10_ui_acceptance.json`, `p08_05_ui_map.json`, `p08_06_ui_analytics.json` |
| UX-03 | Critical action (approve/execute/override) requires an explicit confirmation step and shows denied/pending/executing/failed/rollback states distinctly, 100% of tested action paths | P08.08 view acceptance | P08.10 | **Met on the tested scope** (P08.07-P08.10): every UI write is listed and classified by a test that fails when a new one is added unclassified (`src/config/writes.test.ts`): 14 write paths, 8 behind a confirmation that restates what will happen and defaults focus to Cancel (request a command directly or from a recommendation, approve or deny, cancel a call, resolve an incident, write and acknowledge a handover, start/replay/reset a demo run), 1 data-entry form (a new call) and 5 low-risk direct writes with a stated reason (assign a unit, record a unit's status, choose a route alternative, add a note, change an incident owner). Approve/execute/override: approval is confirmed; execution is never a person; override is not in this UI (P09.05). Denied, pending, executing, failed, rolled back, expired and policy-unavailable are each a distinct shape and word; a decision cannot be submitted twice; the answer shown is the server's. Limit: override and pre-emption approval by an incident commander are not exercised because they are not in this UI. `docs/evidence/p08_07_ui_incident_dispatch.json`, `p08_08_ui_actions.json`, `p08_09_ui_govern.json` |
| UX-04 | Responsive layout (laptop and projector widths) with no clipped or unreachable critical control in either layout | P08.02 wireframe plus P08.10 acceptance | P08.10 | **Met** (P08.10): every screen a role may open at 1366x768, 1920x1080, a 390x844 phone and 200% (640x512) and 400% (320x256) zoom - 85 checks - has no sideways page scroll and no visible content outside the viewport that is not in its own scroll container; the navigation collapses to a keyboard-operable menu; the longest confirmation dialogs keep their controls reachable at 320x256. The 400% check found a real defect (a sticky header and a fixed bottom bar covered the whole viewport, so a button could not be clicked), fixed in P08.10. Limit: browser-window emulation of zoom, not a physical projector or phone. `docs/evidence/p08_10_ui_acceptance.json` |

## Revision policy

A target may be revised when measured environment capacity
(`docs/environment/RESOURCE_BUDGET.md`, updated by P11.06), a supplied
target host's real characteristics, or an assessor clarification makes it
provably unreachable or provably too weak. Each revision appends a dated
entry below rather than editing the original target's history away.

- 2026-09-18: initial target set established (P02.01), no revisions yet.
