"""P09.08: build the edge runtime release artifact and gate it end to end -
SBOM, vulnerability/SAST scans, a signed build-provenance attestation, and a
real accept/reject admission-verification proof - against the real Docker
daemon (WSL/Linux; there is no Windows docker client on this host).

    python3 source-code/security/supply_chain/build_release.py

Requires: docker (native Linux engine), git, and this project's own venv is
NOT needed - only the standard library plus `backend.evidence`, which is
itself stdlib-only.

Third-party tools run as pinned-by-digest containers (syft, cosign, trivy),
matching `source-code/infra/platform/docker-compose.yml`'s convention - no
new tool is installed on the host.

What this proves, and what it deliberately does not claim:

* SBOM (CycloneDX) for the built edge image (OS + Python closure) and for
  the frontend's Node dependency lock, via syft.
* Vulnerability scan of the image via Trivy. The gate is on FIXABLE
  CRITICAL/HIGH findings, not on raw count: as of this run, Trivy found 44
  HIGH findings, all Debian OS packages, all with `FixedVersion: none` -
  upstream has not shipped a patch yet. Forcing that count to zero would
  mean lying about a fix that does not exist. The gate fails loudly the day
  a fix ships and this scan is re-run and still finds them un-upgraded.
* SAST (bandit) over the edge source that ships in the image.
* A build-provenance attestation binding the image digest to its real
  inputs (git commit, dirty state, a content hash of the exact Docker build
  context, the base image digest) - not just a claim, a recomputed hash.
* Cosign-signs the provenance and both SBOMs with a locally generated key
  pair (`keys/cosign.key` / `keys/cosign.pub`, key never committed,
  `--tlog-upload=false` so nothing is published to the public Sigstore
  transparency log for a project that is not ready to be public).
* A real admission gate (`admit()` below) that verifies a signature before
  accepting an artifact, proven twice: it ADMITS the genuine signed
  provenance and REJECTS a one-byte-tampered copy under the same
  signature. `admission-policy.yaml` in this directory is the Kyverno
  policy that will apply the same check on a real cluster - written now,
  genuinely enforced only once P11.02 builds that cluster (there is none
  yet). That gap is real and is recorded as such, not hidden.
* The image itself is not signed as an OCI artifact because there is no
  registry to attach that signature to yet (P13.06/P13.07). The signed
  provenance attestation, which names the image's own digest, is the
  release's real signed claim until a registry exists.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE_ROOT = HERE.parents[1]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))

from backend.evidence import Evidence  # noqa: E402

OUT_DIR = HERE / "output"
KEY_DIR = HERE / "keys"
DOCKERFILE = SOURCE_ROOT / "edge" / "Dockerfile"
IMAGE_TAG_PREFIX = "aiops-edge"

SYFT_IMAGE = (
    "anchore/syft:v1.18.1@sha256:b8c170b8e51bfc4779ec3ef4399942c57290f5ce76a9c3af564c9d00d4946a6b"
)
COSIGN_IMAGE = (
    "gcr.io/projectsigstore/cosign:v2.4.1"
    "@sha256:b03690aa52bfe94054187142fba24dc54137650682810633901767d8a3e15b31"
)
# Run cosign as the host user (not the image's baked-in nonroot UID) so every
# file it creates - keys, signatures - is owned by whoever needs to read it
# back afterwards, both later in this script and in the upload-artifact step.
COSIGN_USER_ARGS = ["--user", f"{os.getuid()}:{os.getgid()}"]
TRIVY_IMAGE = "aquasec/trivy:0.74.0"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return result


def require(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    result = run(cmd, **kw)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n{result.stderr[-4000:]}"
        )
    return result


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def build_context_manifest(paths: list[Path]) -> tuple[dict[str, str], str]:
    """Every file actually COPYed into the edge image (the Dockerfile's own
    inputs), hashed individually and combined into one tree hash - the
    build's real content identity, independent of git commit/dirty state."""
    manifest: dict[str, str] = {}
    for base in paths:
        if base.is_file():
            manifest[str(base.relative_to(SOURCE_ROOT)).replace("\\", "/")] = sha256_file(base)
            continue
        for file in sorted(base.rglob("*")):
            if file.is_file() and "__pycache__" not in file.parts:
                manifest[str(file.relative_to(SOURCE_ROOT)).replace("\\", "/")] = sha256_file(file)
    lines = "\n".join(f"{k}:{v}" for k, v in sorted(manifest.items()))
    return manifest, sha256_bytes(lines.encode("utf-8"))


