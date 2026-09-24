# Traffic intelligence (Phase 06)

| Module | Task | What it does |
|---|---|---|
| `topology.py`, `kpis.py`, `calibration.py`, `kpi_service.py`, `truth_validation.py` | P06.01 | Corridor KPIs from loop telemetry, validated against SUMO ground truth |
| `forecast_data.py`, `forecast_baselines.py`, `evaluate_baselines.py` | P06.02 | 5/15/30-minute forecast baselines with conformal intervals, held-out evaluation |

Shared input: the Phase 06 SUMO dataset, `source-code/models/intelligence_dataset/`.

## P06.01 - corridor KPIs

`compute_corridor_kpis(events, segments, geometry_version)` is a pure function
over `traffic.loop_detector.count` events; the dataset files and the platform
(`kpi_service` reading `observation_events` back out of Postgres) run the same
code, so definitions cannot drift between training and serving. Per corridor,
direction and 5-minute window it produces volume, space-mean speed, density,
a queue indicator, travel time, free-flow travel time, delay, throughput and,
over the trailing hour, travel-time/planning-time/buffer indices. Each record
is served as a `contracts/network-state/v1` corridor record; freshness is
computed at read time (a historical window is `stale` through the API, never
`fresh`), and quality drops to `suspect`/`invalid` when segments stop
reporting instead of quietly reusing free-flow values.

**Segment topology.** `network_segments` (migration 0012, seeded from the real
net file) closes P05.06's `segment` gap; `compute_state('segment', edge_id)` now
works.

**Lane share (the one fitted parameter).** A loop instruments one of each edge's
two general lanes. Scaling by 1/2 assumed an even lane split and left volume
biased about -35%; measured lane share is 0.18-0.63 by segment. It is fitted on
the TRAIN split only (`calibration.py` -> `artifacts/lane_share.json`, with the
dataset hash) and evaluated on validation/test.

**Measured accuracy vs SUMO edge-wide truth (validation + test pooled, 3,456
corridor-windows; gates were fixed after the first measurement at round numbers
with headroom, not tuned in a loop):**

| KPI | MAPE | bias | Pearson r | gate |
|---|---|---|---|---|
| volume | 23.7% | +7 veh/h (1.6% of mean) | 0.96 | bias <=5%, MAPE <=30%, r >=0.90 |
| density | 25.5% | +0.8 veh/km | 0.85 | MAPE <=35%, r >=0.70 |
| travel time | 7.3% | -3.6 s | 0.81 | MAPE <=12% |
| speed | 8.5% | +0.69 m/s | 0.47 | MAPE <=15% |
| throughput | 33.9% | +11.5 veh/h | 0.92 | r >=0.85 |

