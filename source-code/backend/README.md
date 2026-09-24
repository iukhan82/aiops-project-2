# Backend services (Phase 05+)

State, incidents, emergency, optimization, command, and API services.
Connects to `source-code/infra/platform/` (P05.01) and schemas migrated by
`source-code/database/` (P05.04).

| Directory | Task | What it does |
|---|---|---|
| `gateway/` | P05.03 | MQTT-to-Kafka bridge: validates against `contracts/observation-envelope/v1`, durably buffers (reuses `edge.outbox`), produces partitioned-by-device, application-acks only after a confirmed Kafka write |
| `ingestion/` | P05.05 | Kafka-to-Postgres consumer: rejects schema/identity/content conflicts, accepts identical replay as an idempotent no-op, commits a Kafka offset only after its DB write commits |
| `state/` | P05.06 | Windowed network-state aggregation (`contracts/network-state/v1`): preserves source observation time, per-measurement quality/confidence, geometry_version; carries the last known value forward (sample_count=0) when a window has no fresh samples |
| `api/` | P05.07 | Versioned (`/api/v1/`), cursor-paginated device/observation-history/network-state REST endpoints plus a device/corridor-scoped live WebSocket feed with gapless, duplicate-free reconnect |
| `repositories/` | P05.08 | Incident/command repositories: explicit state machines, row-locked concurrency safety, idempotent command creation, required audit references, append-only transition history |
| `scenario_control/` | P05.09 | Separate demo-control API (start/status/reset/replay), wraps P03.07's real replay mechanism, role-gated (placeholder ahead of P09.02/09.03), bounded concurrent runs, every action audited |

## `gateway/`

Bridges the mTLS MQTT listener (P05.02, 8883) into the Kafka-compatible
central event backbone (P05.01, Redpanda). Per ADR-0002, this is the *only*
service allowed to consume MQTT directly.

Reuses `edge.outbox.DurableOutbox` (P04.07) as the gateway's own crash-safe
buffer between "accepted off MQTT" and "confirmed in Kafka" - same durable,
ordered, bounded-quota SQLite WAL mechanism that protects an edge instance
from an uplink outage, applied here to protect the gateway from a Kafka
outage. `DurableOutbox` is documented single-writer/single-reader and its
sqlite3 connection cannot cross threads, so the gateway is careful to create
and use it from exactly one thread (`gateway.py`'s module docstring and
`Gateway.__init__`/`_worker_loop` explain the design); the MQTT callback
thread only ever hands events across via an in-memory `queue.Queue`.

### Reproduction

```bash
cd source-code/infra/platform && bash up.sh   # P05.01/P05.02 stack must be running
cd ../../..
python source-code/backend/gateway/verify_gateway.py
```

`verify_gateway.py` is not a pytest suite - it stops/starts the real Kafka
container to prove backpressure across a genuine outage, which doesn't
belong in an always-green unit test run. It proves, against the live stack:

1. **Validation**: a schema-invalid event is rejected (app-level nack),
   never reaches Kafka.
2. **Partitioning**: one device's events always land on the same Kafka
   partition (produced with `key=device_id`).
3. **Backpressure**: stopping the Kafka broker mid-flight does not crash the
   gateway or drop events - buffered events drain and get acked once Kafka
   recovers, verified in one run against a real container stop/start, not
   simulated.
4. **Application ack/replay**: a device receives "accepted" only after
   Kafka durably has the event; consuming the topic from the earliest
   offset twice yields the identical set of events.

Fast pure-logic unit tests (schema validation, topic parsing) are in
`source-code/tests/test_gateway.py` and run in the normal `check.py` suite.

Evidence: `docs/evidence/p05_03_gateway.json`.

## `ingestion/`

Consumes the real `telemetry.events` Kafka topic and writes to
`observation_events` (P05.04), the only consumer of that topic committing
authoritative state. A Kafka offset is committed only *after* its event's
database write commits, so a crash between the two simply reprocesses that
message on restart - safe only because every write path is idempotent.

