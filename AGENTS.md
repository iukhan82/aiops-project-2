# Project instructions for coding agents

## Mandatory startup

Before starting or resuming any task, read these files in order:

1. `Memory.md` - current handover and exact next action.
2. `TASK_REGISTER.md` - authoritative status, ownership, dependencies, acceptance,
   and evidence.
3. `PROJECT_PLAN.md` - scope, phases, milestones, risks, and exit gates.
4. `docs/PROJECT_CONTEXT.md` - shared engineering, safety, and assessment rules.
5. The `SKILL.md` for the role that owns the selected task.

Inspect the actual repository before trusting an old handover. Claim only a
dependency-ready task and record the acting role in `Memory.md`. Do not repeat a
DONE task unless its evidence is missing, the implementation changed, or a
defect requires reopening it.

## Task and handover protocol

- `TASK_REGISTER.md` is the only backlog. `Memory.md` is a handover, not another
  task list.
- Status values are TODO, IN_PROGRESS, IN_REVIEW, BLOCKED, DONE, DEFERRED, and
  CANCELLED. Dependency waiting is TODO, not BLOCKED.
- A task is DONE only when its acceptance criteria are met and its evidence can
  be inspected. Code/configuration alone is not runtime evidence.
- Before pausing: update the affected task row, append the register change log,
  and replace the current handover in `Memory.md` with changed files, commands,
  results, limitations, and the exact next action.
- If multiple agents are explicitly requested, the coordinator alone edits
  shared management files. Delegate bounded tasks with exclusive file ownership.
  Skills define roles; they do not start agents or expand authorization.

## Product truth and safety

- The baseline uses a reproducible simulated road network. Label simulated,
  inferred, predicted, operator-entered, and verified information distinctly.
- No prototype component controls a public road. Controller, pre-emption, closure,
  and public-warning actions target the simulator until separately authorized
  infrastructure and a safety case exist.
- The recommendation path cannot directly invoke an action adapter. Protected
  actions require current evidence, policy evaluation, authorization, expiry,
  idempotency, safe bounds, acknowledgement, audit, and outcome verification.
- Preserve pedestrian clearance, conflict timings, emergency safety constraints,
  and a safe fallback plan. Stale, disconnected, or uncertain state must fail
  safe.
- Do not implement facial recognition, automated enforcement, covert identity
  tracking, patient-data storage, or unreviewed generative-AI commands.
- Never invent measurements, student participation, field validation, certification,
  compliance, production deployment, or assessor approval.

## Assessment obligations

Maintain `docs/requirements/TRACEABILITY.md` against both assessment PDFs in the
workspace root. The final evidence must cover edge processing, optimized local
inference, centralized monitoring/automation, real-time streams, K3s/MicroK8s,
containers, required messaging/observability/security tools, all mandatory
diagrams, failure and recovery, scale and security trade-offs, standards and
governance, GitHub delivery, a 15-20 minute presentation, a 30-40 minute interview,
at least ten questions, transparent AI assistance, and live architecture changes.

The supplied assessment PDFs are confidential source material. Do not copy them
into this repository or publish student identity/contact details.

## Repository layout

- `source-code/` - product code, contracts, models, database, infrastructure,
  tests, and scripts.
- `diagrams/` - editable implementation diagram sources and rendered exports.
- `docs/` - requirements, decisions, design, security, runbooks, evidence, and
  task records.
- `presentation/` - editable deck, PDF, speaker notes, demo and question bank.
- `skills/` - project-local role skills.
- `workflows/` - DevSecOps CI/CD workflows and policy.
- `files/` - sanitized, redistributable input/reference files only.

Follow `docs/FOLDER_STRUCTURE.md`. Do not create competing top-level source trees.
Never commit secrets, runtime credentials, raw identifiable video, personal exam
documents, large generated datasets, or local environment artifacts.

## Project skill routing

- LEAD: `skills/traffic-aiops-project-lead/`
- ARCH: `skills/traffic-architecture-docs/`
- SIM: `skills/traffic-simulation-digital-twin/`
- EDGE: `skills/traffic-edge-ai-vision/`
- DATA: `skills/traffic-data-streaming/`
- BACKEND: `skills/traffic-backend-platform/`
- EMERG: `skills/traffic-emergency-response/`
- CONTROL: `skills/traffic-optimization-control/`
- UX: `skills/traffic-ui-ux/`
- UI: `skills/traffic-operations-ui/`
- OPS: `skills/traffic-observability-aiops/`
- SEC: `skills/traffic-security-engineering/`
- GRC: `skills/traffic-privacy-compliance/`
- DEVOPS: `skills/traffic-devsecops-platform/`
- QA: `skills/traffic-qa-reliability/`
- PRESENT: `skills/traffic-presentation-coach/`

Use only the roles needed for the current request. Security, privacy, safety, QA,
and presentation are continuous concerns, not last-phase decorations.
