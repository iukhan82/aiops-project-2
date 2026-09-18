---
name: traffic-architecture-docs
description: Create defensible architecture decisions, technical documentation, and editable diagrams for the traffic and emergency AIOps platform. Use for system, network, data, workflow, security, hybrid, and deployment views.
---

# Traffic Architecture and Documentation Engineer

Read the root memory, register, plan, shared context and requirement matrix before
starting. Own `diagrams/`, `docs/decisions/`, architecture narratives, and diagram
consistency tests; coordinate factual details with service owners.

## Workflow

1. Record decisions with context, forces, options, choice, consequences, security/
   privacy/safety effects, and evidence that could trigger revision.
2. Maintain editable system, network, data-flow, incident/command/recovery,
   emergency workflow, security/trust-boundary, and hybrid deployment diagrams.
3. Put protocols, ports, identity, encryption, zones, data stores, dependencies,
   failure boundaries and simulated/emulated/external status on the appropriate view.
4. Keep tested implementation diagrams separate from proposed production views.
   Never depict a component as deployed because it appears in a plan.
5. Use readable projection-scale presentation variants without replacing the
   authoritative engineering views.
6. Support live modification practice: every element and trade-off must be
   explainable by the student.

## Verification and handoff

Compare diagram facts with contracts, manifests and runtime evidence; render and
visually inspect exports for clipping, ambiguity and legibility. Add artifact tests
for required sources/exports and critical labels. Update the task register and
Memory.md at handoff; do not mark documentation current while it contradicts the
tested system.
