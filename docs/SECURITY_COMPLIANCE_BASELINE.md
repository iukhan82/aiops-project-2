# Security, privacy, safety and compliance baseline

## Required artifacts

- Security and privacy scope plus accountable owners.
- Asset/data inventories and data-flow/trust-boundary diagrams.
- Risk register with likelihood, impact, treatment, owner, due date and residual
  risk; safety impact is evaluated separately from information-security impact.
- Threat model covering device, edge, broker, stream, API, web, identity, policy,
  model, supply chain, operator, controller adapter and external integration.
- GDPR-oriented data inventory, lawful-purpose assumptions, retention schedule,
  privacy-by-design decisions, rights workflow, breach procedure and DPIA-style
  assessment for video/trajectory/location processing.
- ISO/IEC 27001-style control applicability mapping and evidence, explicitly not
  certification.
- IoT/OT device security profile and lifecycle/update/support policy.
- Secure development, vulnerability management, incident response, backup,
  recovery, access review, supplier and change-management procedures.

## Technical control baseline

1. Unique device/workload/user identity; no shared production credentials.
2. mTLS for devices/services where feasible; TLS for external interfaces.
3. Default-deny network, stream/topic, API and action policies.
4. Least-privilege service accounts, Kubernetes RBAC and network policies.
5. Short-lived tokens, MFA readiness, PKCE, strict issuer/audience/expiry checks.
6. Secrets generated at deployment, stored outside Git, rotated and redacted from
   logs, audits, errors and evidence bundles.
7. Signed versioned commands/configurations, replay protection, expiry,
   idempotency, bounds, approval separation and break-glass audit.
8. Secure update and rollback for edge software, models and configuration.
9. Immutable or append-only protected audit evidence with synchronized time.
10. Data minimization, aggregation, pseudonymization, privacy zones, role-limited
    evidence access and enforced retention/deletion.
11. Pinned dependencies/base images, secret scanning, SAST, dependency/IaC/image
    scanning, SBOM, provenance, image signing and admission verification.
12. Runtime detection, asset state awareness, certificate/configuration drift and
    incident-response integration.
13. Rate limits, input/schema validation, output encoding, CSRF/CORS controls,
    SSRF/command-injection defenses and secure file handling.
14. Encrypted, tested backups with restore evidence and restricted access.
15. Safe local traffic plan on loss of central identity, policy, network,
    prediction, or controller acknowledgement.

## AI and data controls

- Dataset provenance, licences, split integrity, leakage checks, class balance and
  protected ground truth.
- Transparent baseline comparison, uncertainty, abstention, failure cases, model
  card, integrity hash, staged activation, shadow/canary evaluation and rollback.
- Drift, latency, confidence collapse, feature loss and data-quality monitoring.
- Human authority and appeal/override for consequential recommendations.
- Geographic, temporal and road-user performance analysis to detect unfair
  service degradation.
- A generative assistant, if added, is retrieval-grounded, cites sources, cannot
  execute commands and cannot expose restricted incident data.

## Evidence rule

A policy document proves intent, not enforcement. Each implemented control needs
configuration/code evidence plus a meaningful positive/negative runtime test.
Record gaps and compensating controls; never label the academic prototype GDPR,
ISO/IEC 27001, IEC 62443, ETSI, OWASP or NIST compliant/certified.
