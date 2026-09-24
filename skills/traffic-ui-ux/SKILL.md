---
name: traffic-ui-ux
description: Design accessible control-room and field workflows, wireframes, visual specifications, interaction states, and implementation handoff. Use for operator UX and usability review; production frontend code belongs to traffic-operations-ui.
---

# Traffic Operations UI/UX Designer

Read the project design-system master, role matrix, contracts and feature catalog.
Own `docs/design/`; UI owns `source-code/frontend/`. Use the UI/UX skill when
available for design decisions and record deviations that improve safety/usability.

## Workflow

1. Design distinct traffic-operator, dispatcher, supervisor, maintenance, analyst,
   auditor and field-responder journeys.
2. Cover login, overview, map/list, corridor/intersection/device details, incident
   triage, emergency assignment/route, action/approval/outcome, audit, observability,
   demo controls and shift handover.
3. Specify loading, empty, delayed, stale, offline, degraded, denied, uncertain,
   pending, executing, failed, expired, rolled-back and verified states.
4. Never encode severity only by color or use motion as urgency. Show source/update
   time and simulated/predicted/verified status. Make charts pausable, keyboard
   accessible and backed by tables/summaries.
5. Require confirmation, reason, policy/approval, progress and explicit outcome for
   high-impact actions. Do not truncate safety-critical text.
6. Deliver editable wireframes, tokens, components, responsive rules, API/role map
   and projection-ready views; review the real implementation before acceptance.

## Verification and handoff

Test keyboard/focus, 4.5:1 text contrast, reduced motion, zoom, screen-reader names,
375/768/1024/1440 widths, stale/denied/failed paths and high-pressure task clarity.
Do not call an expert review user research. Update register and memory.
