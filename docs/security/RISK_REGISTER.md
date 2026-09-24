# Risk register

Task P09.01. Generated from `source-code/security/threat_model.json` and `controls.json` by
`source-code/security/verify_threat_model.py --render`. Each row is a threat from `THREAT_MODEL.md` with its owner, due date and
residual risk; the safety and security residuals are shown separately. Nothing here is edited by hand.

**How residual risk is computed.** Likelihood (low 1, medium 2, high 3) times the larger of the safety and security impacts
(none/low 1, medium 2, high 3). An implemented control lowers likelihood one step (two at most); a partial or planned control lowers
nothing. High is a score of 6 or more, medium 3-4, low 1-2. A threat with a written acceptance rationale stays in the list.

**Reading the "Open work" column.** It lists the controls that treat the threat and are not yet implemented, with their state and the
task that builds them. When that task is done and its evidence passes, the row's residual falls the next time the tool runs.

## Summary

<!-- BEGIN GENERATED: risk-summary -->

| Level | Inherent | Residual |
|---|---|---|
| high | 27 | 5 |
| medium | 42 | 48 |
| low | 7 | 23 |
| **threats** | 76 | 76 |

<!-- END GENERATED: risk-summary -->

<!-- BEGIN GENERATED: treatment-summary -->

| Threats treated by implemented controls only | Partially treated | Open (no implemented control) |
|---|---|---|
| 52 | 13 | 11 |

| Controls implemented | Partial | Planned |
|---|---|---|
| 31 | 8 | 8 |

<!-- END GENERATED: treatment-summary -->

## Register, highest residual first

<!-- BEGIN GENERATED: risk-register -->