**Known limits, reported not gated:** in the 51 *congested* held-out windows
(truth speed < 70% of free flow) travel-time MAPE is 41% and speed is
overestimated - a point loop 10 m past the upstream stop line reflects
discharge speed, not the queue behind it. The queue indicator (queue reaching
the loop) is a weak proxy (Spearman ~0.1-0.2 against SUMO's waiting vehicles)
because a queue is invisible until it grows back to the loop. Downstream
detectors (P06.04) therefore must not treat loop speed alone as ground truth
about congestion.

### Reproduction

```bash
bash source-code/models/intelligence_dataset/run_container.sh   # once
python source-code/backend/analytics/calibration.py             # writes the lane-share artifact
python source-code/database/seeds/seed_segments.py
python source-code/backend/analytics/verify_kpis.py             # real Postgres + API + truth gates
```

`verify_kpis.py` ingests a held-out run through P05.05's real write path
(6,480 events), proves the DB-backed KPIs equal the file-computed ones and that
recomputation is idempotent, applies the truth gates to the held-out splits,
validates every KPI as `network-state/v1`, checks the missing-segment and
duplicate-event behaviour, and exercises the new `GET /api/v1/kpis/corridors`
endpoint. Evidence: `docs/evidence/p06_01_kpis.json`.

## P06.02 - forecast baselines

Forecast targets are the KPI values the platform itself will compute `h`
windows later (corridor volume, density, travel time), so a forecast is judged
against what an operator will see, not an oracle. Samples are built per run,
corridor and direction: features use windows `<= t`, the target is window
`t + h` (5/15/30 minutes = 1/3/6 five-minute windows); `forecast_data.py`'s
tests prove no future window leaks into a feature.

Four transparent baselines: persistence, 3-window moving average, damped-free
linear trend, and a time-of-day mean fitted on TRAIN runs only. Per (horizon,
target) each is scored on VALIDATION, the best becomes *the* baseline P06.03
must beat, and a split-conformal 80% interval is calibrated on that baseline's
validation residuals. TEST is then scored once (logged in
`models/registry/test_split_ledger.json` as a non-selecting `baseline_report`).

Held-out (test, 8 runs, 1,344-1,536 samples per horizon):

| Horizon | Target | Selected baseline | Test MAE | Relative MAE | 80% interval coverage |
|---|---|---|---|---|---|
| 5 min | volume | persistence | 161 veh/h | 31% | 0.81 |
| 15 min | volume | persistence | 264 veh/h | 50% | 0.79 |
| 30 min | volume | time-of-day mean | 257 veh/h | 49% | 0.78 |
| 5/15/30 min | density | persistence / persistence / time-of-day | 4.9 / 8.7 / 8.3 veh/km | 39% / 68% / 64% | 0.80 / 0.79 / 0.79 |
| 5/15/30 min | travel time | time-of-day mean | 3.5-3.9 s | 6% | 0.80-0.81 |

Failure mode recorded, not averaged away: persistence's volume MAE is 58 veh/h
on steady windows but 218-324 veh/h on windows where volume changes by 20% or
more over the horizon (the demand ramps) - that is exactly where a learned
forecaster has to earn its keep. Loop counts scaled to the segment are noisy
at five-minute resolution, which bounds any forecaster's accuracy on volume.

`python source-code/backend/analytics/evaluate_baselines.py` regenerates
`models/registry/traffic-forecast/baseline_evaluation.json` (copied to
`docs/evidence/p06_02_baselines.json`).

## P06.03 - trained forecast model

`train_forecast.py` fits 7 candidates per (horizon, target) (ridge and
gradient-boosted trees predicting the *residual over persistence*, 24 lag/
delta/network-context features) on TRAIN, compares each to that pair's P06.02
baseline on VALIDATION, and accepts the model only if it beats the baseline
there by >= 3% MAE - decided before TEST is opened. Split-conformal 80%
intervals are calibrated on validation residuals. TEST is opened **once**
(`test_split_ledger.json` refuses a second `final_comparison`; verified),
and every pair is scored whichever way it goes, including the ones the model
was rejected for.

Held-out TEST (8 runs, 9 pairs) - MAE model vs baseline, run-level bootstrap 95% CI
of the difference:

| Horizon | Target | Validation decision | Model / baseline MAE | Verdict on TEST | 80% interval coverage |
|---|---|---|---|---|---|
| 5 min | volume | accepted (+21.9%) | 136.6 / 161.2 veh/h | **better** (CI -30.8..-19.4) | 0.80 |
| 5 min | density | accepted (+13.5%) | 4.67 / 4.87 | no significant difference | 0.77 |
| 15 min | volume | accepted (+33.7%) | 186.0 / 264.0 veh/h | **better** (CI -113.7..-43.9) | 0.76 |
| 15 min | density | accepted (+21.5%) | 8.07 / 8.65 | **better** (CI -1.02..-0.17) | 0.75 |
| 30 min | volume | accepted (+20.0%) | 233.0 / 257.1 veh/h | no significant difference (CI -52.7..+8.0) | 0.78 |
| 30 min | density | accepted (+9.2%) | 8.87 / 8.31 | **worse** (CI +0.17..+1.01) | 0.71 |
| 5/15/30 min | travel time | rejected (-2..-19%) | 3.5/4.2/4.5 vs 3.5/3.6/3.9 s | baseline served; model no better or worse | 0.79-0.80 (baseline) |

Recorded failure modes, not averaged away:
- **A validation-accepted model can lose on TEST**: 30-minute density was
  accepted on validation (+9.2%) and is significantly *worse* than its baseline
  on TEST. The pre-registered rule is not changed after seeing TEST (that would
  leak it into selection); the finding is in the model card and the next
  version should require the margin on both validation seeds.
- Interval coverage falls below nominal (0.71-0.80) as the horizon grows:
  calibration and selection share the validation runs and demand ramps break
  exchangeability.
- Travel time is not worth learning here: congestion is rare, so free-flow
  persistence/time-of-day already wins; the package serves those baselines and
  labels them (`traffic-forecast-baseline:<name>`).
- **Drift**: train-vs-test PSI stays below 0.1 for every feature (no covariate
  shift among these synthetic demand shapes). A trailing-12-window relative-
  error monitor flags 11/48 series with no drift (23% false alarms) and 26/48
  when an unanticipated 1.4x demand step is injected - a coarse alarm, not a
  precise detector; a false alarm falls back to the baseline, the safe
  direction.

**Serving** (`forecast_service.py`, `GET /api/v1/forecasts/corridors`): stored
KPIs in, `forecast/v1` records out (model records name their `baseline_id`;
baseline-served targets are separate records so a record never mixes sources).
It abstains rather than forecast from stale inputs, a gap or `invalid` window in
the last six windows, an incomplete network context, an origin before the trained
range, or a horizon past the 09:00-12:00 timeline the model was trained on. The
package is loaded only after its sha256 matches the manifest.

`verify_forecasts.py` (real Postgres, 27 checks, evidence
`docs/evidence/p06_03_forecast_model.json`) proves serving equals offline
evaluation to 5e-5 (no train/serve skew), all 144 emitted forecasts are valid
`forecast/v1`, storage is idempotent, and the API serves them `predicted`.

## P06.04 - congestion and spillback detection

`congestion.py` (rules, ADR-0005 baseline first): a segment is congested while
its loop shows occupancy >= `occ_on` with speed <= `speed_on` for `n_on`
consecutive 30 s intervals; the episode clears after `n_off` clearly free
intervals or a data gap. Each episode carries its location (segment, corridor,
direction), onset, `detected_at` (the moment it was first confirmed), clear
time, severity and the ids of the loop events that triggered it. Spillback is
queue crossing a junction: *observed* when the upstream segment's episode starts
0-180 s after its downstream neighbour's while it is active, *inferred* when one
episode persists (>= `min_duration_s` at >= `min_peak_occupancy`) long enough
that its queue must have outgrown the segment - the loop 10 m past the upstream
stop line cannot see that queue directly. Output goes to `detection_candidates`
(migration 0014, `GET /api/v1/candidates`): evidence-backed, `inferred`, **not
incidents** (that is P06.07).

**Truth** is SUMO's own edge-wide standing queue (waiting vehicles x 3.5 m/lane
>= 30 m for >= 2 intervals), never seen by the detector; truth spillback applies
the same propagation rule to truth episodes. 18 congestion settings were scored
on TRAIN and VALIDATION and 12 spillback settings likewise; validation F1 picked
`occ_on 0.2, speed_on 4 m/s, n_on 3` and `min_duration 120 s, min_peak_occ 0.4`.
Per-severity precision on validation is the candidate `confidence` (0.75: too few
detections per severity to calibrate finer). TEST was scored once
(`detector_report` in the test-split ledger). Precision and recall are reported
separately per scenario class (ACC-03):