Three explicit rejection paths, each recorded in `ingestion_rejections` for
audit (P05.04's `0007_ingestion.sql`), never silently dropped:

- **schema conflict**: fails `contracts/observation-envelope/v1` - checked
  again here even though P05.03's gateway already checked it, because
  ingestion is a different trust boundary and must not assume upstream
  validation held.
- **identity conflict**: `device_id` does not reference a known `devices`
  row.
- **content conflict**: `event_id` already exists with a *different*
  `content_sha256` - the original row is never overwritten.

A true replay (same `event_id`, same `content_sha256`) is accepted as an
idempotent no-op - proven not just for a single re-delivered message but for
a brand-new consumer group replaying the *entire* topic from the earliest
offset.

### Reproduction

```bash
cd source-code/infra/platform && bash up.sh   # P05.01/P05.04 stack must be running and migrated
cd ../../..
python source-code/backend/ingestion/verify_ingest.py
```

`verify_ingest.py` produces directly onto a dedicated, reset-on-every-run
test topic (`telemetry.events.p05_05_verify`) rather than the real
`telemetry.events` topic P05.03 uses, and proves against the real database:
a valid event inserts; an identical resend is idempotent; a same-`event_id`-
different-content message is rejected and never overwrites the original; a
schema-invalid and an unknown-device message are both rejected; and a fresh
consumer group replaying the whole topic from scratch reproduces the exact
same outcome for every message, with the stored row never duplicated.

Fast pure-logic unit tests (content-hash determinism) are in
`source-code/tests/test_ingestion.py`.

Evidence: `docs/evidence/p05_05_ingestion.json`.

## `state/`

Reads `observation_events` (never writes) and aggregates a trailing window
into one `contracts/network-state/v1` record per network element -
`compute_state(conn, network_element_type, network_element_id,
window_seconds, max_staleness_seconds, as_of=None)`. Supports `lane`,
`intersection` and `corridor` (map directly to observation_events' own
columns); `segment` raises `NotImplementedError` rather than fabricating an
equivalence P05.04's topology never defined.

Aggregation policy, applied per measurement name within the window:
value = mean, quality = worst-of (`invalid` > `suspect` > `valid`),
confidence = mean, `sample_count` = contributing row count. A window with
zero fresh samples but an earlier one still returns a record - the last
known value, `sample_count=0` - per the contract's own documented
"0 means carried-forward" semantics, rather than returning nothing. An
element that has *never* been observed at all returns `None` instead of a
contract-invalid empty record. `freshness_status` compares the latest
contributing sample's true age (fresh or carried-forward) against
`max_staleness_seconds` - not against when the record was computed.

### Reproduction

```bash
cd source-code/infra/platform && bash up.sh   # P05.01/P05.04 stack must be running and migrated
cd ../../..
python source-code/backend/state/verify_network_state.py
```

Proves against the real database: windowed averaging, worst-quality-wins,
carry-forward on an empty window, fresh-vs-stale freshness, `None` for a
never-observed element, `segment`'s documented `NotImplementedError`, and -
the most rigorous check - that a produced record actually validates against
the real `contracts/network-state/v1/schema.json` via `jsonschema.validate`,
not just "looks right" by inspection.

Fast pure-logic unit tests (quality/truth-label aggregation rules) are in
`source-code/tests/test_network_state.py`.

Evidence: `docs/evidence/p05_06_network_state.json`.

## `api/`

FastAPI + uvicorn (no ADR fixed a backend web framework; chosen here for
native async WebSocket support, built-in OpenAPI, and a small footprint -
documented here since it's a real implementation decision). Sync route
handlers (`def`, not `async def`) so FastAPI runs their blocking psycopg
calls in its thread pool rather than stalling the event loop; the
WebSocket handler is `async def` by necessity and offloads its blocking DB
poll via `asyncio.to_thread`.

| Route | What it does |
|---|---|
| `GET /api/v1/devices` | Cursor-paginated device list |
| `GET /api/v1/devices/{device_id}` | One device, 404 if unknown |
| `GET /api/v1/network-state/{type}/{id}` | Calls P05.06's `compute_state`; 404 if never observed, 501 for `segment` (no topology yet, not faked) |
| `GET /api/v1/observations` | Cursor-paginated observation history, optional `device_id` filter |
| `WS /api/v1/live` | Live feed, optional `device_id`/`corridor_id` scoping, `?since=<iso8601>` reconnect |

Pagination is cursor-based (opaque base64 of the last-seen sort key), not
offset-based, so results stay correct under concurrent inserts - a page
boundary can never skip or repeat a row because another row was inserted
between two page requests, the way offset pagination can.

**Live reconnect**, the part actually worth proving carefully: on connect,
`/api/v1/live` first sends every backlog row with `received_at > since`,
then polls for and streams new ones. A client that disconnects and
reconnects with `since` set to the last event it saw receives exactly what
it missed while disconnected - no gap, no duplicate - because `received_at`
is assigned by the database monotonically, not supplied by the client.
`device_id`/`corridor_id` scope the feed, so watching one corridor never
means receiving every other corridor's firehose.

### Reproduction

```bash
cd source-code/infra/platform && bash up.sh   # P05.01/P05.04 stack must be running and migrated
cd ../../..
python source-code/backend/api/verify_api.py
```

Starts the real app under a real uvicorn server on a real socket (not
FastAPI's in-process TestClient) and drives it with real `httpx`/
`websockets` clients. Proves: 404 on unknown device, pagination collects
the exact expected set across multiple pages with no cursor repeating, the
network-state endpoint round-trips through P05.06, `segment` returns 501
rather than a fabricated 200, the live feed sends the correct backlog on
connect, a genuine disconnect/reconnect receives exactly the one event
inserted while disconnected (never re-delivering what was already seen),
and device-scoping excludes another device's traffic entirely.

Fast pure-logic unit tests (cursor encode/decode) are in
`source-code/tests/test_api.py`.

Evidence: `docs/evidence/p05_07_api.json`.

### The operator API since P08 (authentication, workflows, governance)

The same app now serves the operator UI (`source-code/frontend`). It is split by concern; each module has a `register(app)`
function that decorates the app directly (FastAPI 0.141 hides routes added with `include_router` from the global dependency's
`scope["route"]`, which the access check needs).

| Module | What it serves | Verified by |
|---|---|---|
| `auth.py`, `authz.py`, `../pdp.py` | Keycloak access-token validation (RS256/JWKS, issuer, audience `aiops-api`, `exp`, authorised party, operating role); then the policy engine (Open Policy Agent, P09.03) decides whether the roles hold the capability the UX inventory gives the endpoint - unlisted endpoints are denied, an engine that cannot answer is 503 `policy_unavailable`, never a permit; a 403 is written to `operator_audit`; `AIOPS_AUTH_MODE=off` exists only for the data-API verify scripts and the browser stack refuses it | `verify_auth.py`, `../../policy/verify_policy.py` |
| `hardening.py` | before any decision: security headers on every response (also errors), no cross-origin header, no interactive docs or schema, request bodies over 64 KiB refused (413, also when streamed), per-person read and write token buckets, and a per-address allowance for requests that could not be identified (a flood of bad tokens is throttled with 429 while signed-in people at the same address are untouched). Limits: `AIOPS_RATE_*` | `../../policy/verify_policy.py`, `tests/test_policy_engine.py` |
| `routes_map.py` | topology, segment states, devices, observations, KPIs and forecasts for the live map and analytics | `verify_api.py`, UI specs |
| `routes_incidents.py`, `routes_emergency.py` | incident lifecycle, owner and notes; calls, unit assignment with routes and their lifecycles - each attributed to the token's person and audited, with `expected_status` guards so two people cannot silently overwrite each other | `verify_operator_actions.py` |
| `routes_commands.py` | request (from a recommendation or directly), four-eyes review, the role a safety class needs; execution is never here - only `system:command-executor` executes; `POST /{id}/override` (P09.05, CTL-34: `incident_commander`, SC-2 only, mandatory justification, moves `executed` -> `rolled_back`, honest that no physical undo is attempted) | `verify_command_workflow.py`, `verify_audit_protection.py` |
| `routes_govern.py` | `GET /audit` (one newest-first trail, cursor pages), `GET /audit/export` (P09.05, CTL-17: `auditor` only, required and bounded `since`/`until`, redacts locations the ordinary trail still shows, capped and says so, itself an audited action), `GET /ops/status`, `/handovers` | `verify_govern.py`, `verify_audit_protection.py` |

`routes_commands.py` no longer picks the role that acts by scanning the caller's roles: it asks the policy engine (`control/policy.py` `authorise`), which names the role and the safety class, and every command decision is appended to `policy_decisions` (migration 0024). The executor asks again before it drives an adapter (`control/executor_worker.py`).

The `demo` and `fixtures` modules in `backend/demo/` build a demo world in a separate database (`aiops_demo`) and are not part of
the platform. Evidence: `docs/evidence/p08_04_evidence.json`, `p08_07_evidence.json`, `p08_08_evidence.json`, `p08_09_evidence.json`.

## `repositories/`

`incidents.py` and `commands.py`: each transition takes a row lock
(`SELECT ... FOR UPDATE`) *before* checking whether it's legal, so two
concurrent transition attempts on the same row serialize - the second
transaction sees the first one's committed status once it acquires the
lock, and is validated against *that*, not a stale pre-transition read.
This is what actually prevents a lost update, not just a UNIQUE constraint.

State machines (`ALLOWED_TRANSITIONS`) mirror `contracts/incident/v1` and
`contracts/command/v1`'s own status enums exactly - `test_repositories.py`
asserts the mapping covers every contract status, so an enum change in the
contract that isn't mirrored here fails loudly.

Commands: `create_command(..., idempotency_key)` is idempotent by
construction - a resubmission with the same key returns the *existing*
`command_id` (`created=False`), never a second row, matching ADR-0002's
"a resubmission with the same key must not execute twice" as an enforced
property, not documented intent. Transitioning to `approved` requires
`approved_by`; transitioning to `denied`/`failed` requires the structured
`error_code`/`error_message`/`error_retryable` fields - both refused
(`MissingAuditReference`) rather than silently leaving the audit trail
incomplete.

### Reproduction

```bash
cd source-code/infra/platform && bash up.sh   # P05.01/P05.04 stack must be running and migrated
cd ../../..
python source-code/backend/repositories/verify_repositories.py
```

Proves against the real database: a full valid incident transition chain
with a complete audit trail; an invalid transition is rejected and leaves
status unchanged; idempotent command creation with no duplicate row;
missing-audit-reference refusal for both `approved` and `failed`; and -
needing a real database, not a mock - two threads racing a transition on
the *same* incident row via separate connections, where exactly one
succeeds and the other correctly fails against the post-transition status
it saw after the lock released.

Fast pure-logic unit tests (state-machine coverage of every contract
status) are in `source-code/tests/test_repositories.py`.

Evidence: `docs/evidence/p05_08_repositories.json`.

## `scenario_control/`

A deliberately **separate** FastAPI app (not a router on P05.07's
operational API) for controlling scenario replay - start/status/reset/
replay - matching P08.09's "demo controls remain visibly separate" applied
to the backend, not just the UI. Wraps P03.07's already-proven
`simulator/manifest/replay.py` against the real manifest event streams;
"replay" re-runs that exact mechanism, not a reimplementation.

- **Authenticated and role-separated (P08.09)**: every request carries a Keycloak access token, validated by the operator
  API's own code (`backend/api/auth.py`), and the endpoint's capability `demo.control` - held by `demo_operator` alone - comes
  from the same UX inventory. The P05.09 placeholder that trusted an `X-Demo-Role` header the caller wrote is gone: the header is
  ignored, a forged one beside an operator's token changes nothing, a tampered token is 401, and every operating role is 403.
  There is deliberately no "authentication off" mode. Limit: the token audience is the shared `aiops-api`; a separate audience for
  this service is a P09 hardening item, not built here.
- **Bounded**: at most `MAX_CONCURRENT_RUNS` (2) runs may be `running`
  simultaneously, enforced via `pg_advisory_xact_lock` around the
  check-then-insert - a plain `SELECT COUNT(*) ... FOR UPDATE` cannot stop
  a *new* concurrent row from being inserted (it only locks rows that
  already exist), so the bound would otherwise leak under real concurrency.
- **Audited**: every start/list/status/reset/replay/audit call, allowed or refused - including requests with no valid identity
  - writes a row to `scenario_control_audit` (`0009`, extended by `0022` with the person who acted; append-only by trigger),
  independent of the operational audit trail. `GET /scenario-control/v1/audit` reads it back.
- **Harmless to operations**: a run reads the recorded manifest streams and reports what the replay accepted; it writes nothing to
  the operational tables (the verify script compares row counts before and after).

The operator UI reaches it through the Vite proxy at `/scenario-control` (`python source-code/backend/scenario_control/serve.py`,
port 8101).

### Reproduction

```bash
cd source-code/infra/platform && bash up.sh   # P05.01/P05.04 stack must be running and migrated
cd ../../..
python source-code/backend/scenario_control/verify_scenario_control.py
```

Proves against the real Keycloak, database and a real running server (real tokens for named demo people): no token, a forged
role header, a tampered token and every operating role are refused and audited; the demo operator starts a run that replays the
real 1,715-event P03.07 manifest and the run names them; replay is deterministic (identical `accepted_sha256`); reset succeeds;
the demo trail records each action and each refusal with the person and is not mixed into the operator trail; the demo controls
write nothing to the operational tables; and - fired as genuinely concurrent requests via `asyncio.gather`, not sequential calls
that could never overlap - starting `MAX_CONCURRENT_RUNS + 1` runs at once lets at most `MAX_CONCURRENT_RUNS` through and
rejects the rest with 429 and a reason. The demo operator's token reaches nothing in the operational API except the read-only
picture. Evidence: `docs/evidence/p08_09_demo_controls_evidence.json` (P05.09's original header-based evidence is superseded).