| Threat | Boundary | Inherent | Residual | Safety / security residual | Treatment | Owner | Due | Open work |
|---|---|---|---|---|---|---|---|---|
| T-20 Database credentials are read from configuration or the process environment | B05 | high (6) | high (6) | medium / high | open | SEC | 2026-09-30 | CTL-14 (partial, P09.04); CTL-11 (planned, P09.04); CTL-12 (planned, P09.04). |
| T-27 A user of one agency reads or changes another agency's records | B06 | high (6) | high (6) | medium / high | open | SEC | 2026-10-05 | CTL-47 (planned, P09.06). Accepted: This prototype is one operating organisation: emergency-service collaboration is modelled as handover messages (P07.03), not as separate user populations, so no user can belong to a different agency. It becomes a real gap the day a second agency signs in. |
| T-59 A secret is committed or a CI token is abused | B12 | high (6) | high (6) | low / high | open | DEVOPS | 2026-10-05 | CTL-14 (partial, P09.04); CTL-28 (partial, P01.05). |
| T-71 Lateral movement between services | B17 | high (6) | high (6) | medium / high | open | SEC | 2026-10-12 | CTL-13 (planned, P11.02); CTL-11 (planned, P09.04); CTL-12 (planned, P09.04). |
| T-72 Secrets are readable from cluster or host state | B17 | high (6) | high (6) | medium / high | open | SEC | 2026-10-12 | CTL-14 (partial, P09.04); CTL-13 (planned, P11.02). |
| T-15 Redelivery duplicates or reorders events | B04 | high (6) | medium (4) | low / medium | treated | DATA | 2026-09-28 | none. |
| T-16 An unauthorized consumer reads the stream | B04 | medium (4) | medium (4) | low / medium | open | SEC | 2026-09-30 | CTL-11 (planned, P09.04); CTL-13 (planned, P11.02). |
| T-55 Service quality is unequal across areas or road users | B11 | medium (4) | medium (4) | medium / medium | open | QA | 2026-10-19 | CTL-45 (partial, P12.04). |
| T-68 A wrong or runaway remediation worsens an outage | B16 | medium (4) | medium (4) | medium / medium | open | OPS | 2026-10-05 | CTL-38 (planned, P10.08). |
| T-73 Malicious runtime behaviour goes unnoticed | B17 | medium (4) | medium (4) | low / medium | open | SEC | 2026-09-30 | CTL-30 (partial, P09.09). |
| T-04 Camera-derived data or tracks identify individuals | B01 | high (6) | medium (3) | low / medium | treated | GRC | 2026-09-30 | none. |
| T-08 The edge loses the network, identity or central services | B02 | high (6) | medium (3) | medium / low | treated | EDGE | 2026-09-28 | none. |
| T-10 A device publishes under another device's identity or topic | B03 | high (6) | medium (3) | low / medium | treated | DATA | 2026-09-28 | none. |
| T-14 A poisoned or conflicting event overwrites a stored observation | B04 | high (6) | medium (3) | low / medium | treated | DATA | 2026-09-28 | none. |
| T-19 A person denies having requested, approved or denied a command | B05 | high (6) | medium (3) | low / medium | partially treated | SEC | 2026-09-28 | CTL-36 (partial, P11.03). |
| T-21 Audit details or exports carry tokens, identifiers or locations | B05 | high (6) | medium (3) | low / medium | treated | SEC | 2026-09-28 | none. |
| T-25 A forged, expired or wrong-audience token is accepted | B06 | high (6) | medium (3) | medium / medium | treated | SEC | 2026-09-28 | none. |
| T-26 A role calls a capability it does not hold | B06 | high (6) | medium (3) | medium / medium | treated | SEC | 2026-09-28 | none. |
| T-30 Mass assignment: a client names its own author, requester or status | B06 | high (6) | medium (3) | low / medium | treated | BACKEND | 2026-09-28 | none. |
| T-32 A new endpoint ships without an authorization decision | B06 | high (6) | medium (3) | medium / medium | treated | BACKEND | 2026-09-28 | none. |
| T-35 The screen shows old or cached data as if it were live | B07 | high (6) | medium (3) | medium / low | treated | UI | 2026-09-28 | none. |
| T-38 Passwords are guessed or stuffed against operator accounts | B08 | high (6) | medium (3) | medium / medium | treated | SEC | 2026-09-28 | none. |
| T-41 The realm drifts from the reviewed configuration | B08 | high (6) | medium (3) | low / medium | treated | SEC | 2026-09-28 | none. |
| T-43 A protected action skips the policy decision | B09 | high (6) | medium (3) | medium / medium | treated | SEC | 2026-09-28 | none. |
| T-46 A command is aimed at a target it was not approved for | B10 | high (6) | medium (3) | medium / medium | treated | CONTROL | 2026-09-28 | none. |
| T-47 A requester approves their own command, or two low roles collude | B10 | high (6) | medium (3) | medium / medium | treated | CONTROL | 2026-09-28 | none. |
| T-48 An old approved command is replayed | B10 | high (6) | medium (3) | medium / low | treated | CONTROL | 2026-09-28 | none. |
| T-50 An action makes things worse and is not undone | B10 | high (6) | medium (3) | medium / low | treated | CONTROL | 2026-09-28 | none. |
| T-56 Operators over-rely on a recommendation | B11 | high (6) | medium (3) | medium / low | treated | UI | 2026-09-28 | none. |
| T-57 A malicious or vulnerable dependency is installed | B12 | high (6) | medium (3) | low / medium | partially treated | DEVOPS | 2026-10-05 | CTL-27 (partial, P09.08); CTL-28 (partial, P01.05). |
| T-75 Logs, metrics or traces contain tokens or personal identifiers | B18 | high (6) | medium (3) | low / medium | partially treated | OPS | 2026-10-05 | CTL-37 (partial, P10.01). |
| T-06 A tampered model is activated at an edge site | B02 | medium (3) | medium (3) | low / medium | treated | EDGE | 2026-09-28 | none. |
| T-09 Raw camera frames or intermediate data are retained locally | B02 | medium (3) | medium (3) | low / medium | treated | EDGE | 2026-09-30 | none. |
| T-13 A certificate from an untrusted authority is accepted | B03 | medium (3) | medium (3) | low / medium | treated | SEC | 2026-09-28 | none. |
| T-18 Audit or history rows are rewritten to hide an action | B05 | medium (3) | medium (3) | low / medium | treated | SEC | 2026-09-28 | none. |
| T-22 SQL injection through an API parameter | B05 | medium (3) | medium (3) | low / medium | treated | BACKEND | 2026-09-28 | none. |
| T-23 Data is lost with no restorable backup | B05 | medium (3) | medium (3) | low / medium | open | DEVOPS | 2026-10-12 | CTL-35 (planned, P11.05). |
| T-33 Cross-site scripting steals or misuses the session | B07 | medium (3) | medium (3) | medium / medium | treated | UI | 2026-09-28 | none. |
| T-34 The console is framed or spoofed by another origin | B07 | medium (3) | medium (3) | medium / medium | treated | UI | 2026-09-28 | none. |
| T-36 Phishing or an open redirect during sign-in | B07 | medium (3) | medium (3) | low / medium | treated | SEC | 2026-09-28 | none. |
| T-39 A client or redirect misconfiguration leaks tokens or codes | B08 | medium (3) | medium (3) | medium / medium | treated | SEC | 2026-09-28 | none. |
| T-42 The administrator console or its credentials are exposed | B08 | medium (3) | medium (3) | low / medium | open | SEC | 2026-09-30 | CTL-14 (partial, P09.04). |
| T-44 The policy engine fails open | B09 | medium (3) | medium (3) | medium / medium | treated | SEC | 2026-09-28 | none. |
| T-45 The policy rules or their data are altered | B09 | medium (3) | medium (3) | medium / medium | treated | SEC | 2026-09-28 | none. |
| T-49 A signal change creates an unsafe state | B10 | medium (3) | medium (3) | medium / low | treated | CONTROL | 2026-09-28 | none. |
| T-51 A person or another service reaches an adapter | B10 | medium (3) | medium (3) | medium / medium | treated | CONTROL | 2026-09-28 | none. |
| T-58 A tampered image or artifact is deployed | B12 | medium (3) | medium (3) | low / medium | partially treated | DEVOPS | 2026-10-05 | CTL-27 (partial, P09.08). |
| T-60 An insider approves harmful commands | B13 | medium (3) | medium (3) | medium / medium | treated | SEC | 2026-09-28 | none. |
| T-61 An override is used without a record | B13 | medium (3) | medium (3) | medium / medium | treated | SEC | 2026-09-28 | none. |
| T-64 A false public warning is sent through a notification channel | B14 | medium (3) | medium (3) | medium / medium | treated | ARCH | 2026-09-30 | none. Accepted: No public-warning adapter exists; nothing can be sent to the public. Building one would need its own threat review. |
| T-66 Demo controls reach the operational command path | B15 | medium (3) | medium (3) | medium / medium | treated | SEC | 2026-09-28 | none. |
| T-69 The remediation identity is used to reach traffic actions | B16 | medium (3) | medium (3) | medium / medium | partially treated | SEC | 2026-10-05 | CTL-38 (planned, P10.08). |
| T-70 A container escape or over-privileged workload | B17 | medium (3) | medium (3) | low / medium | partially treated | SEC | 2026-10-12 | CTL-13 (planned, P11.02); CTL-30 (partial, P09.09). |
| T-01 A device is impersonated to inject false readings | B01 | medium (4) | low (2) | low / low | treated | SEC | 2026-09-28 | none. |
| T-02 Captured telemetry is replayed | B01 | medium (4) | low (2) | low / low | treated | DATA | 2026-09-28 | none. |
| T-05 A faulty or compromised device floods the platform | B01 | medium (4) | low (2) | low / low | treated | DATA | 2026-09-28 | none. |
| T-11 A client subscribes to other devices' topics | B03 | medium (4) | low (2) | low / low | treated | DATA | 2026-09-28 | none. |
| T-12 The broker or gateway is flooded or a consumer stalls | B03 | medium (4) | low (2) | low / low | treated | DATA | 2026-09-28 | none. |
| T-17 Ingestion lag hides fresh data behind old data | B04 | medium (4) | low (2) | low / low | treated | OPS | 2026-09-28 | none. |
| T-24 Location or trajectory data is kept beyond its purpose | B05 | medium (4) | low (2) | low / low | treated | GRC | 2026-09-30 | none. |
| T-28 Responses carry more than the caller needs | B06 | medium (4) | low (2) | low / low | treated | BACKEND | 2026-09-28 | none. |
| T-29 A request flood or oversized body exhausts the API | B06 | medium (4) | low (2) | low / low | treated | SEC | 2026-09-28 | none. |
| T-31 The access token in the WebSocket URL is logged or cached | B06 | medium (4) | low (2) | low / low | partially treated | SEC | 2026-10-05 | CTL-37 (partial, P10.01). Accepted: Tokens live 300 s and are audience-bound; the alternative (a ticket endpoint) is recorded as a design option for P11 when a real reverse proxy exists. |
| T-37 A demonstration screen or simulated data is mistaken for real operations | B07 | medium (4) | low (2) | low / low | treated | UI | 2026-09-28 | none. |
| T-40 The identity provider is down when tokens expire | B08 | medium (4) | low (2) | low / low | treated | SEC | 2026-09-28 | none. |
| T-52 The adapter is unreachable or does not acknowledge | B10 | medium (4) | low (2) | low / low | treated | CONTROL | 2026-09-28 | none. |
| T-54 A model drifts or is fooled by unusual input | B11 | medium (4) | low (2) | low / low | partially treated | OPS | 2026-10-05 | CTL-44 (planned, P10.06). |
| T-62 Accounts keep roles they no longer need | B13 | medium (4) | low (2) | low / low | partially treated | GRC | 2026-09-30 | CTL-46 (planned, P09.07). |
| T-74 Resource exhaustion starves safety-critical services | B17 | medium (4) | low (2) | low / low | partially treated | DEVOPS | 2026-10-12 | CTL-13 (planned, P11.02). |
| T-03 A device is stolen or opened and its key extracted | B01 | low (2) | low (2) | low / low | partially treated | SEC | 2026-09-30 | CTL-39 (partial, P11.02). |
| T-07 The edge outbox is altered on disk | B02 | low (2) | low (2) | low / low | treated | EDGE | 2026-09-28 | none. |
| T-53 Training data is poisoned or leaks into evaluation | B11 | low (2) | low (2) | low / low | treated | SIM | 2026-09-28 | none. |
| T-63 A forged CAD or AVL feed creates false calls or moves units | B14 | low (2) | low (2) | low / low | treated | ARCH | 2026-09-30 | none. Accepted: No real CAD/AVL integration is authorized or connected; the prototype's feeds are simulated and labelled as such. Any real adapter needs its own authenticated, tested integration before this risk applies. |
| T-65 An integration credential leaks | B14 | low (2) | low (2) | low / low | partially treated | SEC | 2026-09-30 | CTL-14 (partial, P09.04). |
| T-67 Demo activity is mixed into the operational audit trail | B15 | low (2) | low (2) | low / low | treated | SEC | 2026-09-28 | none. |
| T-76 Telemetry is poisoned to mislead operators or AIOps | B18 | low (2) | low (2) | low / low | partially treated | OPS | 2026-10-05 | CTL-38 (planned, P10.08). |

<!-- END GENERATED: risk-register -->
