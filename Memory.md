# Project memory and handover

Last updated: 2026-09-18 (Asia/Karachi)

## Current state

- Project 2 planning baseline is established for an Intelligent Traffic and
  Emergency Response Platform.
- Both six-page assessment PDFs were read completely. Requirements are normalized
  in `docs/requirements/TRACEABILITY.md`; the originals remain outside this
  project because they contain confidential/personal assessment information.
- The initial demonstration assumption is 12 simulated intersections across
  three corridors with ambulance, fire, and police scenarios. This is provisional
  until environment and schedule profiling.
- Platform feature, sensor/data, architecture, safety, compliance, protocol, and
  delivery-roadmap documents exist.
- Sixteen project-local role skills exist and have been validated.
- Source, diagram, documentation, presentation, skills, workflow, and sanitized
  file areas are separated. No application implementation or deployment evidence
  exists yet.
- Management validation passed: 132 unique tasks, 16 valid skills, no dependency
  cycles, required folder structure present, and local Markdown links resolved.

## Current task

P01.06 is IN_PROGRESS under Codex SIM. P01.05 is IN_REVIEW: local Python, Node,
workflow-policy and Trivy checks pass, but no hosted GitHub Actions run exists.
No project service is assumed active.

## Exact next action

Complete a pinned headless SUMO feasibility run twice, compare deterministic
telemetry hashes, record exact commands/results, then begin P01.07 WSL feasibility.

## Open decisions and required user information

- Submission deadline, upload format, GitHub access expectations, and rehearsal
  availability are not recorded for Project 2.
- Target hardware/OS and whether a real cloud account is required are unknown.
- Actual traffic controllers, camera feeds, CAD/AVL systems, public alert systems,
  and physical sensors are not authorized or assumed available.
- Confirm whether the initial map should remain a synthetic 12-intersection grid
  or model a named public area using redistributable map data.

## Durable handover rules

At every pause, record the last completed command, changed files, verification,
blockers, and exact resume action here after updating `TASK_REGISTER.md`. Keep
facts current and delete superseded current-state text rather than accumulating
contradictory summaries.
