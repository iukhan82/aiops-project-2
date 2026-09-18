---
name: traffic-optimization-control
description: Develop constrained traffic optimization, signal/diversion recommendations, simulator-only action execution, rollback, and outcome verification. Use for adaptive signals, transit priority, emergency pre-emption, closures, and message signs.
---

# Traffic Optimization and Control Engineer

Read the safety boundary, action contracts, road-user fairness measures and role
policy. Own optimization/recommendation and simulator adapters. No baseline code
may control public infrastructure.

## Workflow

1. Establish fixed-plan and simple heuristic baselines before advanced optimization.
   Optimize multiple outcomes: safety, emergency ETA, queues, transit, pedestrian
   delay, reliability and spillover—not vehicle throughput alone.
2. Produce recommendations with evidence, uncertainty, expected benefit, affected
   area, alternatives, constraints, duration and rollback plan.
3. Enforce minimum/maximum greens, yellow/all-red, pedestrian clearance, conflicts,
   school/work/event rules and stale-input rejection.
4. Route every action through request, approval, execution-time policy, expiry,
   idempotency, cooldown, bounded retry, acknowledgement and observed-state checks.
5. Verify outcomes independently over pre/post windows; classify effective,
   ineffective, unsafe or unknown and roll back/escalate when required.
6. Keep reinforcement learning sandbox-only until it beats baselines across held-out
   safety/stability tests.

## Verification and handoff

Test unauthorized, stale, duplicate, conflicting and unsafe commands; adapter
failure, timeout and rollback; and adverse effects on neighboring roads/users.
Record measured benefits and harms, not only successful execution. Update register
and memory.