| Held-out class (2 runs each) | Truth episodes | Detected | Precision (Wilson 95%) | Recall (95%) | Median detection delay |
|---|---|---|---|---|---|
| am-peak-blockage | 19 | 7 | 1.00 (0.65-1.00) | 0.37 (0.19-0.59) | 90 s |
| am-peak | 7 | 1 | 1.00 (0.21-1.00) | 0.14 (0.03-0.51) | 450 s |
| pm-double, midday-steady | 0 | 0 | no false alarms | - | - |

Spillback on the blockage class: 4 truth, 4 detected, 3 matched (precision
0.75, recall 0.75; n is tiny). Severity agreement with the truth queue tier is
poor (within one level for 29% of matched blockage episodes): occupancy/duration
and queue length are different quantities and the mapping is uncalibrated -
reported, not tuned away.

What the numbers mean: precision is high and false alarms are absent, but
recall is **bounded by physics, not tuning** - most truth episodes are queues at
a segment's downstream end that never reach the loop (recall by truth
severity is in the evaluation JSON). Fixed on the way: `detected_at` had been
overwritten on every congested interval, inflating detection delay (240 s -> 90 s
after the fix; matches and severities were unaffected).

`verify_congestion.py` (real Postgres, 18 checks): a held-out blockage run goes
through real ingestion; the DB-backed detector equals the file-based one (after
deduplicating repeated intervals); every candidate cites real event ids on its
own segment inside its window; an episode still open on partial data is closed
*in place* (same `candidate_id`) when the rest arrives; held-out metrics
reproduce exactly from the stored parameters; the P03 normal run raises no
congestion. Evidence: `docs/evidence/p06_04_congestion.json`. The verify scripts
share one DB timeline: `reset_simulated_loops` clears the previous simulated
run first (two runs at identical timestamps would interleave two histories).

