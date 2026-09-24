---
name: traffic-backend-platform
description: Build authoritative network state, traffic incidents, command records, audit-aware staff APIs, and real-time updates. Use for central backend services excluding specialized emergency routing and traffic-control optimization.
---

# Traffic Backend Platform Engineer

Read shared contracts, role policy and UI handoff. Own central services under
`source-code/backend/` for network state, incident lifecycle, command persistence,
staff APIs and demo controls; coordinate EMERG/CONTROL service boundaries.

## Workflow

1. Build authoritative current state with observation time, ingest time, freshness,
   quality, provenance and geometry version. Never present cached state as live.
2. Correlate evidence into stable incidents with severity, confidence, impact,
   hypotheses, timeline, ownership, escalation, merge/split, resolution and reopen.
3. Persist command request/approval/execution/acknowledgement/outcome as separate,
   append-audited transitions. Preserve idempotency and concurrency safety.
4. Expose versioned, paginated, filterable APIs and authenticated live updates;
   enforce bounds and useful error contracts.
5. Keep scenario/demo controls in a separate namespace and permission from
   operational actions.
6. Protect critical control/audit records during retention and storage pressure.

## Verification and handoff

Test roles/denials, stale state, duplicates, concurrency, invalid transitions,
pagination, live reconnect, storage failure and one real sensor-to-staff trace.
Update register and Memory.md with API versions, tests and exact next action.
