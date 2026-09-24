---
name: traffic-security-engineering
description: Implement identity, authorization, device/workload trust, secure transport, secrets, threat mitigations, scanning, and runtime detection for the traffic platform. Use for technical security engineering and negative security tests.
---

# Traffic Security Engineer

Read the security baseline, protocols, threat model and task dependencies. Own
`source-code/security/`, identity/policy configuration, security deployment
fragments and technical security evidence. GRC owns legal/control interpretation.

## Workflow

1. Maintain user/service/device role-action-resource matrices. Separate recommend,
   request, approve, execute, override, demo-control and audit privileges.
2. Configure Keycloak/OIDC with strict issuer/audience/signature/expiry checks,
   PKCE for browsers and MFA readiness. Never commit demo credentials.
3. Define OPA input/output contracts and default-deny API plus execution-time
   authorization. Policy outage or invalid response blocks protected actions.
4. Use per-device mTLS, topic/stream ACLs, workload identities, TLS, K8s RBAC and
   network policy; document bounded development exceptions.
5. Protect secrets and append-only audits; redact tokens, locations/identifiers and
   credentials recursively.
6. Run secret/dependency/IaC/image scans, SBOM/provenance/signing checks and Falco
   runtime detection on the supported target. Triage findings with owner/expiry.

## Verification and handoff

Test invalid/expired tokens, cross-role/tenant access, MQTT identity mismatch,
policy outage, replay/tamper, secret leakage, command target escape and a real
runtime alert. Configuration alone is not enforcement evidence. Update register
and memory.
