# Feature Catalog

Priorities use four levels: **MVP**, **Release 2**, **Advanced**, and **Production
integration**. Advanced control remains simulation-only until a formal safety
case and authorized real infrastructure exist.

## 1. Network operations map

### MVP

- Live map with intersections, segments, lanes, crossings, signals, devices,
  emergency units, incidents, closures, weather hazards, and camera locations.
- Status layers for speed, volume, congestion, queues, signal health, sensor
  freshness, emergency routes, and current actions.
- Time scrubber for replay and comparison with the live state.
- Corridor, intersection, route, device, and incident detail panels.
- Visible `live`, `delayed`, `stale`, `simulated`, and `predicted` labels.
- Map/list synchronization and a non-map table alternative.

### Release 2 and advanced

- 3D or simplified digital-twin view where it adds operational value.
- Side-by-side baseline versus intervention comparison.
- Custom operator layers, saved views, geofences, and shared operational picture.
- Multi-city/agency tenancy with scoped layers and data-sharing agreements.

## 2. Traffic analytics and prediction

- Volume, vehicle class, speed distribution, density, occupancy, queue length,
  turning movements, travel time, delay, stops, throughput, and reliability.
- Congestion classification and bottleneck localization.
- 5-, 15-, and 30-minute forecasts with uncertainty bands.
- Queue spillback and gridlock risk.
- Origin-destination estimation from privacy-preserving aggregated data.
- Peak/event/weather demand forecasting.
- Route and corridor performance comparison.
- Before/after intervention and counterfactual simulation.
- Model explanation, missing-feature warnings, abstention, drift, and baseline
  comparison.

## 3. Safety analytics

- Collision or abrupt-stop detection from multiple sources.
- Stalled vehicle, wrong-way movement, debris, fire/smoke, flooding, ice, low
  visibility, and unsafe-speed events.
- Pedestrian/cyclist conflict and near-miss indicators using trajectories and
  time-to-collision measures.
- Red-light-running risk analytics without automated enforcement.
- School-zone, work-zone, tunnel, bridge, and event-zone monitoring.
- Hotspot analysis by location, time, road user, severity, and contributing
  condition.
- Evidence clip reference or metadata snapshot under strict access and retention
  controls.

## 4. Incident management

- Correlate sensor, model, emergency call, operator, and field reports into one
  incident rather than duplicate alerts.
- Severity, confidence, affected lanes/routes, impact radius, causal hypotheses,
  and evidence timeline.
- Acknowledge, assign, investigate, escalate, merge, split, resolve, and reopen.
- Playbooks by incident type with required checks and agency contacts.
- SLA timers, handover, shift notes, and cross-agency acknowledgement.
- Structured post-incident review and lessons/actions tracking.
- Link every action and public message to the triggering incident.

## 5. Emergency response coordination

### MVP

- Simulated CAD intake for ambulance, fire, and police incidents.
- Unit availability, capability, location, assignment, route, ETA, and status.
- Fastest-safe route considering traffic, closures, hazards, and vehicle limits.
- Dispatcher recommendation with alternative routes and confidence.
- Shared incident timeline between dispatch and traffic operations.

### Advanced

- Green-corridor calculation and signal pre-emption request.
- Multi-unit staging and route conflict avoidance.
- Hospital/facility selection using permitted capacity categories.
- Dynamic perimeter, evacuation, contraflow, and major-incident coordination.
- Responder mobile/PWA view with offline task and route cache.
- Handover to neighboring jurisdiction and mutual-aid workflows.

## 6. Traffic control and optimization

- Recommend signal timing changes based on queues, fairness, pedestrian demand,
  transit, and emergency priorities.
- Corridor coordination and green-wave plans.
- Transit signal priority with delay and schedule rules.
- Emergency pre-emption preserving yellow/all-red and pedestrian clearance.
- Diversion routes, lane closures, reversible-lane plans, ramp-meter suggestions,
  variable speed limits, and variable message signs.
- Simulation preview showing expected benefits and side effects before approval.
- Constraint engine for minimum greens, clearance, conflicts, maximum durations,
  protected movements, school/event rules, and fallback plans.
- Command acknowledgement, observed controller state, timeout, rollback, and
  verified traffic outcome.

## 7. AIOps and self-healing platform operations

