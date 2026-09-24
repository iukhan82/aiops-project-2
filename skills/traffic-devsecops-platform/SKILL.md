---
name: traffic-devsecops-platform
description: Build reproducible developer environments, CI/CD, containers, K3s deployment, software-supply-chain controls, backups, releases, and operational runbooks. Use for repository, infrastructure, DevSecOps, and deployment work.
---

# Traffic DevSecOps Platform Engineer

Read environment decisions, security baseline and deployment dependencies. Own
`source-code/infra/`, `workflows/`, environment docs, build/release automation and
deployment runbooks. Use the user-approved Linux target; local success is not target
evidence.

## Workflow

1. Inventory resources and prove SUMO, containers, K3s/MicroK8s, storage/network,
   ingress and Falco feasibility early. Define bounded development/acceptance profiles.
2. Create deterministic setup with pinned dependencies, checksums, health/readiness,
   resource limits, non-root images and target-owned secrets.
3. Build CI gates for format/lint/test/contracts/frontend, secret/SAST/dependency/
   IaC/image scanning, policy tests, SBOM, provenance and artifact validation.
4. Build versioned images/manifests, least-privilege K3s RBAC/network policies,
   persistent volumes, backups, migrations, observability and safe rollout/rollback.
5. Verify idempotent deploy, restart, backup/restore, certificate/config rotation,
   capacity, degraded operation and disaster procedures.
6. Prepare a reproducible content/commit/image/model release; publish only with
   explicit user authorization and access.

## Verification and handoff

Run workflows from a clean environment and execute deployment/recovery on the real
target. Record versions, hashes, commands, failures and cleanup. Do not mark a
manifest deployed without observed workloads. Update register and memory.
