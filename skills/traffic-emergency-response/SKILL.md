---
name: traffic-emergency-response
description: Design and implement simulated CAD/AVL intake, emergency units, dispatch, safe routing, ETA, staging, agency coordination, and responder workflows. Use for ambulance, fire, police, and major-incident response features.
---

# Traffic Emergency Response Engineer

Read safety constraints, geospatial contracts and privacy rules. Own emergency
services under `source-code/backend/emergency/` and their simulator adapters. This
platform supplements rather than replaces certified dispatch systems.

## Workflow

1. Define generic call, unit, capability, availability, assignment, route, ETA,
   acknowledgement, arrival, clear and handover contracts without patient data.
2. Calculate fastest-safe alternatives using live/stale traffic, closures, hazards,
   vehicle constraints and uncertainty. Explain why a route is recommended.
3. Implement ambulance, fire and police scenarios, multi-unit staging, cross-agency
   timeline and neighboring-jurisdiction handover.
4. Request green corridors through CONTROL; never bypass policy, pedestrian
   clearance, conflict timing, command expiry or operator approval.
5. Degrade visibly when CAD/AVL, map, location or communications are stale; retain
   offline field tasks/routes where appropriate.
6. Measure dispatch acknowledgement, ETA error, route time, response improvement
   and safety-rule violations.

## Verification and handoff

Test normal and unavailable units, route closure, stale position, conflicting
emergencies, communications loss, aborted priority and safe recovery. Keep all
agency integrations labelled simulated until tested. Update register and memory.
