# Initial DevSecOps gates

GitHub requires runnable workflow files under `.github/workflows/`. This directory
holds human-readable policy and later provider-neutral release procedures.

## Pull-request gates

1. Python exact-lock install, Ruff format/lint, pytest and management validation.
2. Node exact-lock install, TypeScript tool check and Vitest.
3. Trivy filesystem scan for HIGH/CRITICAL dependency vulnerabilities,
   configuration findings and committed secrets.
4. Read-only repository token permissions; checkout credentials are not persisted
   in the security job.
5. Fixed job timeouts and cancellation of superseded runs.

## Later gated additions

P09.08 adds SAST, policy/IaC tests, SBOM, provenance, signatures, container image
scanning and admission verification after build artifacts exist. P13 adds release
promotion and publication checks. No workflow deploys or publishes without an
explicit protected release process and user authorization.

## Finding handling

- Critical/high findings block by default.
- False positives or accepted upstream risks need exact identifier, affected
  artifact/version, rationale, owner, expiry and retest trigger.
- Never disable an entire scanner to suppress one finding.
- Secret findings trigger credential revocation/rotation even when Git history is
  later cleaned.
