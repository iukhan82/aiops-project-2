# Keycloak realm `aiops` (P09.02, pulled forward for P08.04)

The identity provider for the operator UI and the APIs: Keycloak 26.0.8 (image pinned by digest in `../docker-compose.yml`), run in
`start-dev` mode on host port **8180** (8080 is used by other projects on this development host). Everything here is for local
development and demonstration; production hardening (TLS at the provider, production mode, secret rotation, network policy) belongs to
P09.04 and is not claimed here.

## What the realm holds

`realm/aiops-realm.json` is imported when the container starts and contains **no secret**.

| Thing | Setting |
|---|---|
| Realm | `aiops`, self-registration off, brute-force protection (5 failures), refresh-token rotation with no reuse, access token 300 s, single-sign-on idle 30 min |
| Roles | the seven human operating roles of `docs/security/ROLES_AND_ACTION_AUTHORITY.md` (`operator`, `supervisor`, `dispatcher`, `incident_commander`, `field_responder`, `auditor`, `demo_operator`) and five service identities (`system:command-executor`, `system:outcome-verifier`, `system:optimization-engine`, `system:emergency-engine`, `system:aiops-remediation`) |
| `aiops-ui` | public client, Authorization Code with **PKCE S256 only**, exact redirect URIs (`<origin>/callback`), post-logout `<origin>/login`, no implicit or password grant; tokens carry the `aiops-api` audience (mapper) and only the client's scope-mapped roles (`fullScope` off) |
| `aiops-api` | confidential audience client for the APIs |
| `system-*` | five confidential service clients (client-credentials only); their generated secrets go to the git-ignored `output/service_client_secrets.json` |

The people are **not** in the realm file. `provision.py` creates one demo person per role (plus `sam.two` and `alex.two`, because four-eyes
needs two different people of a role) with a random password from `secrets`, written only to the git-ignored `output/demo_identities.json`.
It is idempotent, and a lost file is recoverable: users whose password is not on file are reset.

## How the APIs use it

`backend/api/auth.py` validates every request strictly: RS256 signature against the realm's JWKS (cached), issuer, audience `aiops-api`,
`exp`/`iat`/`sub` required, an authorised party we issued, and at least one known operating role. Then the endpoint's capability from the
UX inventory must be held by the roles: 401 when unauthenticated, 403 when authenticated but not permitted (audited), 503 when the
provider cannot be reached (fail closed). The scenario-control API (`backend/scenario_control`) uses the same code with the
`demo.control` capability. The browser keeps the access token in memory only.

## Running and proving it

```sh
bash source-code/infra/platform/up.sh                       # brings the stack up (Keycloak is healthy in about 30 s)
python source-code/infra/platform/keycloak/provision.py     # demo identities; run again any time
python source-code/infra/platform/keycloak/verify_keycloak.py
python source-code/backend/api/verify_auth.py
```

`verify_keycloak.py` reads the *running* realm back through the admin API and compares it with the written requirements (drift between
the file and the realm is caught), then attacks the flow: no or plain PKCE, implicit and password grants, an unregistered redirect, a
wrong verifier, a replayed code, a replayed refresh token, account guessing. Every attack must be refused. `verify_auth.py` proves the API
side with real tokens: wrong audience, expired, tampered, `alg=none` and HS256-with-the-public-key tokens, unknown client, no role, an
unlisted endpoint, and every role against every endpoint of the inventory. Evidence: `docs/evidence/p09_02_evidence.json`,
`docs/evidence/p08_04_evidence.json`.

## Development quirks worth knowing

- Keycloak sets `Secure` cookies even on `http://localhost`; browsers accept them for localhost, the Python client uses `127.0.0.1`.
- Replaying an authorization code makes Keycloak revoke the tokens issued from it. The verify scripts test that once, on purpose.
- The Docker VM on this host idles out unless something holds it (a long-running `wsl.exe` command); a stopped VM looks like an identity
  provider outage.
