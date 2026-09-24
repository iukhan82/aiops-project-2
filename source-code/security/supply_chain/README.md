# Supply-chain gate (P09.08)

`build_release.py` builds the edge runtime image and gates it as a release
artifact: SBOM, vulnerability scan, SAST, a signed build-provenance
attestation, and a real accept/reject admission proof. Run it from WSL/Linux
(there is no Windows Docker client on this host), using an interpreter that
has `source-code/requirements-lock.txt` installed (bandit is in that lock;
the bare WSL system `python3` is externally-managed and will not have it -
either `python3 -m venv` a local interpreter and `pip install -r
source-code/requirements-lock.txt` into it, or reuse the project's own
CI install step):

```
python3 source-code/security/supply_chain/build_release.py
```

Outputs land in `output/` (git-ignored) and are copied to
`docs/evidence/p09_08_supply_chain.json`. The signing key pair is generated
on first run into `keys/` (git-ignored) - `keys/cosign.pub` is the only
piece anyone else needs, to verify a signature with the real `cosign` CLI or
via `admission-policy.yaml`'s Kyverno rule. Losing `keys/cosign.key` means
generating a new pair and re-signing; nothing upstream of this task depends
on today's key surviving.

## What is scanned, and how the vulnerability gate is scoped

Trivy scans the built image for CRITICAL/HIGH vulnerabilities. The gate
passes only on **fixable** findings (`FixedVersion` present) being zero. As
of the last run, Trivy reports 44 HIGH findings, all in Debian OS packages
pulled in by the pinned `python:3.12-slim` base image (util-linux family,
ncurses, systemd libs), and every one of them has `FixedVersion: none` -
Debian has not shipped a patch yet. None of these packages is exercised by
the edge runtime process itself (no login, mount, or setuid path in
`edge/main.py`'s call graph), and the container already runs non-root with
a read-only rootfs (P04.05). Re-run this script periodically; the day
Debian ships a fix, an unfixed finding here becomes a real, actionable
failure instead of a NOFIX note.

## What "signing" covers today

There is no container registry to push the edge image to yet, so cosign
cannot attach an OCI signature to the image the way it would in a normal
release. Instead, the **provenance attestation** (which names the image's
own digest as its subject) is cosign-signed, along with both SBOMs. Once a
registry destination exists (P13.06/P13.07), `cosign sign` against the
pushed image reference is a small addition here, not a redesign.

## Admission verification

`admit()` in `build_release.py` is the actual gate: it verifies a detached
cosign signature against `keys/cosign.pub` before accepting an artifact.
The script proves this two ways in one run, using the identical signature
file both times: it admits the genuine signed provenance, and it rejects a
one-byte-tampered copy of the same file. `admission-policy.yaml` is the
Kyverno `ClusterPolicy` that will run the same check as a real Kubernetes
admission webhook - **written now, not yet applied anywhere**, because
P11.02 (the K3s cluster it would apply to) does not exist yet.
