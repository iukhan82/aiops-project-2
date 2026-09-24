# Database (Phase 05, P05.04)

Migrations, seeds and retention/backup helpers for the P05.01 PostgreSQL/
PostGIS service. Schema only; application repositories/state machines live
in `source-code/backend/` (P05.08).

| File/dir | What it does |
|---|---|
| `migrations/` | Ordered, numbered `.sql` files (`0001_...` .. `0006_...`), one table group per contract family |
| `migrate.py` | Applies not-yet-applied migrations in order, each in its own transaction; records a sha256 checksum per applied file and refuses to proceed if an already-applied file's on-disk content has since changed |
| `seeds/seed_topology.py` | Idempotent (`ON CONFLICT DO UPDATE`) seed of the 12 real intersections from P03.01's actual SUMO network - not synthetic rows |
| `verify_migrate.py` | Real-Postgres acceptance proof (see below) |

## Schema, by migration

1. `0001_topology.sql` - `geometry_versions`, `intersections`, `lanes` (PostGIS `geography` columns, GiST-indexed), matching `contracts/road-geometry/v1`'s core entities.
2. `0002_devices.sql` - `devices`, matching `contracts/device/v1`.
3. `0003_observation_events.sql` - `observation_events`, matching `contracts/observation-envelope/v1`. `event_id` is the primary key: the exactly-once-by-meaning mechanism P05.05's ingestion and P05.03's gateway rely on.
4. `0004_commands_outcomes.sql` - `commands` (`idempotency_key UNIQUE` is a real constraint, not just documented behavior), `command_outcomes`.
5. `0005_incidents_recommendations.sql` - `incidents`, `recommendations`.
6. `0006_emergency.sql` - `emergency_calls`, `emergency_unit_assignments` - deliberately no patient/medical column, matching the contracts' own exclusion.
7. `0007_ingestion.sql` - `content_sha256` on `observation_events`, `ingestion_rejections` (P05.05's schema/identity/content-conflict audit).
8. `0008_audit_trails.sql` - `incident_transitions`, `command_transitions` - append-only state-transition history (P05.08).
9. `0009_scenario_control.sql` - `scenario_runs`, `scenario_control_audit` - the separate P05.09 demo-control API's own state, deliberately apart from the operational tables.
10. `0010_retention.sql` - `retention_rollups` (aggregation before deletion), `retention_runs` (P05.10).
11. `0011_retention_cascades.sql` - `ON DELETE CASCADE` from command/incident audit trails, outcomes and recommendations to their parent row: a retention purge retires a terminal record's whole case file together, never leaving orphaned children (non-terminal/active records are never purged in the first place).
12. `0012_segments_kpis.sql` - `network_segments` (segment topology with fitted lane shares), `corridor_kpis` (persisted corridor KPIs per window) (P06.01).
13. `0013_forecasts.sql` - `forecasts` (`contracts/forecast/v1`; the id is derived so a recompute replaces, never duplicates) (P06.03).
14. `0014_detection_candidates.sql` - `detection_candidates`: every detector's output in one uniform, evidence-backed stream (P06.04-P06.06).
15. `0015_incident_correlation.sql` - `incident_candidates`, `incident_hypotheses`: candidates correlated into incidents, with the reason recorded (P06.07).
16. `0016_emergency_state.sql` - emergency call/assignment transition tables and the contract columns 0006 predates (P07.01).
17. `0017_emergency_retention_cascades.sql` - retiring a call retires its assignments with it (P07.01).
18. `0018_recommendations_contract_complete.sql` - the recommendation contract's remaining columns (P07.04).
19. `0019_command_roles_and_params.sql` - who held which operating role at request and approval, and `command_params` (P07.10 / P08).
20. `0020_operator_workflows.sql` - `incident_notes`, `operator_audit` (append-only by trigger), `shift_handovers`, `service_heartbeats` (P08.07 - P08.09).
21. `0021_assignment_capability_array.sql` - `emergency_unit_assignments.capability` becomes the array the contract says it is (P08.07).
22. `0022_scenario_control_identity.sql` - the scenario-control API records who acted (`actor`), can record refusals with no identity and reads of its trail, and its audit table becomes append-only (P08.09).
23. `0023_ingestion_received_index.sql` - an index on `observation_events.received_at`, for the platform-status screen's ingestion measurement (P08.09).
24. `0024_policy_decisions.sql` - `policy_decisions`: every policy-engine decision about a command (request, review, approval, execution, and an outage as `unavailable`) with the input, the policy version and the engine's decision id; append-only by trigger (P09.03).
25. `0025_scenario_control_policy_outcome.sql` - the scenario-control audit records a refusal caused by an unreachable policy engine under its own outcome (P09.03).
26. `0026_audit_hash_chain.sql` - a hash chain (`prev_hash`/`row_hash`) over eight histories so a rewrite that defeats the append-only trigger is still caught by an independent recompute (`audit_chain.py`); `command_transitions`/`incident_transitions`/the two emergency transition tables gain UPDATE/TRUNCATE protection (they were unprotected since 0008/0016) but not DELETE, which `database/retention.py` legitimately cascades through; `retention_runs` (0010) gains full append-only protection and a chain of its own, so it is what turns an unexplained gap in those four into an accounted-for one; CHECK constraints refuse a JWT or PEM private key in the three tables that carry free-form detail (P09.05).
27. `0027_policy_decisions_override_point.sql` - adds `command_override` to `policy_decisions.point`'s allowed values (P09.05).

## `retention.py` (P05.10) and `audit_chain.py` (P09.05)

`audit_chain.py` (`source-code/backend/audit_chain.py`, run `python source-code/backend/audit_chain.py [--database aiops_demo]`) independently verifies the hash chain 0026 adds: for every chained row it recomputes the hash server-side (a regex substitution on `row_to_json(t)::text` puts `row_hash` back to `null` exactly as it was when the trigger first hashed the row - re-deriving it in Python was tried first and was wrong, because `row_to_json` renders a nested jsonb column with JSONB's own spaced formatting but the row's own top-level fields compactly, and reproducing that mix client-side is fragile) and must match; a `prev_hash` that names no surviving row is tampering for the four tables nothing ever deletes from, and an accounted-for gap - cross-checked against `retention_runs` - for the four `database/retention.py` cascades through.

Retention, aggregation and storage-pressure handling for `observation_events`
and terminal command/incident records.

- **Per-`retention_class` windows**: `short` (7d), `standard` (90d),
  `extended` (365d) each purge past their window; `audit` is *never*
  purged - a real compliance property, not just a long default.
- **Aggregation before deletion**: every row about to be purged is folded
  into a per-device/per-day `retention_rollups` row first, so trend history
  survives the raw event's retention window.
- **Preserves active control**: commands/incidents are only retention-
  eligible in a *terminal* status (commands: denied/failed/expired/
  rolled_back; incidents: resolved). A command still `requested` or an
  incident still `open`/`investigating` is never purged, no matter how old.
- **Storage-pressure handling**: if the database exceeds `HARD_SIZE_BYTES`,
  every finite window shrinks by `PRESSURE_FACTOR` (0.5) *for that run
  only* - never persisted as a new default, so pressure relief never
  silently rewrites policy at rest.

### Reproduction

```bash
cd source-code/infra/platform && bash up.sh   # P05.01/P05.04 stack must be running and migrated
cd ../../..
python source-code/database/verify_retention.py
```

Proves against the real database: a `short`-class event is purged past its
window and rolled up first; an `audit`-class event with the same age is
never purged; a terminal command/incident past the operational-record
window is purged (cascading its own audit trail/outcomes/recommendations);
an active (non-terminal) command/incident survives despite being
projected far into the future; and - a real before/after comparison on
the *same* data, not a config check - a `standard`-class event that
survives the normal 90-day window is purged once storage pressure is
forced (by lowering `HARD_SIZE_BYTES` against the real, small dev database
size), proving the shortened window actually takes effect.

Fast pure-logic unit tests (window ordering, pressure-shrink arithmetic)
are in `source-code/tests/test_retention.py`.

Evidence: `docs/evidence/p05_10_retention.json`.

Well-defined scalar/enum fields from each JSON contract become real typed
columns with `CHECK` constraints mirroring the contract's own `enum`s;
open-ended nested structures (`measurements`, `alternatives`,
`route_alternatives`, `provenance`, ...) stay `jsonb`.

## Reproduction

```bash
cd source-code/infra/platform && bash up.sh   # P05.01 Postgres/PostGIS must be running
cd ../../..
export POSTGRES_DB=aiops POSTGRES_USER=aiops_app POSTGRES_PASSWORD=<from .env>
python source-code/database/verify_migrate.py --reset
```

`verify_migrate.py` is not a pytest suite - proving checksum-tamper
detection means temporarily rewriting a migration file on disk (restored in
a `finally`), which doesn't belong in the always-green unit test run.
`--reset` drops and recreates the `public` schema first (safe: this is the
project's own dev/test Postgres fixture, nothing else depends on it yet).
It proves, against the real database:

1. All 6 migrations apply and create every expected table.
2. Re-running is a true no-op (idempotent - no migration re-applied).
3. Constraints actually reject bad data: an invalid enum value, a dangling
   foreign key, and `expires_at <= requested_at` are all refused by
   Postgres itself, not just documented as intended behavior.
4. Hand-editing an already-applied migration file is detected
   (`ChecksumMismatch`) on the next run, not silently ignored.
5. The topology seed loads the real 12-intersection SUMO network and is
   idempotent (re-running produces the same 12 rows, not 24).

Fast, DB-free unit tests (migration discovery/ordering/checksum stability)
are in `source-code/tests/test_database_migrate.py` and run in the normal
`check.py` suite.

Evidence: `docs/evidence/p05_04_migrations.json`.