## P06.05 - stalled-vehicle, collision, wrong-way, flooding and low-visibility candidates

Two different kinds of detector, with different honest claims:

**Stalled vehicle** (`stall_candidates.py`, `evaluate_stall.py`) is a real
detector on real physics. P04's ONNX road-blockage model runs inside the real
`EdgeRuntime` (validation -> features -> inference -> alarm/abstain) over loop
telemetry; consecutive alarms on one segment form a span; a span becomes a
candidate after `k_min` alarms and is corroborated when P06.04's congestion
episode overlaps it (noisy-OR; the per-source parts stay in
`attributes.sources`, so uncertainty is inspectable and not collapsed into one
number). An edge **abstain never raises a candidate**. Truth is SUMO's measured
stop-output blockage windows. Nine fusion settings were scored on TRAIN and
VALIDATION; validation F1 picked `k_min 3`, no mandatory corroboration. TEST was
scored once (`stall_report` in the ledger; an adjacent-segment precision figure
was added afterwards, TEST parameters and candidates unchanged, and this is
recorded in the evaluation JSON):

| Held-out TEST (6 measured blockages, 18 incident-free hours) | Value |
|---|---|
| Recall | 5/6 = 0.83 (Wilson 95%: 0.44-0.97) |
| Precision (candidate level) | 5/13 = 0.38 (0.18-0.65) |
| False alarms per incident-free hour | 0.17 (3 in 18 h) |
| Detection delay, median / p90 | 139 s / 145 s |
| Recall by blockage distance from the loop | 25 m: 2/2, 45 m: 3/3, 70 m: 0/1 |

Precision is the weak number and is reported as such: 13 candidates, 5 matched.
It is a **candidate-level** figure - P06.07 correlates duplicates and gates on
corroboration before anything reaches an operator, and P06.08 measures the
incident-level false-alarm rate against the FA-01 target. The edge model was
trained on 900 s uniform-demand runs; these are 180 min peak-shaped runs, a real
distribution shift. Only 33 measured blockages exist across all splits.

**Collision, wrong-way, flooding, low visibility** (`overlay_candidates.py`,
`evaluate_overlays.py`) cannot be simulated by SUMO
(`docs/evidence/SIMULATION_LIMITATIONS.md`), so P03.05 overlays their sensor
signatures. They are event-driven state machines over explicit flags, and the
evaluation is a **specification test**, not a skill claim - no thresholds were
fitted, so no validation/test selection applies. What is exercised is the
handling a platform needs around a flag: quality gating (an `invalid` reading
never raises a candidate), a confidence floor, deduplication (a repeated flag
extends the open candidate), corroboration by an independent source (a
congestion episode near a collision flag; friction with a flood flag; negative
speed with a wrong-way flag), contradiction (flood flag over dry-road friction
halves confidence) and a stuck-sensor guard (a flag held past 30 min with no
corroboration is marked `suspect_stuck` and demoted below 0.5). Results:
216/216 seeded injections (2 seeds per split, 6 per kind) detected with exact
onset and clear, 0 candidates from 270 hard negatives (sub-threshold visibility,
flood-free friction dips, invalid-quality flags, flag flicker), 0 from P03's
normal run (1,453 events) and 0 from the fault streams (126 events). Signal
faults are deliberately **not** safety candidates: they are device faults and
belong to data-quality handling.

`safety_service.py` rebuilds full observation-envelope events from
`observation_events` (`fetch_envelopes`), so the identical detector runs over
files and over the database; both write `detection_candidates`.
`verify_safety_candidates.py` (real Postgres, 19 checks, evidence
`docs/evidence/p06_05_safety.json`): the held-out blockage run and P03.05's
overlay events go through P05.05's real ingestion; DB-path stall candidates equal
file-path candidates; the candidates match 3 of 3 measured blockages in that run;
every candidate cites event ids that exist; re-running is idempotent (12 -> 12);
held-out metrics reproduce exactly from the stored parameters; `/api/v1/candidates`
serves every kind, labelled `inferred`.

## P06.06 - pedestrian/cyclist conflict indicators

