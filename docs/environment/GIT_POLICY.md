# Git and review policy

## Repository boundary

The repository root is `AIOPS-Project-2/`. Assessment PDFs, sample presentation,
Project 1, personal information and unrelated workspace files remain outside it.

## Branches and commits

- `main` remains releasable after Phase 1.
- Use short-lived branches named `feature/<task-id>-<topic>`,
  `fix/<task-id>-<topic>` or `docs/<task-id>-<topic>` when remote collaboration
  begins.
- One logical task per commit where practical. Include task ID in commit subject.
- Never rewrite shared published history. Local unpublished correction remains
  exceptional and must not discard user work.
- Tag accepted releases as `vMAJOR.MINOR.PATCH` only after reproducibility and
  publication review.

## Required review gates

Before merge to `main`:

1. Task dependencies and acceptance criteria are satisfied.
2. Relevant format, lint, unit, contract, integration and UI checks pass.
3. Secret, dependency, IaC and container findings meet the defined release policy.
4. Shared contracts/migrations/manifests receive review from affected owners.
5. Security/privacy/safety-impacting changes include threat/risk and negative tests.
6. Evidence and documentation match actual behavior.
7. No generated credentials, private assessment files, personal data, raw video,
   runtime databases, large generated datasets or unrelated artifacts are staged.

## Protected paths

- Root management files: LEAD/coordinator reconciles concurrent edits.
- `source-code/contracts/`: LEAD owns version compatibility.
- `source-code/database/migrations/`: DATA/BACKEND review; applied migrations are
  immutable and corrected through new migrations.
- `source-code/infra/` and `workflows/`: DEVOPS owns deployment/release changes;
  SEC reviews privilege/trust changes.
- `source-code/security/`: SEC owns policies and security tests.
- `docs/compliance/`: GRC owns interpretations and claims.

## Publication gate

Pushing to any remote needs user authorization and repository access. Before first
push, run publication review for secrets, personal data, licences, attribution,
large files, generated outputs, local addresses/hostnames and assessment source
documents. Verify clean clone instructions after push.
