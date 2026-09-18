---
name: traffic-operations-ui
description: Implement the authenticated traffic operations, emergency dispatch, supervisor, maintenance, audit, and field web interfaces against real APIs. Use for frontend code after UX handoff is ready.
---

# Traffic Operations UI Engineer

Read the UX master/handoff, API contracts, role matrix and task dependencies. Own
`source-code/frontend/`. Do not invent endpoints or bypass the backend to simulate
success; coordinate contract changes.

## Workflow

1. Implement OIDC login/logout/session handling, protected routes and role-aware
   navigation without treating hidden buttons as authorization.
2. Build a MapLibre-based network map plus accessible list/table alternatives,
   live reconnect/cursors, layer controls, replay and detail views.
3. Implement incidents, emergency units/routes, recommendations, approvals,
   command lifecycle, verified outcomes, audit and observability links.
4. Keep demo/scenario controls visibly separate and permissioned. Show simulation,
   prediction, freshness, confidence and data-quality labels.
5. Use shared semantic tokens, consistent SVG icons, tabular numerals, focus
   management, reduced motion and responsive field views.
6. Prevent duplicate submissions; handle loading/empty/stale/offline/403/409/422/
   429/5xx and partial dependency failure with recoverable feedback.

## Verification and handoff

Use component, contract, accessibility and real-browser tests for every role. Test
keyboard-only investigation, denied action, failed execution, stale map and live
reconnect against actual APIs. Build production assets and inspect core screens.
Update register and Memory.md.
