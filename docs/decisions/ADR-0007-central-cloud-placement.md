# ADR-0007: Central/cloud placement

- **Status:** Provisional - pending user confirmation
- **Date:** 2026-09-18
- **Deciders:** ARCH, with DEVOPS/LEAD as implementing owners

## Context

RQ03 requires edge integration with a "centralized cloud/system tier" and
RQ13 requires a "defensible" cloud/hybrid/on-prem placement decision
(`docs/requirements/TRACEABILITY.md`). `Memory.md` records that target
hardware/OS and whether a real cloud account is required are unknown, and
`docs/environment/TOPOLOGY.md` already restricts the assessment profile to a
"dedicated Ubuntu LTS host," not a named cloud provider. This ADR makes the
placement trade-off explicit so the platform is never presented as cloud-
deployed without authorization and evidence.

## Decision drivers

- `AGENTS.md`: never invent production deployment or assessor approval; no
  prototype component may control public infrastructure.
- RQ13 requires the placement choice to be defensible, i.e., justified against
  real alternatives, not asserted.
- No cloud account, budget, or provider has been authorized by the user
  (`Memory.md` open decisions).
- The "centralized" requirement (RQ03) is about architectural role (a tier
  distinct from edge, doing monitoring/analytics/automation), not literally
  about a commercial cloud vendor.
- Reproducibility for examiners: a locally deployed central tier can be
  verified by any reader with the documented target spec; a private cloud
  account cannot.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Local K3s "central" tier on a dedicated Linux host, real deployment; separate proposed multi-zone cloud architecture as a design-only document (per `docs/REFERENCE_ARCHITECTURE.md` section 7, profile 3)** | Fully reproducible without spending or third-party account risk, satisfies RQ03's architectural separation between edge and a centralized tier, RQ13 is answered with an explicit, defensible on-prem-first rationale, proposed-cloud view still demonstrates cloud/hybrid design competence for RQ13/RQ27 | Does not demonstrate a real managed-cloud control plane; must be labeled clearly as proposed, not deployed |
| Real public cloud deployment (e.g., a managed Kubernetes service) | Directly demonstrates cloud deployment | Requires an authorized account, budget, and credentials the user has not provided; against `AGENTS.md` permission boundary ("cloud spending... require explicit user authorization"); risks unreproducible examiner access if the account is later removed |
| Fully on-prem with no cloud design artifact at all | Simplest scope | Fails RQ13's requirement to show cloud/hybrid placement reasoning, even at design level |

## Decision

Provisional default: deploy the "central" tier as a real, locally hosted K3s
target (the assessment profile in `docs/environment/TOPOLOGY.md`), and
satisfy RQ03/RQ13's cloud/hybrid expectation with a separate, explicitly
labeled proposed production architecture (multi-zone, cloud/hybrid,
redundant gateways, per `docs/REFERENCE_ARCHITECTURE.md` section 7 profile 3
and P13.02) that is never claimed as deployed. This proceeds now because it
requires no new authorization. If the user later authorizes and supplies a
real cloud account/budget, the implementing action is to deploy the same
central-tier services to that account in addition to (not instead of) the
local target, and update this ADR's Status to Accepted with the account
constraints recorded.

## Consequences

- Diagrams and documentation must keep the tested local-K3s view and the
  proposed cloud/hybrid view visibly separate at all times (P13.01, P13.02;
  `docs/decisions` ADR review is part of that consistency check).
- The presentation (P14) must describe this as a deliberate, resource- and
  authorization-constrained choice, not a limitation hidden from the
  examiner.

## Security/privacy/safety effects

No cloud credentials, billing account, or public endpoint exists until
explicitly authorized. This avoids uncontrolled spend and avoids creating a
real externally reachable attack surface for a project with no production
security operations team behind it.

## Revision triggers

- User answers the open `Memory.md` question on target hardware/cloud
  account. Update Status to Accepted or replace with a new ADR describing the
  authorized target.
- Assessment guidance explicitly requires a live managed-cloud deployment as
  a pass condition rather than a design artifact.

## Related requirements

RQ03, RQ13, P02.02, P11.01-P11.08, P13.02.
