# Operator UI

The traffic-operations console: a React 19 + TypeScript (strict) single-page app on Vite, react-router 7 and keycloak-js. Plain CSS
on the generated design tokens, hand-built SVG for the map and charts, native `<dialog>` for confirmations. No UI framework, no map
tile service (the network is 12 intersections and 26 segments; see `docs/ux/DESIGN_SYSTEM.md` for why).

Every screen reads the real API. The one source of truth for who may see and do what is `src/config/inventory.json` (P08.01): the
backend derives its role-to-capability matrix and default-deny endpoint policy from it, this app derives navigation and route guards
from it, and `tools/inventory.py` proves the three agree. There is no mocked data anywhere in the app or its tests.

## Screens (21, all built)

| Group | Screens |
|---|---|
| Operate | Live operations map (and its accessible list), Incidents, Incident detail, Emergency dispatch, Dispatch detail, Field view |
| Analyse | Corridor analytics, Intersection analytics, Device health |
| Act | Recommendations, Commands and approvals, Command detail, Verified outcomes |
| Govern | Shift handover, Platform status, Audit trail |
| Demonstrate | Demo controls (a separate service, identity and audit trail) |
| Access | Sign in, Session ended / sign-in service unreachable, Not permitted, Not found |

`src/pages/registry.tsx` maps each inventory screen to its page; `registry.test.ts` fails if one is missing.

## Running it

The stack is real, so it needs the platform containers and a few processes. From the repository root (`source-code/` paths):

```sh
bash source-code/infra/platform/up.sh                         # Postgres, Kafka, MQTT, Keycloak (host port 8180); once
python source-code/infra/platform/keycloak/provision.py       # realm, clients, roles, demo identities (passwords -> git-ignored output/demo_identities.json)

python source-code/backend/demo/feeder.py --reset --start-offset-min 60   # demo world: separate database aiops_demo, live simulated feed
python source-code/backend/control/executor_worker.py                     # the only thing that executes an approved command
python source-code/backend/control/verifier_worker.py --window-minutes 5  # measures what an executed command did
python source-code/backend/demo/seed_actions.py                           # command histories (real and recorded ones are flagged in the data)
python source-code/backend/api/serve.py                                   # operator API, 127.0.0.1:8100 (refuses to start with auth off)
python source-code/backend/scenario_control/serve.py                      # demo controls, 127.0.0.1:8101

cd source-code/frontend && npm ci && npm run dev              # http://localhost:5173 (proxies /api and /scenario-control)
```

Sign in as any demo person (`alex.chen` operator, `sam.okafor` supervisor, `dana.rivera` dispatcher, `eve.laurent` incident commander,
`fin.hassan` field responder, `ana.petrov` auditor, `dee.moreno` demo operator, plus `alex.two` and `sam.two` as second people for
four-eyes flows). Their generated passwords are in `source-code/infra/platform/output/demo_identities.json`; nothing is committed.

Ports on this development host: Keycloak 8180 (8080 is taken by other projects), API 8100, scenario control 8101, Vite 5173.

## Checks

```sh
npm run check        # tsc --noEmit + vitest (41 unit tests: formatting, status vocabulary, access rules, lifecycles, wording)
npm run e2e          # real Chrome against the real stack; workers=1 because the specs share one demo world
python tools/inventory.py      # inventory vs roles, capabilities and the endpoints the apps really serve
python tools/design.py         # tokens, computed contrast pairs, status vocabulary vs backend enumerations, component docs
python tools/verify_wireframes.py
python tools/e2e_evidence.py --task P08.10 --name p08_10_ui_acceptance --metrics .metrics/p08_10.json   # after an e2e run
```

The e2e specs (`e2e/`), each on real Keycloak (Authorization Code with PKCE), the real API and a real Chrome, with `axe` on WCAG 2.2 AA:

| Spec | What it proves |
|---|---|
| `auth` | sign-in/out, deep links, reload with single sign-on, session ended elsewhere, per-role navigation and denials |
| `map`, `analytics` | live feed with reconnect and gap-free catch-up, freshness and truth labels, accessible list, replay, keyboard use |
| `incidents`, `dispatch` | ownership, transitions, notes, evidence, calls, units, routes with ETA and uncertainty, field view is read-only |
| `actions` | request, four-eyes approval, policy outage, executor and verifier states, outcomes, every confirmation |
| `govern` | audit filters and refusals, platform status probes, handover write/acknowledge/conflict, demo controls and their separation |
| `acceptance` | UX-01 and UX-04 across every screen of every role: axe, keyboard, focus indicators, reduced motion, five viewports incl. 200% and 400% zoom |
| `failures` | API unreachable, screen that cannot load, stale data, session ended, identity provider unreachable |
| `latency` | LAT-05 and LOAD-04 measured with real events and ten concurrent sessions |

Fixtures for the tests come from `backend/demo/fixtures.py` and go through the platform's own paths (ingestion, repositories, the API with a
real token), never around them.

## Conventions worth knowing

- The access token lives in memory only; a reload re-checks the identity provider once (single sign-on) instead of asking again, and the
  destination survives sign-in.
- Every state a backend record can be in is shown as shape + word + tone (`src/design/status.json`, checked against the real enumerations);
  colour is never the only signal. Truth labels and freshness are shown beside every value; ages are computed from the clock so a screen
  cannot go on looking live.
- A write is one `useAction` at a time (no double submit), goes through a confirmation that restates what will happen and defaults to
  Cancel, and shows the server's own answer in place. There are no optimistic updates.
- Times are UTC and numbers use one fixed format, independent of the browser locale.
