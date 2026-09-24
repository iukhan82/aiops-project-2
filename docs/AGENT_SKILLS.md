# Project-local agent skills

All roles read `AGENTS.md`, `Memory.md`, `TASK_REGISTER.md`, `PROJECT_PLAN.md`, and
`docs/PROJECT_CONTEXT.md` before acting. A role owns decisions/files; it does not
create a background agent or authorize work by itself.

| Code | Skill | Primary ownership |
|---|---|---|
| LEAD | `traffic-aiops-project-lead` | Requirements, contracts, plan, integration and evidence coverage |
| ARCH | `traffic-architecture-docs` | Decisions, diagrams and technical documentation |
| SIM | `traffic-simulation-digital-twin` | SUMO network, scenarios, sensors, ground truth and datasets |
| EDGE | `traffic-edge-ai-vision` | Edge features, models, inference, privacy-preserving vision and outbox |
| DATA | `traffic-data-streaming` | MQTT/Kafka bridge, schemas, ordering, persistence and replay |
| BACKEND | `traffic-backend-platform` | Network state, incidents, staff APIs and command records |
| EMERG | `traffic-emergency-response` | CAD/AVL abstraction, units, routes, ETAs and coordination |
| CONTROL | `traffic-optimization-control` | Recommendations, constraints, simulator actions and outcomes |
| UX | `traffic-ui-ux` | Journeys, wireframes, design system and usability review |
| UI | `traffic-operations-ui` | Operator/dispatcher/supervisor/field web application |
| OPS | `traffic-observability-aiops` | Telemetry, SLOs, operational detection, remediation and recovery |
| SEC | `traffic-security-engineering` | Identity, policy, transport, secrets, threat controls and runtime security |
| GRC | `traffic-privacy-compliance` | Privacy, risk, standards mapping, governance and publication review |
| DEVOPS | `traffic-devsecops-platform` | Environments, CI/CD, containers, K3s, supply chain and releases |
| QA | `traffic-qa-reliability` | Test strategy, acceptance, fault/capacity evidence and coverage |
| PRESENT | `traffic-presentation-coach` | Deck, demo, oral questions, live redesign and rehearsals |