`edge/vru_conflict.py` (edge), `backend/analytics/{vru_data,evaluate_vru,vru_service,verify_vru_conflicts}.py`,
`models/vru_dataset/` (dataset + realized-PET truth), `models/registry/vru-conflict/evaluation.json`.

**Data.** No earlier dataset could contain a vehicle-VRU conflict: P03.02's pedestrians walk single sidewalk
edges and never cross a road, and the P04/P06.01 runs carry no VRUs. `models/vru_dataset` runs SUMO with
pedestrians routed between random sidewalk edges (so they use the guessed crossings), cyclists in the bike
lanes and passenger vehicles, 18 runs (P03.08 seed split; 30 min at 0.25 s; a peak and an off-peak run per
seed), keeping only trajectory rows within 40 m of an intersection. A twin rebuild is byte-identical
(dataset sha256 `db4064a0...`). Modelling assumptions, stated: 25% of drivers are "assertive"
(`jmCrossingGap 1 m`), because SUMO's default driver always leaves a 10 m gap and would give almost nothing
to detect; 25% of pedestrians walk at 0.8 m/s.

**Truth** is *realized* post-encroachment time (PET): both actual paths cross, and PET is the gap between the
two users' crossing times, from the clean 4 Hz trajectories. A **conflict** is PET <= 3 s *and* a vehicle at
least 4 m/s at the crossing point. That risk weighting was fixed after inspecting the validation PET/speed
distribution, because at PET <= 3 s alone the large majority of interactions are vehicles crawling through
queues (routine yield-and-go); the plain-PET figures are reported as well. Paths that never cross (walking
beside a passing car) are not conflicts however close.

**Indicator.** The detector sees only what an edge tracker would: 1 Hz frames, 0.4 m Gaussian position noise,
5% missed detections, anonymous local track ids. Per frame and (VRU, vehicle) pair it projects both objects
forward (vehicle on a constant-turn-rate arc), and if the paths cross computes the predicted PET. An 11-feature
logistic `PairScorer` (stored as JSON, arithmetic only) turns that geometry into a probability that the
encounter really ends in a conflict. Raw geometry alone had precision 3-4% on validation; the scorer is
what makes it usable at all. Selection: 24 raw settings and 6 scorer thresholds scored on TRAIN and
VALIDATION, best validation event F1 wins (`scorer@0.1`), TEST scored once (`conflict_report`, non-selecting).

| Held-out TEST (106 risk-weighted conflicts, 100 alerts) | Value (Wilson 95%) |
|---|---|
| Event precision | 14/100 = 0.14 (0.09-0.22) |
| Event recall | 14/106 = 0.13 (0.08-0.21) |
| Plain PET <= 3 s truth: precision / recall | 0.30 / 0.08 (360 interactions) |
| Alerted before the first user reached the crossing point | 50% of matches, median lead ~0 s |
| Site x 5-min window level (what actually leaves the edge) | Spearman 0.41, Pearson 0.47; 28 of 58 true-conflict windows flagged, 28 flagged windows had none (window precision 0.50) |
| Pedestrian / cyclist | pedestrian only; cyclist: 968 site visits, 0 alerts, 0 real interactions |

**Read this as a weak, site-level risk indicator, not an alarm.** A gradient-boosting model on the same
features scores the same average precision (0.141 vs 0.143 for the logistic; base rate 0.027): the limit is the
information in 1 Hz tracks, not the model. The dominant failure is that a track cannot say whether a waiting
pedestrian will step out or a driver will yield. Bias and robustness found on TEST: off-peak recall 0/12 (few
events, sparse tracks); slow pedestrians 0/6 (CI 0-0.39; small n but the direction is the concern); recall 25%
when the vehicle reaches the point first vs 10% when the pedestrian does; recall barely moves with vehicle
speed. Noise makes it worse in a specific way: at a position noise of 0.8 m the detector emits 328
alerts (precision 0.06) and at 1.2 m 693 (0.023) - a false-alarm flood, not just lower recall; 30% missed
detections cut recall to 0.07 and a 2 s frame period to 0.08.

**Cyclists are not validated.** SUMO's separated bike lanes keep cyclists >= 2.35 m from every vehicle and no
cyclist path ever crosses a vehicle path, so this world contains no cyclist-vehicle conflict to score. The code
handles cyclists identically, but cyclist candidates are off (`cyclist_mode_validated: false`) and a cyclist
claim needs real data (Phase 12 pilot).

