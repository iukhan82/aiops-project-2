# ADR-0008: Deployment orchestration

- **Status:** Accepted
- **Date:** 2026-09-18
- **Deciders:** ARCH, with DEVOPS/SEC as implementing owners

## Context

RQ15 mandates "K3s or MicroK8s plus Docker or Podman." The platform needs
least-privilege manifests, PVCs, RBAC, network policy, and Falco runtime
detection on a real supported Linux target (Phase 11), while development
proceeds on WSL2 Docker Compose (`docs/environment/TOPOLOGY.md`,
`docs/environment/WORKSTATION_INVENTORY.md`).

## Decision drivers

- Assessment-mandated orchestrator family (K3s or MicroK8s) and container
  engine family (Docker or Podman).
- Falco compatibility for runtime security detection (P01.07, P09.09), which
  needs kernel-level probe support that must be proven on the actual target,
  not assumed.
- Resource efficiency on the assessment target budget (8-12 logical CPUs,
  16-32 GiB RAM, `docs/environment/RESOURCE_BUDGET.md`), which rules out a
  full multi-node HA control plane.
- Development-to-target parity: development should exercise the same
  container images the target runs, differing only in orchestration layer
  (Compose locally, K3s on target).

## Options considered

### Orchestrator

| Option | Pros | Cons |
|---|---|---|
| **K3s** | Single lightweight binary, low control-plane overhead fits the assessment target budget, well-documented Falco/eBPF compatibility, satisfies RQ15 directly | Single-node by default for this project's scale, which is disclosed as the assessment topology's known limitation, not hidden |
| MicroK8s | Also satisfies RQ15, snap-based addon model | Snap packaging adds a dependency not confirmed available on an unknown target OS; K3s's single-binary install is more portable across unconfirmed target distributions |
| Full upstream Kubernetes (kubeadm) | Most production-representative | Materially higher control-plane resource cost and operational complexity than the assessment budget and schedule support; not required since RQ15 explicitly accepts K3s/MicroK8s |

### Container engine

| Option | Pros | Cons |
|---|---|---|
| **Docker (Engine, WSL2-hosted for development)** | Already the measured, working WSL2 runtime (`docs/environment/WORKSTATION_INVENTORY.md`), satisfies RQ15, broadest CI/registry tooling compatibility for the DevSecOps pipeline (P01.05) | Daemon-based architecture has a larger root-adjacent attack surface than Podman's daemonless model, mitigated by non-root image builds and K3s-side pod security, not by the build tool itself |
| Podman | Daemonless, rootless-by-default, also satisfies RQ15 | Not yet installed or measured on either the Windows or WSL2 workstation; switching now would delay P01.07/P01.08 for no capability Docker lacks at this project's scope |

## Decision

K3s is the orchestrator for the integrated-acceptance and assessment
deployment profiles; Docker is the container engine for image builds and
WSL2-hosted development Compose, per the already-measured environment in
`docs/environment/TOPOLOGY.md`. Podman remains a documented RQ15-compliant
alternative but is not adopted unless a measured constraint (for example, a
target host that disallows a Docker daemon) requires it.

## Consequences

- `source-code/infra/` (DEVOPS ownership) holds Compose files for
  development and K3s manifests for the assessment target; both must
  reference the same pinned, non-root container images (P11.01).
- P01.07 must independently prove K3s/Falco feasibility on a supported Linux
  target before this decision counts as verified beyond documentation.
- Single-node K3s is the disclosed assessment topology; it is documented as
  "not production high availability" per `docs/environment/TOPOLOGY.md` and
  must never be presented otherwise in diagrams or the presentation.

## Security/privacy/safety effects

Least-privilege K3s manifests (resource limits, RBAC, network policy,
non-root images) and Falco runtime detection are required before any
Phase 11 deployment is treated as acceptance evidence (P11.02, P11.04). No
production workload placement decision follows from this ADR alone.

## Revision triggers

- The eventual assessment target cannot run a Docker daemon or K3s (for
  example, a locked-down managed host), which would require adopting Podman
  and/or MicroK8s as a documented substitution with re-measured feasibility.

## Related requirements

RQ15, P01.07, P01.08, P11.01-P11.08.