def git_info() -> dict:
    head = run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT)
    status = run(["git", "status", "--porcelain"], cwd=REPO_ROOT)
    return {
        "head": head.stdout.strip() if head.returncode == 0 else None,
        "dirty": bool(status.stdout.strip()) if status.returncode == 0 else None,
        "uncommitted_paths": len(status.stdout.strip().splitlines())
        if status.returncode == 0
        else None,
    }


def base_image_ref() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().upper().startswith("FROM "):
            return line.strip()[5:].split(" AS ")[0].strip()
    raise RuntimeError("no FROM line in edge/Dockerfile")


def docker_build(tag: str) -> str:
    require(["docker", "build", "-f", str(DOCKERFILE), "-t", tag, str(SOURCE_ROOT)])
    result = require(["docker", "image", "inspect", tag, "--format", "{{.Id}}"])
    return result.stdout.strip()


def ensure_world_writable(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(path.stat().st_mode | stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)


def syft_sbom(
    target: str, out_file: Path, *, mount_docker_sock: bool, mount_dir: Path | None
) -> dict:
    ensure_world_writable(out_file.parent)
    cmd = ["docker", "run", "--rm"]
    if mount_docker_sock:
        cmd += ["-v", "/var/run/docker.sock:/var/run/docker.sock"]
    if mount_dir is not None:
        cmd += ["-v", f"{mount_dir}:/src:ro"]
    cmd += [SYFT_IMAGE, target, "-o", "cyclonedx-json"]
    result = require(cmd)
    out_file.write_text(result.stdout, encoding="utf-8")
    data = json.loads(result.stdout)
    return {"components": len(data.get("components", [])), "path": str(out_file)}


def trivy_image_scan(image_ref: str, out_file: Path) -> dict:
    ensure_world_writable(out_file.parent)
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        TRIVY_IMAGE,
        "image",
        "--scanners",
        "vuln",
        "--severity",
        "CRITICAL,HIGH",
        "--exit-code",
        "0",
        "--format",
        "json",
        image_ref,
    ]
    result = require(cmd)
    out_file.write_text(result.stdout, encoding="utf-8")
    data = json.loads(result.stdout)
    findings = []
    for r in data.get("Results") or []:
        for v in r.get("Vulnerabilities") or []:
            findings.append(
                {
                    "package": v.get("PkgName"),
                    "id": v.get("VulnerabilityID"),
                    "severity": v.get("Severity"),
                    "installed": v.get("InstalledVersion"),
                    "fixed": v.get("FixedVersion") or None,
                }
            )
    fixable = [f for f in findings if f["fixed"]]
    return {
        "total": len(findings),
        "fixable": len(fixable),
        "fixable_findings": fixable,
        "unfixable_ids": sorted({f["id"] for f in findings if not f["fixed"]}),
        "path": str(out_file),
    }


def bandit_scan(target: Path, out_file: Path) -> dict:
    ensure_world_writable(out_file.parent)
    result = run(
        [sys.executable, "-m", "bandit", "-r", str(target), "-f", "json", "-o", str(out_file)],
        cwd=REPO_ROOT,
    )
    # bandit exits 1 when it finds issues (not a tool failure); only a missing
    # report file (crash) is treated as a hard error.
    if not out_file.is_file():
        raise RuntimeError(f"bandit did not produce a report: {result.stderr[-2000:]}")
    data = json.loads(out_file.read_text(encoding="utf-8"))
    return {
        "issues": len(data.get("results", [])),
        "loc": data.get("metrics", {}).get("_totals", {}).get("loc"),
        "path": str(out_file),
    }


def cosign_ensure_keys() -> None:
    ensure_world_writable(KEY_DIR)
    if (KEY_DIR / "cosign.key").is_file() and (KEY_DIR / "cosign.pub").is_file():
        return
    password = os.environ.get("COSIGN_PASSWORD") or sha256_bytes(os.urandom(32))
    (KEY_DIR / ".cosign-password").write_text(password, encoding="utf-8")
    (KEY_DIR / ".cosign-password").chmod(0o600)
    env = {**os.environ, "COSIGN_PASSWORD": password, "HOME": str(KEY_DIR)}
    require(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            *COSIGN_USER_ARGS,
            "-e",
            "COSIGN_PASSWORD",
            "-e",
            "HOME",
            "-v",
            f"{KEY_DIR}:/work",
            "-w",
            "/work",
            COSIGN_IMAGE,
            "generate-key-pair",
        ],
        env=env,
    )


def cosign_password() -> str:
    return (KEY_DIR / ".cosign-password").read_text(encoding="utf-8").strip()


