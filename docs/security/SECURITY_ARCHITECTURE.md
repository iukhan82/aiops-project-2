# Security architecture

Task P09.01. How the platform is protected, in layers, and which control (`CTL-nn`, catalogued in `source-code/security/controls.json`)
does each job. The state of every control - implemented, partial or planned - and the evidence behind it is in `THREAT_MODEL.md` and
`RISK_REGISTER.md`; this document explains the design and does not repeat the state, so it does not go stale when a control lands.
Design decisions and their trade-offs are at the end.

## Principles

1. **A person decides, a service acts.** Recommendations, requests, approvals and execution are four different authorities held by
   different identities (`ROLES_AND_ACTION_AUTHORITY.md`). No human role executes; no service approves.
2. **Default deny, everywhere a decision is made.** An unlisted endpoint, an unknown role, an unreachable identity provider or policy engine,
   an expired or unverifiable input: the answer is no, and it is recorded.
3. **Check twice, at different times, by different code.** A recommendation is bounded when it is generated (CTL-20); the same action is
   checked again when it is approved (CTL-06) and its result is verified independently afterwards (CTL-21).
4. **Evidence outlives its author.** Audit and state histories are append-only at the database (CTL-15), attributed to a person from the
   verified token, never from what the client says (CTL-32), and readable only by a role that cannot change anything (CTL-17).
5. **Simulated stays labelled.** Every value carries its truth label and age; a demonstration identity and the demo service are visibly and
   structurally separate (CTL-08, CTL-23).

## Layers

### Identity plane

- **Keycloak** issues short-lived (300 s) RS256 access tokens through the authorization code flow with PKCE S256 for the browser; there are
  no implicit, hybrid or password grants, redirects are exact, brute force locks the account, and refresh tokens rotate (CTL-02).
- **The API validates every token strictly**: signature against the realm's published keys, issuer, audience, expiry, authorised party and
  operating role; a provider that cannot be reached fails closed with 503 (CTL-01).
- **The browser holds the token in memory only** and never asks for a password itself (CTL-43).
- **Services have their own identities** as confidential Keycloak clients (`system:command-executor`, `system:outcome-verifier`, ...).
  Using them for the workers' own authentication is CTL-12.

### Authorization plane

- **Policy enforcement point:** the API's global dependency authenticates and then asks whether the caller's roles hold the capability the
  endpoint needs. The capability table is generated from one inventory shared by the API, the UI and the wireframes, so the three cannot
  drift; a route that is not in it is denied (CTL-03).
- **Policy decision point:** Open Policy Agent, running as its own container (`aiops-opa`, image pinned by digest, read-only, no
  capabilities, loopback only). It answers three questions and nothing else decides them at run time: may these roles call this
  endpoint; may one of these roles request or review a command of this action type; may this command be approved, and may it be
  executed. The policies (`source-code/policy/`) carry no role, route or adapter of their own - they read tables generated from the UX
  inventory and `backend/roles.py`, so the enforcement points, the UI and the engine cannot disagree, and the Python implementation of
  the same rules is kept as the oracle a differential test compares the Rego with (CTL-04, CTL-05, CTL-06).
- **Fail closed, and say so:** an engine that cannot answer is not one that said yes or no. The API answers 503 `policy_unavailable`,
  an approval leaves the command requested and labelled policy-unavailable, and the executor holds an approved command instead of
  running it (it expires by its own time limit if the engine stays down). Public health stays up. There is no in-process fallback.
- **Command authority:** the requester is never the approver; the approver's role must hold APPROVE for the safety class; the executor
  identity alone performs the action; the demo operator cannot reach any of it (CTL-05, CTL-07, CTL-08).
- **Hardening at the edge of the API** - rate limits, body-size limits, security headers and a closed cross-origin policy - is CTL-33.

### Transport and device plane

- Devices authenticate to the broker with per-device client certificates and can publish and subscribe only on their own topics; an
  untrusted authority is refused (CTL-09).
- Everything arriving is validated for schema and registered identity; a conflicting event never overwrites the original; a replay is
  idempotent (CTL-10).
- Links inside the platform (stream, database, services) are plain in the developer profile and protected in P09.04 (CTL-11); the cluster's
  RBAC, default-deny network policy, non-root read-only workloads and admission checks are CTL-13 (P11.02).

