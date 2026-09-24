# Architecture and technology decisions

This directory holds versioned Architecture Decision Records (ADRs) for task
P02.02. Each record captures the context, the options actually considered, the
decision, its consequences, and the evidence that would trigger revision. ADRs
formalize and justify choices already reflected informally in
`docs/REFERENCE_ARCHITECTURE.md` and `docs/environment/TOPOLOGY.md`; where an
ADR and those documents ever disagree, treat the ADR as authoritative for the
decision and update the other document to match.

## Status values

- **Proposed** - drafted, not yet acted on.
- **Accepted** - the project proceeds on this decision.
- **Provisional** - accepted for planning but explicitly pending information
  outside project control (for example, user-supplied deployment target or
  submission constraints); the record states exactly what would resolve it.
- **Superseded** - replaced by a later ADR, which is linked.

## Index

| ID | Title | Area | Status |
|---|---|---|---|
| [ADR-0001](ADR-0001-simulator-platform.md) | Simulator platform | Simulator | Accepted |
| [ADR-0002](ADR-0002-streaming-transport.md) | Streaming and messaging transport | Streaming | Accepted |
| [ADR-0003](ADR-0003-persistent-storage.md) | Persistent storage engines | Data | Accepted |
| [ADR-0004](ADR-0004-geospatial-map-data.md) | Geospatial map data source | Map | Provisional |
| [ADR-0005](ADR-0005-ai-model-strategy.md) | Edge AI model strategy | AI | Accepted |
| [ADR-0006](ADR-0006-edge-compute-runtime.md) | Edge compute runtime | Edge | Accepted |
| [ADR-0007](ADR-0007-central-cloud-placement.md) | Central/cloud placement | Central/cloud | Provisional |
| [ADR-0008](ADR-0008-deployment-orchestration.md) | Deployment orchestration | Deployment | Accepted |

## Template

New ADRs follow this structure: Status, Date, Deciders, Context, Decision
drivers, Options considered (with trade-offs), Decision, Consequences,
Security/privacy/safety effects, Revision triggers, Related requirements.