**Privacy structure** (pinned by 14 unit tests and 21 checks against the real platform): a track inside a
privacy zone is dropped before any computation; ids are HMAC-rehashed every 5-min window from a salt that is
never exported (results are identical under two different salts); a (site, window) is exported only when at
least 3 distinct pedestrians were seen in it, and what is exported is counts and one minimum predicted PET -
no track, position, time finer than the window or individual speed exists in any event, candidate or table.
The cost, measured not assumed: the floor suppressed 21 of 285 TEST windows (all off-peak) and none of the
106 true conflicts fell in them (1 of 98 in validation) - but it *would* hide a lone pedestrian's near miss at a
quiet site, and lowering it is a GRC decision, not a tuning knob. A privacy zone at a crossing likewise makes
conflicts inside it invisible (`verify_vru_conflicts.py`: 1,474 samples dropped at 3 zones).

**Platform path.** Only aggregates leave the edge (`vru.conflict.aggregate` / `vru.conflict.window_suppressed`,
envelope-valid, `truth_label: inferred`, `privacy_classification: aggregated`, short retention), through P05.05's
real ingestion; `pedestrian_conflict` candidates are derived per (intersection, window) with the validated
window-level precision (0.50) as their confidence and the basis stored beside it; re-ingesting and re-detecting
are idempotent. Evidence: `docs/evidence/p06_06_vru_conflicts.json`.

## P06.07 - correlating candidates into incidents

`correlation.py` (pure rules), `incident_service.py` (one idempotent correlation tick, `sync(conn, now)`),
`calibrate_incident_policy.py` -> `artifacts/incident_policy.json`, migration `0015` (`incident_candidates`,
`incident_hypotheses`, `incidents.duplicate_of / evidence_cleared_at / evidence_sources`),
`GET /api/v1/incidents[/{id}]`, `verify_incidents.py`.

**Grouping.** Candidates link when they overlap in time (300 s slack) *and* relate on the real network graph:
same kind on the same element or collision/stalled-vehicle and congestion/spillback on one segment (duplicate
sources); a cause (collision, wrong-way, stall, flooding, low visibility) with congestion/spillback on the same
or up to three segments *upstream* (queues grow against the flow: consequence); a collision or stall near an
intersection with a pedestrian conflict, or two different causes on one segment (related). Connected components
become incidents, typed by the highest-precedence member. Known limitation: components can chain (A-B, B-C
links make one incident even if A and C are only indirectly related); the alternative, cliques, would split
one physical queue into several incidents. An "active" candidate (no clear time) is only assumed alive as far
as its evidence goes (last evidence + slack), so a flag last seen hours ago cannot absorb a new event nearby; a
stalled-vehicle candidate, which never carries a clear time, is treated as cleared 120 s after its last alarm.

**Confidence is calibrated, not the detector's score.** The stall fusion emits noisy-OR values near 0.97 for
candidates whose measured precision is far lower, so each kind carries its detector's *empirical precision on
TRAIN+VALIDATION* (TEST stays untouched for P06.08): congestion 0.85 (11/13), spillback 0.50 (4/8), stalled
vehicle 0.63 corroborated (10/16) vs 0.46 alone (5/11), pedestrian conflict 0.50 (validation windows). Collision,
wrong-way, flooding and low visibility (0.8/0.7/0.7/0.8) are **assumed priors**: SUMO cannot produce them, the
detectors are specification-tested overlays, and no false-positive rate is measurable in simulation - replace them
before any real use. Only *independent sensor modalities* add up (same-modality members take the best, different
modalities combine noisy-OR): a stall candidate and a congestion episode both come from loop detectors, so they
corroborate nothing; a road-condition sensor plus a loop is genuinely two sources. An incident opens at 0.6, so
a single-source candidate whose measured precision is below that (spillback, an uncorroborated stall, a
pedestrian-conflict window) stays on a watch list - FA-01 asks for <= 10% false incidents. The threshold, slack and
hysteresis are design choices evaluated (not tuned on TEST) in P06.08.

