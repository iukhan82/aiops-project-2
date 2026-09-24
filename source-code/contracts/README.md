# Versioned contracts

Owner: LEAD, coordinating factual fields with DATA and each service owner
(`docs/FOLDER_STRUCTURE.md`). This directory holds JSON Schema contracts for
cross-service events and records. Task P02.03 defines the first two: device
identity and the generic observation envelope. Later tasks (P02.04-P02.07)
add geometry/signal, incident/command, emergency, and observability/policy
contracts as siblings of this directory, reusing these two where a
measurement or a device reference is needed.

## Versioning policy

- Each contract lives under `<name>/v<major>/schema.json`. A breaking change
  (removed/renamed required field, narrowed type, changed enum meaning) gets
  a new `v<major+1>` directory; the old version stays until every producer
  and consumer has migrated. A backward-compatible addition (new optional
  field, widened enum) stays in the same version directory and bumps the
  schema's own `schema_version` string (`major.minor.patch`, matched by the
  `schema_version` const in the file).
- Every contract ships `examples/valid-*.json` (must pass) and
  `examples/invalid-*.json` (must fail, with a comment key `_invalid_because`
  removed by the test loader before validation) so compatibility is a test,
  not a claim.
- All contracts use JSON Schema 2020-12, `additionalProperties: false` at the
  top level, and explicit `format: date-time` (UTC, RFC 3339) for every
  timestamp field, per the engineering invariants in
  `docs/PROJECT_CONTEXT.md`.

## Shared vocabulary

- **Truth label** (`truth_label` on the observation envelope): one of
  `simulated`, `measured`, `inferred`, `predicted`, `operator_entered`,
  `verified`, exactly the set in `docs/PROJECT_CONTEXT.md`. This describes
  the epistemic status of a data *value* and must not be confused with...
- **Deployment type** (`deployment_type` on the device record): `simulated`
  or `real`, describing whether the *device itself* is a SUMO-driven
  simulated entity or a physical/real integration. Every device in this
  project is `simulated` until a real integration is separately authorized
  (`AGENTS.md`).
- **Privacy classification** (`privacy_classification`, both contracts):
  `none`, `aggregated`, `pseudonymous`, `sensitive`, matching the retention
  and minimization intent in `docs/SECURITY_COMPLIANCE_BASELINE.md`.
- **Retention class** (`retention_class`, both contracts): `short`,
  `standard`, `extended`, `audit`; final numeric retention periods are set in
  P05.10, this field only names the class.

## Verification

```
pytest source-code/tests/test_contracts.py
```

The test loads every schema, validates every `valid-*.json` example against
it, and asserts every `invalid-*.json` example fails validation with a
specific, checked reason.
