# Folder structure and ownership

| Path | Contents | Primary owner |
|---|---|---|
| `source-code/simulator/` | SUMO network, demand/scenario orchestration, ground truth | SIM |
| `source-code/edge/` | Device adapters, local inference, durable outbox | EDGE |
| `source-code/contracts/` | Versioned event/API/policy schemas and examples | LEAD |
| `source-code/backend/` | State, incidents, emergency, optimization, commands, APIs | BACKEND/EMERG/CONTROL |
| `source-code/frontend/` | Operator, dispatcher, supervisor, field web applications | UI |
| `source-code/database/` | Migrations, seeds, retention and backup helpers | DATA/BACKEND |
| `source-code/models/` | Training, evaluation, registry metadata, model cards | EDGE/OPS |
| `source-code/security/` | Policy, identity configuration and security tests | SEC |
| `source-code/observability/` | OTel, metrics, dashboards, alerts, operational datasets | OPS |
| `source-code/infra/` | Containers, Compose, K3s and deployment scripts | DEVOPS |
| `source-code/tests/` | Cross-service and acceptance tests | QA |
| `diagrams/` | Editable engineering diagrams and exports | ARCH |
| `docs/design/` | Journeys, wireframes, design system and UX handoff | UX |
| `docs/requirements/` | Traceability, acceptance and coverage | LEAD/QA |
| `docs/security/` | Threat model and security evidence | SEC |
| `docs/compliance/` | Privacy, risk and framework mapping | GRC |
| `docs/runbooks/` | Operational, security, recovery and deployment procedures | OPS/DEVOPS |
| `docs/evidence/` | Machine-readable measured evidence and index | QA |
| `presentation/` | Deck, PDF, notes, demo and interview material | PRESENT |
| `skills/` | Project-local agent skills | LEAD |
| `workflows/` | CI/CD and DevSecOps workflows | DEVOPS |
| `files/` | Sanitized licensed inputs only | LEAD/GRC |

Shared contracts, management files, and deployment manifests have one active
editor. A delegated agent proposes changes through its handover when ownership is
not assigned.