### Data plane

- The four audit logs nothing ever deletes from (operator, scenario-control, policy-decision, retention-run) refuse UPDATE, DELETE and
  TRUNCATE by trigger, and each row also carries a hash of the row before it, so a party with enough privilege to defeat the trigger and
  rewrite history is still caught by an independent recompute over the stored content (CTL-15, `backend/audit_chain.py`) - a forensic
  check, not a second enforcement point. Command and incident history (and the two emergency ones) get the same chain and the same
  UPDATE/TRUNCATE refusal, but deliberately not a DELETE refusal: retention legitimately deletes a terminal command or a resolved incident
  past its window, cascading its history with it, and `retention_runs` records every purge so that gap is accounted for, not just absent.
- What may be written to audit detail, a policy decision record, or a log line is redacted before it is stored - by key name and by shape
  (a JWT, a bearer credential, a private key, `password=` in text, a location) - and the database refuses a JWT or private key that
  reaches it anyway, as a backstop the application's own redaction cannot be routed around by a missed call site (CTL-16).
- Reading the trail is limited to the auditor role; an export additionally redacts locations the live trail still shows (an auditor working
  an incident needs to see what was acted on; a bundle leaving the platform does not), is bounded and capped, and is itself an audited,
  attributed action (CTL-17).
- Countermanding an executed SC-2 command before independent verification completes is `incident_commander`'s alone, needs a written
  justification, and is honest that this prototype's one-shot adapter has no held session to drive a further physical reversal - it
  records the countermand, not a fabricated undo (CTL-34).
- Retention windows per data class, aggregation before deletion, and audit that is never purged (CTL-18); privacy-preserving edge processing
  so that no camera frame or identifying track reaches the centre (CTL-19).
- Request bodies are closed schemas; cursors and time ranges are validated; search text is escaped; the client never names an author
  (CTL-32).

### Safety plane

The command path adds physical-world controls that no generic security layer provides: safety bounds at generation (CTL-20), safe-by-
construction pre-emption and an independent signal-safety monitor (CTL-22), expiry and idempotency (CTL-24), an independent verifier with
rollback (CTL-21), and honest degraded behaviour - a policy outage leaves a command waiting, the UI never shows old data as live, the edge
buffers and keeps a safe plan (CTL-06, CTL-23, CTL-41).

### Supply chain and runtime

Exact dependency locks and digest-pinned images, including the edge runtime image (CTL-27), CI scanning (CTL-28), and P09.08's SBOM, Trivy
scan, bandit SAST, signed build provenance and a proven local admission gate (CTL-29) - the gate is proven by verifying, twice, that the
same signature admits a genuine artifact and rejects a one-byte-tampered copy; `admission-policy.yaml` carries the identical check into
Kyverno for P11.02's cluster, not yet applied because that cluster does not exist. Runtime detection with Falco (CTL-30) was proven on a
real Linux target in P01.07 and is scoped to the platform in P09.09.

## Decisions and trade-offs

| Decision | Why | What it costs |
|---|---|---|
| One inventory drives API authorization, UI navigation and wireframes | Three copies of an access matrix drift; one file cannot | The inventory is a single point of correctness; changing it needs the verifier |
| Roles are global in the token; agency scoping is not enforced per user (T-27, CTL-47) | The prototype has one operating organisation; emergency-service collaboration is modelled as handover messages, not as separate user populations | A user of one agency could read another's records the day a second agency signs in. Accepted in writing for this scope; the engine is in place, so the rule is a policy and data change |
| The access token rides in the WebSocket query string | A browser cannot set headers on a WebSocket | Proxies and logs may record it; mitigated by a 300 s lifetime and audience binding; a ticket endpoint is the recorded alternative (T-31) |
| Internal links are plain in the developer profile | Compose on one workstation; TLS everywhere would cost setup time without a real network | The threat is stated and treated in P09.04, not hidden |
| Residual risk is computed and a planned control earns no credit | A gap must never look like protection | Registers look worse than a hand-written one would, and are honest |
| Demo controls are a separate service with their own identity and audit trail | A demonstration path must be structurally unable to reach operations | One more service to run and secure |

## Out of scope

Real controller integration, real emergency-service integration, public warnings, and production-grade high availability of the identity
provider are designed but not built or claimed. Certification to any standard is not claimed anywhere.
