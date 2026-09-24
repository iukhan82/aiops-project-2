# Policy engine (P09.03)

Open Policy Agent is the platform's policy decision point. The operator API, the scenario-control API and the command executor ask it;
nothing else decides, at run time, who may call an endpoint, who may request or review a command, or whether a command may be approved
or executed.

```
policy/
  aiops/api/authz.rego          may these roles call this endpoint?           (input: service, method, route template, roles)
  aiops/command/command.rego    request / review authority; approval and execution decisions
  aiops/model/data.json         GENERATED: roles, capabilities, endpoints, safety classes, adapters   (build_data.py)
  aiops/**/*_test.rego          46 unit tests, run by `opa test`
  build_data.py                 writes / checks data.json and the policy version
  verify_policy.py              acceptance run against the real engine, Keycloak, PostgreSQL and the real apps
```

## What is not in the policies

No role, route, capability or adapter is written in Rego. `build_data.py` generates the data document from the two places the platform
already keeps them - `frontend/src/config/inventory.json` (who holds which capability, which endpoint needs which) and
`backend/roles.py` / `backend/control/policy.py` (safety classes, request and approve roles, the adapter registry) - so a change there
reaches the engine with one command, and `python source-code/policy/build_data.py --check` (and a unit test) fails when the file on disk
is stale. `version` is a hash of the policy text and the data; every decision names it, and platform status compares the version the
engine serves with the one on disk.

## Decisions

| Question | Input | Answer |
|---|---|---|
| `aiops/api/authz/decision` | `service`, `method`, `path` (the route template, never the URL), `roles` | `allow`, `reason` (`public`, `permit`, `forbidden`, `unlisted_endpoint`, `no_operating_role`), `capability` |
| `aiops/command/authority` | `kind` (`request`/`review`), `action_type`, `critical_incident_on_target`, `roles` | `allow`, the `role` that holds the authority, the derived `safety_class`, `reason` |
| `aiops/command/decision` | `phase` (`approval`/`execution`) and the facts gathered by `backend/control/policy.py` | `approved` / `denied` / `expired`, `error_code`, `message`, `safety_class` |

Command checks run in a fixed order and the first failure decides, so the reason a person sees is stable: (execution only) the caller is
the executor, the command is `approved`, an approval is recorded; then expiry, four-eyes, the approver's role, the requester's role,
the adapter is registered, the target exists, (approval only) the recommendation, and last that the evidence is fresh. At execution the
approval is not taken on trust: the same checks run again against fresh facts.

The safety class is derived inside the policy from the action type and whether a critical incident is active on the target; a caller
cannot supply it. Anything malformed - a missing field, a wrong type, an unparseable time, an unknown phase - is the lowest-numbered
rule and denies. The policies deny by default and no rule can approve on an undefined comparison.

## Fail closed

`backend/pdp.py` is the only client. An engine that cannot answer is not an engine that said no: it raises `PolicyUnavailable`, and every
caller stops. The API answers 503 `policy_unavailable` (public health stays up); an approval leaves the command `requested`, labelled
policy-unavailable; the executor leaves an approved command `approved`, asks again after 15 s, and lets it expire by its own limit if
the engine stays down. A malformed answer (an empty result, HTML, a bare string, a permit that is not a boolean) is the same.
Every command decision - permit, refusal or outage - is appended to `policy_decisions` (append-only, migration 0024) with the input, the
policy version and the engine's own decision id.

## Running it

The engine is the `opa` service of `infra/platform/docker-compose.yml` (image pinned by digest, read-only, no capabilities, loopback
port 8181, decision log to the container console). `up.sh` waits until it has loaded the policy. After changing a policy or the tables:

```
python source-code/policy/build_data.py                    # regenerate data.json and the version
docker restart aiops-opa                                   # the engine loads the policy at start
python source-code/policy/verify_policy.py                 # the acceptance run (evidence: docs/evidence/p09_03_policy.json)
```

`verify_policy.py` runs `opa check --strict` and `opa test` from the pinned image, compares the engine with the Python reference on
every endpoint and role set, every request/review authority combination and 6,000 generated command contexts (decision, code and
message), then attacks the real API with real tokens: cross-role requests and reviews, targets aimed outside what an adapter controls,
each way an approval can be wrong at the executor's gate, the engine stopped and the engine answering nonsense. It stops and restarts the
`aiops-opa` container.

## Development-host note

Decision queries are sent as `GET /v1/data/<path>?input=<json>`, falling back to POST for an input too large for a URL. On the Windows
development host a POST to the engine in WSL takes about 44 ms whatever the engine does (the same POST takes 1.5 ms from inside WSL);
the delay is in the Windows-to-WSL localhost relay and does not affect a GET. A decision takes about 1.5 ms (p95 3 ms) measured this way.
