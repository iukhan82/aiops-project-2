# Project diagrams

Keep editable source plus SVG/PNG exports for system architecture, network flow,
data flow, workflow, security/trust boundaries, hybrid deployment, and production
reference views. Tested implementation diagrams must not silently include proposed
components. Presentation-simplified copies belong under `presentation/`.

## Mandatory views (P02.09)

Source format is Mermaid (`.mmd`), rendered to `diagrams/exports/*.svg` with
`npx @mermaid-js/mermaid-cli -i <source> -o <export> -b white`. Regenerate an
export after any source edit; do not hand-edit an export.

| # | View | Source | Requirement | Status |
|---|---|---|---|---|
| 1 | System architecture | `sources/01-system-architecture.mmd` | RQ10 | Tested-implementation logical view |
| 2 | Network flow | `sources/02-network-flow.mmd` | RQ08 | Zones, protocols, ports, secure channels |
| 3 | Data flow | `sources/03-data-flow.mmd` | RQ09 | Sensing through decision, truth-label transitions marked |
| 4 | Workflow (command safety path) | `sources/04-workflow-command-safety-path.mmd` | RQ11 | Sequence diagram; REQUEST/APPROVE/EXECUTE/OVERRIDE per P02.08 |
| 5 | Security / trust boundaries | `sources/05-security-trust-boundaries.mmd` | RQ12 | 9 trust zones; restricted zones highlighted |
| 6 | Hybrid / cloud placement | `sources/06-hybrid-cloud-placement.mmd` | RQ13 | TESTED (green) vs PROPOSED-not-deployed (red/dashed), per ADR-0007 |
| 7 | Deployment topology | `sources/07-deployment-topology.mmd` | RQ15/RQ22 | K3s namespaces/workloads on the single-node assessment target |

These are first-draft engineering views built from the Phase 00-02 documents
and contracts (`docs/decisions/`, `docs/security/ROLES_AND_ACTION_AUTHORITY.md`,
`source-code/contracts/`), not from a running system. P13.01 updates them to
match tested implementation evidence before release; until then they describe
the intended architecture, and diagram 6 is explicit about what is and is not
deployed.