def cosign_sign_blob(path: Path) -> Path:
    sig_path = path.with_suffix(path.suffix + ".sig")
    env = {**os.environ, "COSIGN_PASSWORD": cosign_password(), "HOME": str(KEY_DIR)}
    require(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            *COSIGN_USER_ARGS,
            "-e",
            "COSIGN_PASSWORD",
            "-e",
            "HOME",
            "-v",
            f"{KEY_DIR}:/keys:ro",
            "-v",
            f"{path.parent}:/data",
            "-w",
            "/data",
            COSIGN_IMAGE,
            "sign-blob",
            "--key",
            "/keys/cosign.key",
            "--tlog-upload=false",
            "--output-signature",
            f"/data/{sig_path.name}",
            f"/data/{path.name}",
        ],
        env=env,
    )
    return sig_path


def cosign_verify_blob(path: Path, sig_path: Path) -> bool:
    env = {**os.environ, "HOME": str(KEY_DIR)}
    result = run(
        [
            "docker",
            "run",
            "--rm",
            *COSIGN_USER_ARGS,
            "-e",
            "HOME",
            "-v",
            f"{KEY_DIR}:/keys:ro",
            "-v",
            f"{path.parent}:/data:ro",
            "-w",
            "/data",
            COSIGN_IMAGE,
            "verify-blob",
            "--key",
            "/keys/cosign.pub",
            "--signature",
            f"/data/{sig_path.name}",
            "--insecure-ignore-tlog",
            f"/data/{path.name}",
        ],
        env=env,
    )
    return result.returncode == 0


def admit(path: Path, sig_path: Path) -> bool:
    """The admission gate itself: an artifact is ADMITTED only if its
    signature verifies against the committed public key. This is the same
    check `admission-policy.yaml` asks Kyverno to run on a real cluster."""
    return cosign_verify_blob(path, sig_path)