- Unified metrics, logs, traces, topology, deployment, and configuration views.
- SLOs for event freshness, ingestion, API, maps, prediction, alerting, controller
  acknowledgement, and emergency updates.
- Detect device silence, stream lag, bad data, certificate expiry, clock drift,
  resource pressure, model latency/drift, failed integrations, and stale maps.
- Correlate failures by service, edge site, network path, dependency, and recent
  change.
- Policy-controlled actions: restart connector, fail over consumer, quarantine
  device, change sampling, roll back model/configuration, and scale service.
- Maintenance mode and alert suppression with expiry and audit.
- Verify recovery using independent health signals and sustained-success windows.
- Capacity forecasting and cost/resource optimization.

## 8. Operator experience

- Role-specific home views rather than one overloaded universal dashboard.
- Global command/search palette for roads, devices, incidents, units, and actions.
- Persistent critical incident queue and agency communication state.
- Keyboard-first triage, clear focus, screen-reader labels, and non-color status.
- Pause/resume real-time charts, data tables, update timestamps, and stale-state
  warnings.
- Streaming charts for recent activity, line charts with anomaly markers,
  forecast confidence bands, heatmaps with numeric legends, and sortable tables.
- Confirmation, reason, expected impact, policy result, progress, and explicit
  success/failure feedback for every command.
- Responsive field view; complex network control remains desktop/control-room
  optimized.
- Shift handover summary, bookmarks, saved filters, and personal workspace.

## 9. Public information and stakeholder reporting

- Sanitized incident, closure, roadwork, transit, parking, and travel-time feeds.
- Accessible public messages with channel-specific previews.
- Website/PWA, API, variable message sign, email/SMS/push adapter boundaries.
- Approval and expiry for public alerts; correction and retraction workflow.
- Executive dashboard for safety, reliability, response, equity, emissions, and
  platform health.
- Scheduled and incident-specific reports with provenance.

## 10. Security, governance, and privacy

- OIDC login, MFA readiness, role and attribute-based access, least privilege.
- Separate permissions for recommendation, request, approve, execute, override,
  and audit.
- mTLS/service identity, device certificates, certificate rotation, and topic or
  stream ACLs.
- Signed commands/configurations, secret manager integration, encryption, and
  immutable audit retention.
- Data classification, field-level redaction, configurable retention, legal hold,
  access review, and export/delete workflows where applicable.
- Privacy zones, edge anonymization, restricted video access, and no facial
  recognition in the baseline.
- Security-event correlation, break-glass access with enhanced audit, and regular
  policy tests.

## 11. Simulation, testing, and digital twin

- Seeded SUMO road network, demand profiles, vehicles, pedestrians, cyclists,
  public transport, weather, emergency units, and signal controllers.
- Scenarios: normal, peak, event surge, collision, stalled vehicle, wrong-way,
  flooding, poor visibility, signal failure, sensor fault, network outage, model
  degradation, and multi-incident emergency.
- Separate ground truth, immutable scenario manifest, and train/validation/test
  partitions.
- Scenario control API and operator console kept separate from production actions.
- Replay at real time or accelerated speed.
- Chaos/fault injection for services, streams, devices, networks, models, storage,
  identity, and policy dependencies.
- Load, soak, recovery, security, accessibility, browser, and disaster-recovery
  testing.

## 12. Optional differentiators

- Reinforcement-learning signal policy evaluated only in a sandbox against fixed
  baselines and hard safety constraints.
- Graph neural network for network-wide speed/queue prediction.
- Computer-vision trajectory analytics using privacy-preserving edge metadata.
- Natural-language incident assistant grounded only in approved incident and
  runbook data, with citations and no direct command privilege.
- Optimization that balances emergency ETA, safety, transit reliability,
  pedestrian delay, emissions, and general traffic rather than one metric.
- Federated or site-local model learning where raw video/data cannot leave an
  edge zone.
- Drone or aerial feed adapter for authorized disaster response, explicitly
  separated from routine surveillance.

## 13. Features deliberately excluded from the baseline

- Facial recognition and identity tracking.
- Automated fines or enforcement decisions.
- Unreviewed generative-AI commands.
- Fully autonomous signal control on public roads.
- Storage of patient records in the traffic platform.
- Hidden optimization criteria or predictions without uncertainty/provenance.
