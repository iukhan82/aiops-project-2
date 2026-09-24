# Shared project context

## Baseline

Build a reproducible simulated urban district with 12 intersections and three
corridors. Generate traffic, vulnerable-road-user, signal, weather, road,
emergency-unit, and platform telemetry. Perform real edge inference, secure
streaming, central persistence/analytics, incident management, guarded simulator
actions, observability, and verified recovery.

## Truth labels

Every relevant record or interface distinguishes `simulated`, `measured`,
`inferred`, `predicted`, `operator_entered`, and `verified`. Ground truth is kept
outside model features. Synthetic evaluation cannot support field-accuracy claims.

## Engineering invariants

- UTC timestamps, explicit units, stable IDs, schema versions, provenance,
  geometry version, source/observation/ingest time, confidence, and quality flags.
- Offline edges continue safe local processing and buffer accepted events. Replay
  is ordered, acknowledged, bounded, idempotent, and duplicate-safe.
- Recommendations, commands, acknowledgements, observed state, and verified
  outcomes are different records.
- Root-cause hypotheses are not verified causes. Recovery needs independent,
  sustained health evidence.
- Baseline rules precede complex ML; test splits are disjoint and models can
  abstain or roll back.
- Accessibility, privacy, security, safety, and assessment traceability are part
  of acceptance criteria.

## Permission boundary

Routine local code, tests, documents, diagrams, and disposable project-scoped
containers are in scope when requested. Publishing, cloud spending, submission,
live infrastructure access, public messaging, and real traffic control require
explicit user authorization and appropriate access.