**Hypotheses are not causes.** Each incident carries ranked hypotheses ("Stalled or disabled vehicle blocking a
lane on X; queue on Y (hypothesis, not verified)", or for congestion alone "demand-driven" vs "unobserved
downstream blockage") whose `likelihood` is only each alternative's share of the calibrated evidence.
`verified_cause` stays NULL until a person sets it; the API never returns one for a correlator incident.

**Lifecycle**, always through P05.08's state machine: open at the policy threshold; update as the group
changes; merge when a bridging candidate joins two incidents (older kept, the other resolved with `duplicate_of`);
escalate when a high-severity incident is unacknowledged for 600 s, at once when critical, or when a second
independent modality agrees at >= 0.85; resolve automatically only for `open`/`reopened` incidents once *all*
evidence has been clear for 120 s - an acknowledged/investigating/escalated incident only records
`evidence_cleared_at` and a person closes it; reopen on new evidence after resolution. Transitions carry the
timeline they were given (simulated time in replays), and `updated_at` never moves backwards.

`verify_incidents.py` (real Postgres, 33 checks): Part A replays the held-out blockage run's real detector
output in 5-minute ticks (21 candidates -> 7 incidents; 4 duplicate sources, 9 consequences; 1 merge, 2 reopens,
4 escalations, 5 auto-resolutions; no pair the rules connect left in different incidents; a re-sync at the same
time changes nothing, hash-verified). Part B uses *synthetic candidates, labelled `test:synthetic` and removed
afterwards* for what real data does not produce on demand: bridging merge, reopen, the three escalation rules,
an operator-owned incident that is never auto-resolved, the watch list. The API returns `incident/v1`-valid
records (schema-validated), cursor pagination, a detail view with candidates + relations + hypotheses +
transitions, 404 on unknown ids. Unit tests: `tests/test_correlation.py` (15). Evidence:
`docs/evidence/p06_07_incidents.json`.

## P06.08 - evaluate intelligence and incident outcomes

`evaluate_intelligence.py` (incident-level: detectors -> candidates -> correlation -> incidents, scored against
SUMO truth), `evaluate_p06_08.py` (assembles the acceptance report; cites, never recomputes, P06.02-P06.07's own
held-out numbers). Evidence: `docs/evidence/p06_08_acceptance.json`, `p06_08_incident_evaluation.json`.

An incident is a true positive when a member candidate relates - same event, duplicate source, or an upstream
consequence on the real segment graph, the same rules P06.07 uses live - to a truth event (SUMO's measured
blockage window or standing-queue episode); FA-01 is the share that are not. The opening policy (`open_confidence
0.6`, from P06.07's calibration) was fixed before TEST; TEST is scored once (`incident_report`, ledgered
non-selecting).

| Target | TEST result | Met | Caveat |
|---|---|---|---|
| **FA-01** incident false-positive rate <= 10% | 0/7 = 0.0% (Wilson 0.0-0.35) | yes | Only 7 incidents raised on TEST (2 blockage-seed runs): 0% is "no false positives observed on a small sample," not a tight bound. |
| **LAT-03** candidate-to-incident latency P95 < 5 s | P95 = 0.0 s (n=7) | yes, by design | Measured for **event-driven** correlation (`sync()` on every candidate write). `verify_incidents.py`'s 5-minute demo polling would itself violate this - deployment must trigger on write, not a timer. |
| **ACC-02** forecast beats/rejected vs baseline | see P06.03: 5-min volume/density and 15-min volume/density significantly better; 15-min travel time and every 30-min target significantly worse, baseline served instead | reported | cited from `models/registry/traffic-forecast/training_report.json`, not recomputed |
| **ACC-03** congestion/spillback precision/recall per class, never blended | am-peak-blockage: precision 1.00, recall 0.37; am-peak: precision 1.00, recall 0.14; pm-double/midday: no false alarms; stall fusion precision 0.38/recall 0.83; VRU event precision 0.14/recall 0.13 | reported honestly | cited from P06.04/05/06, per class |

Incident-level recall on TEST (informational, not a target): 5/6 measured blockages produce an incident that
correctly relates to them (0.83), 19/26 queue episodes (0.73) - bounded by the same physics P06.04/05 already
documented (loop placement, blockage distance).

**FA-02 (data-quality incident false-positive rate) is honestly reported as not measurable here**: no
data-quality incident detector exists yet. That is P10.07's job (correlating OpenTelemetry/health signals,
P10.01-P10.06), and P03.06's fault catalog (7 fault types, byte-identical ground truth,
`source-code/simulator/faults/`) is sitting ready as its held-out set - inventing a detector now just to close
this number would defeat the point of measuring it. **SAFE-03** is partially true today (P05.06/07 prove
`freshness_status` correctly flips to `stale` past the staleness budget) but nothing downstream yet *acts* on
it; that gate belongs to P07.05's command policy and is carried forward to P07.09/P12.02.
