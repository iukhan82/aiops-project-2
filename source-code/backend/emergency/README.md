# P07.01 - CAD/AVL adapter and emergency state

`cad_avl_adapter.py`, `backend/repositories/emergency.py`, migrations `0016`/`0017`,
`GET /api/v1/emergency/calls[/{id}]`, `verify_emergency.py`.

Calls and unit assignments are explicit state machines (`contracts/emergency-call/v1`,
`emergency-unit-assignment/v1`'s own enums), the same discipline as P05.08's incidents:
row-locked transitions, an append-only transition table per entity, every write taking
an explicit `at` so the same code drives a live feed and a historical replay.

P03.04's simulator exports one **final** CAD record per call/assignment (a real legacy
CAD/AVL feed shape). The adapter (`plan_replay`) derives each call's and assignment's
timeline from the timestamps that record actually carries, then replays every call and
assignment across the whole dataset **interleaved in real chronological order** (not
call-by-call) through the live repository. Two timestamps the export does not
distinguish are collapsed onto the nearest evidence that exists, and that is stated
in the module docstring rather than hidden: `dispatched`/`unit_assigned` both land at
the assignment's `assigned_at`, and an assignment's `en_route` lands at its own
`acknowledged_at`. A call clears only once every one of its assignments has.

AVL position telemetry (`emergency.unit_position.avl`) is real `observation-envelope/v1`
device telemetry and goes through P05.05's real ingestion unchanged - it is not a
position column bolted onto the call record.

`verify_emergency.py` (real Postgres, 15 checks): Part A replays the full real dataset
(9 calls across all three call types, 9 assignments, 238 AVL events from 6 devices) -
every call reaches a terminal status, every assignment has its full 5-transition history,
AVL ingestion is idempotent, and re-running the replay on already-loaded data is
*rejected* (not silently duplicated) because the CAD ids already exist. Part B proves
the two transitions the real dataset never exercises, with synthetic records labelled
`test:synthetic` and removed afterwards: a call can be `cancelled`, an assignment can go
`unavailable` mid-response and the call can still be reassigned to a second unit, and a
`handover` record persists with its required fields (exercised here because P03.04's
calls are single-agency; P07.03 builds the real cross-agency handover scenario on this
same adapter). Evidence: `docs/evidence/p07_01_emergency.json`.

## P07.03 - cross-agency staging and handover

`cross_agency.py`, `verify_cross_agency.py`.

P07.01's two state machines are per-entity: they know a call's own legal status changes and an
assignment's own, nothing about how one agency's progress should gate another's. That gating is a
dispatch policy, not a state-machine invariant, so `cross_agency.py` layers it on top rather than
baking it into the repository: `StagingStep.after` names the *unit* (not agency) a unit stages
behind, so a real dependency chain exists (police perimeter -> fire extrication -> ambulance
transport), and `advance_to_scene` blocks a unit at `staged` - raising `StagingViolation`, recorded
in its own audit trail, not silently dropped - if it is asked to go on-scene before that specific
prerequisite has actually arrived. `record_handover` attaches a `from_agency`/`to_agency`/
`acknowledged_by` handover to the departing unit's own assignment, so responsibility for a scene can
change hands independently of when a unit physically clears.

`verify_cross_agency.py` (real Postgres, 10 checks): a real three-agency traffic-collision call
(police, fire staged behind police, ambulance staged behind fire) is dispatched through the real
repository. Fire and ambulance are both genuinely blocked when they try to proceed before their real
prerequisite has arrived (not a scripted skip - the attempt is made and rejected); each proceeds once
released after its prerequisite is actually on scene; handovers chain police -> fire and EMS -> fire
with every contract-required field; no patient/medical field exists anywhere in the case file
(grepped, not merely asserted absent); P07.01's own call-timeline derivation stays correct for a
multi-unit call (on-scene at the first unit's real arrival, cleared at the last unit's real
clearance). Unit tests: `tests/test_cross_agency.py` (5, pure dependency-ordering logic, no
database). Evidence: `docs/evidence/p07_03_cross_agency.json`.