def main() -> int:
    ev = Evidence(task="P09.08", docs_name="p09_08_supply_chain")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Source identity: what is actually going into the image, independent
    # of git commit state (most of this repository is currently uncommitted).
    context_paths = [
        SOURCE_ROOT / "edge",
        SOURCE_ROOT / "contracts",
    ]
    manifest, tree_hash = build_context_manifest(context_paths)
    git = git_info()
    ev.check(
        "build_context_hashed",
        len(manifest) > 0,
        f"{len(manifest)} files, tree_sha256={tree_hash[:16]}...",
    )
    ev.notes["git_head"] = git["head"] or "unknown"
    ev.notes["git_dirty"] = str(git["dirty"])

    tag = f"{IMAGE_TAG_PREFIX}:{tree_hash[:12]}"

    # 2. Build.
    try:
        image_digest = docker_build(tag)
        ev.check("edge_image_builds", True, f"{tag} -> {image_digest}")
    except RuntimeError as exc:
        ev.check("edge_image_builds", False, str(exc)[:300])
        return ev.finish()

    # 3. SBOMs.
    try:
        img_sbom = syft_sbom(
            tag, OUT_DIR / "sbom_image.cdx.json", mount_docker_sock=True, mount_dir=None
        )
        ev.check(
            "sbom_image_generated",
            img_sbom["components"] > 0,
            f"{img_sbom['components']} components",
        )
    except RuntimeError as exc:
        ev.check("sbom_image_generated", False, str(exc)[:300])
        img_sbom = None

    try:
        fe_sbom = syft_sbom(
            "dir:/src",
            OUT_DIR / "sbom_frontend.cdx.json",
            mount_docker_sock=False,
            mount_dir=SOURCE_ROOT / "frontend",
        )
        ev.check(
            "sbom_frontend_generated",
            fe_sbom["components"] > 0,
            f"{fe_sbom['components']} components",
        )
    except RuntimeError as exc:
        ev.check("sbom_frontend_generated", False, str(exc)[:300])
        fe_sbom = None

    # 4. Vulnerability scan - gate on fixable findings only (see module docstring).
    try:
        scan = trivy_image_scan(tag, OUT_DIR / "trivy_image.json")
        ev.check(
            "trivy_image_scan_ran",
            True,
            f"{scan['total']} CRITICAL/HIGH found, {scan['fixable']} fixable",
        )
        ev.check(
            "no_fixable_critical_high",
            scan["fixable"] == 0,
            f"{scan['fixable']} fixable findings (must be 0 to pass)"
            if scan["fixable"]
            else f"0 fixable; {scan['total']} unfixable upstream-NOFIX (ids: {scan['unfixable_ids'][:5]})",
        )
        ev.metrics["trivy"] = {k: v for k, v in scan.items() if k != "fixable_findings"}
    except RuntimeError as exc:
        ev.check("trivy_image_scan_ran", False, str(exc)[:300])

    # 5. SAST on the shipped edge source.
    try:
        sast = bandit_scan(SOURCE_ROOT / "edge", OUT_DIR / "bandit_edge.json")
        ev.check(
            "sast_edge_clean",
            sast["issues"] == 0,
            f"{sast['issues']} findings over {sast['loc']} lines",
        )
    except RuntimeError as exc:
        ev.check("sast_edge_clean", False, str(exc)[:300])

    # 6. Provenance attestation.
    provenance = {
        "_type": "https://in-toto.io/Statement/v1",
        "predicateType": "https://slsa.dev/provenance/v1",
        "subject": [{"name": tag, "digest": {"sha256": image_digest.removeprefix("sha256:")}}],
        "predicate": {
            "buildType": "https://aiops-project.local/dockerfile-build/v1",
            "builder": {"id": "local-devsecops-workstation"},
            "invocation": {
                "configSource": {"path": "source-code/edge/Dockerfile"},
                "parameters": {"context": "source-code", "target": None},
            },
            "materials": [
                {"uri": "git+local-repo", "digest": {"sha1": git["head"]}, "dirty": git["dirty"]},
                {
                    "uri": "tree:source-code/edge+source-code/contracts",
                    "digest": {"sha256": tree_hash},
                },
                {"uri": f"docker+{base_image_ref()}"},
            ],
            "metadata": {
                "buildFinishedOn": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "reproducible": False,
                "completeness": {
                    "materials": True,
                    "note": "source identity is a content hash, not git commit, because the working tree is not fully committed yet",
                },
            },
        },
    }
    prov_path = OUT_DIR / "provenance.json"
    ensure_world_writable(OUT_DIR)
    prov_path.write_text(json.dumps(provenance, indent=2, sort_keys=True), encoding="utf-8")
    ev.check("provenance_generated", True, str(prov_path))

    # 7. Sign provenance + both SBOMs.
    try:
        cosign_ensure_keys()
        ev.check("cosign_keys_ready", True, str(KEY_DIR / "cosign.pub"))
    except RuntimeError as exc:
        ev.check("cosign_keys_ready", False, str(exc)[:300])
        return ev.finish()

    to_sign = [prov_path]
    if img_sbom:
        to_sign.append(Path(img_sbom["path"]))
    if fe_sbom:
        to_sign.append(Path(fe_sbom["path"]))

    signed = []
    all_verify_ok = True
    for artifact in to_sign:
        try:
            sig = cosign_sign_blob(artifact)
            ok = cosign_verify_blob(artifact, sig)
            all_verify_ok = all_verify_ok and ok
            signed.append((artifact, sig))
        except RuntimeError as exc:
            ev.check(f"sign_{artifact.name}", False, str(exc)[:300])
            all_verify_ok = False
    ev.check(
        "artifacts_signed", len(signed) == len(to_sign), f"{len(signed)}/{len(to_sign)} signed"
    )
    ev.check(
        "signatures_verify",
        all_verify_ok,
        f"{len(signed)} artifacts, all genuine signatures verify OK",
    )

    # 8. The admission gate proof: tamper one byte in a COPY of the signed
    # provenance and confirm the same gate that admits the genuine artifact
    # rejects the tampered one, under the identical signature file.
    genuine_path, genuine_sig = signed[0] if signed else (None, None)
    tamper_ok = False
    if genuine_path is not None:
        tampered = genuine_path.with_name(genuine_path.stem + ".tampered" + genuine_path.suffix)
        data = bytearray(genuine_path.read_bytes())
        data[-1] ^= 0xFF  # flip the last byte
        tampered.write_bytes(bytes(data))
        admitted_genuine = admit(genuine_path, genuine_sig)
        admitted_tampered = admit(tampered, genuine_sig)
        tamper_ok = admitted_genuine and not admitted_tampered
        ev.notes["admission_gate_proof"] = (
            f"genuine admitted={admitted_genuine}, tampered admitted={admitted_tampered}"
        )
    ev.check(
        "admission_gate_rejects_tampered_artifact",
        tamper_ok,
        ev.notes.get("admission_gate_proof", "no signed artifact to test"),
    )

    ev.metrics["image"] = {"tag": tag, "digest": image_digest, "tree_sha256": tree_hash}
    ev.metrics["git"] = git
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
