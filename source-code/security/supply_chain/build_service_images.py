"""P11.01: build the platform's own service images reproducibly and gate them - SBOM, vulnerability scan, versioned identity, signed provenance.

    python3 source-code/security/supply_chain/build_service_images.py        # inside WSL/Linux with a native docker engine

Images: `aiops-backend` (every Python service; `backend/Dockerfile`) and `aiops-frontend` (the console behind an unprivileged nginx;
`frontend/Dockerfile`). Tools run as digest-pinned containers exactly as in `build_release.py` (syft, trivy, cosign); this script reuses
that file's helpers.

What is proven, and what is not:

* REPRODUCIBLE: each image is built twice from scratch (`--no-cache`) with `SOURCE_DATE_EPOCH` fixed to the HEAD commit time and
  BuildKit's `rewrite-timestamp`, exported as an OCI archive, and the two manifest digests must be identical. (A `pip` install that
  compiles bytecode breaks this - the backend Dockerfile installs with `--no-compile` for that reason.)
* NON-ROOT: the image configuration's `User` is a non-root user, and the running container reports a non-zero uid.
* VERSIONED: the image carries `org.opencontainers.image.version` and `.revision` labels equal to the release version and the git HEAD.
* SBOM (CycloneDX via syft) of the exact archive that was built; for the backend it must list every package of
  `backend/requirements-lock.txt` at the pinned version, and nothing from the development lock (pytest, ruff, bandit, matplotlib ...).
* VULNERABILITY SCAN (Trivy) of that archive; the gate is on FIXABLE critical or high findings. Findings with no upstream fix are
  reported, not hidden and not pretended fixed.
* PROVENANCE (SLSA v1 shape) binding each image digest to its inputs (git HEAD and dirty state, a hash of every file the Dockerfile COPYs,
  the base image digests, the lock hash, the reproducibility result), signed with the local cosign key; the admission gate ADMITS the
  genuine statement and REJECTS a one-byte-tampered copy.
* NOT proven: nothing is pushed to a registry and no image signature is attached to an OCI registry (P13.06/P13.07); the admission
  policy for a cluster (`admission-policy.yaml`) is enforced only once a cluster runs it (P11.03).
"""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
import sys
import tarfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_release as br  # noqa: E402
from backend.evidence import Evidence  # noqa: E402

SOURCE_ROOT = br.SOURCE_ROOT
OUT = br.OUT_DIR / "service_images"
IMAGES = {
    "aiops-backend": {"dockerfile": SOURCE_ROOT / "backend" / "Dockerfile", "context": SOURCE_ROOT},
    "aiops-frontend": {
        "dockerfile": SOURCE_ROOT / "frontend" / "Dockerfile",
        "context": SOURCE_ROOT / "frontend",
    },
}
DEV_ONLY = (
    "pytest",
    "ruff",
    "bandit",
    "matplotlib",
    "onnxruntime",
    "kubernetes-validate",
    "paramiko",
)


def release_version() -> str:
    text = (SOURCE_ROOT / "infra" / "k8s" / "generate_workloads.py").read_text(encoding="utf-8")
    return re.search(r'^VERSION = "([^"]+)"', text, re.M).group(1)


def commit_time() -> str:
    result = br.run(["git", "log", "-1", "--format=%ct"], cwd=br.REPO_ROOT)
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "0"


def dockerfile_inputs(dockerfile: Path, context: Path) -> tuple[list[Path], list[str]]:
    """The files the Dockerfile COPYs from the build context, and its base images."""
    paths, bases = [], []
    for raw in dockerfile.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.upper().startswith("FROM "):
            bases.append(line.split()[1])
        elif line.upper().startswith("COPY ") and "--from=" not in line:
            parts = [p for p in line.split()[1:] if not p.startswith("--")]
            for source in parts[:-1]:
                path = context / source
                if path.exists():
                    paths.append(path)
    return paths, bases


def ignored(relative: str, patterns: list[str]) -> bool:
    """A file `.dockerignore` keeps out of the build context (simple patterns only; the ones these two files use)."""
    parts = relative.split("/")
    for pattern in patterns:
        bare = pattern.removeprefix("**/")
        if any(fnmatch.fnmatch(part, bare) for part in parts) or fnmatch.fnmatch(relative, bare):
            return True
        if "/" not in bare and parts[0] == bare:
            return True
    return False


def context_manifest(paths: list[Path], context: Path) -> tuple[dict[str, str], str]:
    """Hash of every file that actually enters the image: the COPY sources minus what `.dockerignore` excludes."""
    ignore_file = context / ".dockerignore"
    patterns = (
        [ln.strip() for ln in ignore_file.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith(("#", "!"))]
        if ignore_file.is_file()
        else []
    )  # fmt: skip
    manifest: dict[str, str] = {}
    for base in paths:
        files = [base] if base.is_file() else sorted(f for f in base.rglob("*") if f.is_file())
        for file in files:
            relative = str(file.relative_to(context)).replace("\\", "/")
            if base.is_file() or not ignored(relative, patterns):
                manifest[relative] = br.sha256_file(file)
    lines = "\n".join(f"{k}:{v}" for k, v in sorted(manifest.items()))
    return manifest, br.sha256_bytes(lines.encode("utf-8"))


def extract_layout(archive: Path, into: Path) -> Path:
    """Trivy reads an OCI layout directory; unpack the archive that was built."""
    if into.exists():
        shutil.rmtree(into)
    into.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        tar.extractall(into, filter="data")
    br.ensure_world_writable(into)
    return into


def build_archive(
    name: str, spec: dict, version: str, revision: str, epoch: str, dest: Path
) -> str:
    """Build from scratch into an OCI archive; returns the manifest digest."""
    meta = dest.with_suffix(".meta.json")
    br.require([
        "docker", "buildx", "build", "--no-cache", "--provenance=false", "--sbom=false",
        "--build-arg", f"SOURCE_DATE_EPOCH={epoch}", "--build-arg", f"VERSION={version}", "--build-arg", f"REVISION={revision}",
        "-f", str(spec["dockerfile"]), "--metadata-file", str(meta),
        "--output", f"type=oci,dest={dest},rewrite-timestamp=true", str(spec["context"]),
    ])  # fmt: skip
    return json.loads(meta.read_text(encoding="utf-8"))["containerimage.digest"]


def archive_config(archive: Path) -> dict:
    with tarfile.open(archive) as tar:
        index = json.load(tar.extractfile("index.json"))
        manifest = json.load(
            tar.extractfile("blobs/sha256/" + index["manifests"][0]["digest"].split(":")[1])
        )
        return json.load(
            tar.extractfile("blobs/sha256/" + manifest["config"]["digest"].split(":")[1])
        )


def sbom_of_archive(archive: Path, out_file: Path) -> dict:
    br.ensure_world_writable(out_file.parent)
    result = br.require(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{archive.parent}:/in:ro",
            br.SYFT_IMAGE,
            f"oci-archive:/in/{archive.name}",
            "-o",
            "cyclonedx-json",
        ]
    )
    out_file.write_text(result.stdout, encoding="utf-8")
    data = json.loads(result.stdout)
    return {"components": data.get("components", []), "path": out_file}


def trivy_of_archive(archive: Path, out_file: Path) -> dict:
    layout = extract_layout(archive, archive.parent / (archive.name.split(".")[0] + "-layout"))
    result = br.require([
        "docker", "run", "--rm", "-v", f"{layout}:/in:ro", "-v", "aiops-trivy-cache:/root/.cache/trivy", br.TRIVY_IMAGE, "image", "--input", "/in",
        "--scanners", "vuln", "--severity", "CRITICAL,HIGH", "--exit-code", "0", "--format", "json",
    ])  # fmt: skip
    out_file.write_text(result.stdout, encoding="utf-8")
    findings = [
        {"package": v.get("PkgName"), "id": v.get("VulnerabilityID"), "severity": v.get("Severity"), "fixed": v.get("FixedVersion") or None}
        for r in (json.loads(result.stdout).get("Results") or [])
        for v in (r.get("Vulnerabilities") or [])
    ]  # fmt: skip
    fixable = [f for f in findings if f["fixed"]]
    return {
        "total": len(findings),
        "fixable": fixable,
        "unfixable_ids": sorted({f["id"] for f in findings if not f["fixed"]}),
        "path": out_file,
    }


HARDENED_RUN = [
    "--read-only",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges",
    "--tmpfs",
    "/tmp",
]  # noqa: S108 - a container path
IMPORT_PROBE = """
import ast, importlib, json, os, pathlib
mods = set()
for script in {scripts!r}:
    for node in ast.walk(ast.parse(pathlib.Path(script).read_text())):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module)
failed = []
for m in sorted(mods):
    try:
        importlib.import_module(m)
    except BaseException as exc:
        failed.append(f"{{m}}: {{type(exc).__name__}}: {{exc}}")
print(json.dumps({{"uid": os.getuid(), "modules": len(mods), "failed": failed}}))
"""


STARTUP_PROBE = (
    "from fastapi.testclient import TestClient; from backend.api.app import app; "
    "r = TestClient(app).get('/api/v1/health'); assert r.status_code == 200, r.text"
)


def load_image_for_smoke_test(
    name: str, spec: dict, version: str, revision: str, epoch: str
) -> str:
    """Loads the image straight into the local Docker daemon through buildx's own `--load`, not `docker load` of the OCI archive:
    some Docker Engine versions (seen on the GitHub-hosted Ubuntu 24.04 runner's Docker 28.0.4, not on a newer local Engine) fail to
    import the image-index-wrapped OCI tar BuildKit now produces even for a single-platform build ("blobs/json: no such file or
    directory") - `--load` never goes through that tar at all. Cache from the two `--no-cache` archive builds moments earlier makes
    this fast and, since the Dockerfile/context/build-args are unchanged, content-identical; only used for the smoke test below, not
    for anything recorded, signed or compared as the release digest - that is still `archive`, built with `--no-cache`."""
    iid_file = OUT / f"{name.removeprefix('aiops-')}-load.iid"
    br.require(
        [
            "docker", "buildx", "build", "--provenance=false", "--sbom=false",
            "--build-arg", f"SOURCE_DATE_EPOCH={epoch}", "--build-arg", f"VERSION={version}", "--build-arg", f"REVISION={revision}",
            "-f", str(spec["dockerfile"]), "--iidfile", str(iid_file), "--load", str(spec["context"]),
        ]
    )  # fmt: skip
    image = iid_file.read_text(encoding="utf-8").strip()
    iid_file.unlink(missing_ok=True)
    return image


def workload_scripts() -> list[str]:
    """Every backend script a workload runs (from the generated manifests), so the image is probed with exactly what will run in it."""
    text = (SOURCE_ROOT / "infra" / "k8s" / "workloads.yaml").read_text(encoding="utf-8")
    return sorted(set(re.findall(r"\b(backend/[\w/]+\.py)\b", text)))


def smoke_backend(image: str) -> dict:
    """Run as configured, read-only and with no capabilities: every module every service script imports must import, as a non-root uid."""
    scripts = workload_scripts()
    result = br.require(
        [
            "docker",
            "run",
            "--rm",
            *HARDENED_RUN,
            "--workdir",
            "/srv/repo/source-code",
            image,
            "-c",
            IMPORT_PROBE.format(scripts=scripts),
        ]
    )
    report = json.loads(result.stdout.strip().splitlines()[-1])
    report["scripts"] = len(scripts)
    # Importing is not starting: the operator API refuses to answer without files it reads at run time (found on the target, where the
    # image lacked the UX inventory). So the API is also asked its health check, in the same read-only container. No database is reachable here, so the
    # health check answers 200 "degraded"; the placeholders only let the API build a connection string (they are not credentials of anything).
    started = br.run(
        [
            "docker",
            "run",
            "--rm",
            *HARDENED_RUN,
            *(
                "-e",
                "POSTGRES_DB=probe",
                "-e",
                "POSTGRES_USER=probe",
                "-e",
                "POSTGRES_PASSWORD=probe",
            ),
            "--workdir",
            "/srv/repo/source-code",
            image,
            "-c",
            STARTUP_PROBE,
        ]
    )
    report["api_starts"] = started.returncode == 0
    report["api_start_error"] = (
        started.stderr.strip().splitlines()[-1][:160] if started.returncode else ""
    )
    return report


def smoke_frontend(image: str) -> dict:
    """Serve the console read-only with no capabilities; the response must be 200 with the security headers, and the uid non-root."""
    name = f"p11-01-frontend-{int(time.time())}"
    # nginx resolves its two proxy upstreams by name when it starts (in the cluster they are Services); here they only have to resolve
    br.require(
        [
            "docker", "run", "-d", "--name", name, *HARDENED_RUN, "--tmpfs", "/var/cache/nginx",
            "--add-host", "aiops-api:127.0.0.1", "--add-host", "aiops-scenario-control:127.0.0.1", image,
        ]
    )  # fmt: skip
    try:
        time.sleep(3)
        fetched = br.run(
            [
                "docker",
                "exec",
                name,
                "wget",
                "-q",
                "-S",
                "-O",
                "/dev/null",
                "http://127.0.0.1:8080/",
            ]
        )
        uid = br.run(["docker", "exec", name, "id", "-u"]).stdout.strip()
    finally:
        br.run(["docker", "rm", "-f", name])
    headers = fetched.stderr.lower()
    return {
        "status_200": " 200 " in headers,
        "csp": "content-security-policy" in headers,
        "nosniff": "x-content-type-options: nosniff" in headers,
        "uid": uid,
    }


def main() -> int:  # noqa: PLR0915
    ev = Evidence(task="P11.01", docs_name="p11_01_service_images")
    OUT.mkdir(parents=True, exist_ok=True)
    br.ensure_world_writable(OUT)
    version, epoch = release_version(), commit_time()
    git = br.git_info()
    revision = (git["head"] or "unknown")[:7]
    ev.notes["git_head"], ev.notes["git_dirty"], ev.notes["source_date_epoch"] = (
        git["head"] or "unknown",
        str(git["dirty"]),
        epoch,
    )
    br.cosign_ensure_keys()
    signed: list[Path] = []
    lock_text = (SOURCE_ROOT / "backend" / "requirements-lock.txt").read_text(encoding="utf-8")
    lock_pins = {
        m.group(1).lower().replace("_", "-"): m.group(2)
        for m in re.finditer(r"^([A-Za-z0-9_.\-]+)==(\S+)", lock_text, re.M)
    }

    for name, spec in IMAGES.items():
        short = name.removeprefix("aiops-")
        inputs, bases = dockerfile_inputs(spec["dockerfile"], spec["context"])
        manifest, tree_hash = context_manifest(inputs, spec["context"])
        ev.check(
            f"{short}_build_inputs_hashed_and_base_images_pinned_by_digest",
            bool(manifest) and all("@sha256:" in b for b in bases),
            f"{len(manifest)} files, tree {tree_hash[:12]}; bases {[b[-24:] for b in bases]}",
        )

        try:
            first = build_archive(name, spec, version, revision, epoch, OUT / f"{short}-1.oci.tar")
            second = build_archive(name, spec, version, revision, epoch, OUT / f"{short}-2.oci.tar")
        except RuntimeError as exc:
            ev.check(f"{short}_builds_twice_from_scratch", False, str(exc)[-700:])
            continue
        archive = OUT / f"{short}-1.oci.tar"
        ev.check(
            f"{short}_two_clean_builds_give_the_identical_image_digest",
            first == second,
            f"{first[:23]} vs {second[:23]}",
        )

        config = archive_config(archive)
        user = config["config"].get("User", "")
        labels = config["config"].get("Labels", {})
        ev.check(
            f"{short}_image_is_configured_to_run_as_a_non_root_user",
            user not in ("", "0", "root") and not user.startswith("0:"),
            f"User={user!r}",
        )
        ev.check(
            f"{short}_image_carries_its_release_version_and_source_revision",
            labels.get("org.opencontainers.image.version") == version
            and labels.get("org.opencontainers.image.revision") == revision,
            f"version {labels.get('org.opencontainers.image.version')}, revision {labels.get('org.opencontainers.image.revision')}",
        )

        try:
            image = load_image_for_smoke_test(name, spec, version, revision, epoch)
            if name == "aiops-backend":
                probe = smoke_backend(image)
                ev.check(
                    "backend_every_module_the_service_scripts_import_imports_and_the_operator_api_answers_its_health_check_in_the_read_only_no_capability_container_as_a_non_root_uid",
                    not probe["failed"] and probe["uid"] != 0 and probe["api_starts"],
                    f"{probe['modules']} modules from {probe['scripts']} scripts, uid {probe['uid']}, failed {probe['failed'][:3]}; the operator API answers its health check: {probe['api_starts']} {probe['api_start_error']}",
                )
            else:
                probe = smoke_frontend(image)
                ev.check(
                    "frontend_serves_the_console_read_only_without_capabilities_with_its_security_headers_as_a_non_root_uid",
                    probe["status_200"]
                    and probe["csp"]
                    and probe["nosniff"]
                    and probe["uid"] not in ("", "0"),
                    str(probe),
                )
        except (RuntimeError, AttributeError, ValueError) as exc:
            ev.check(f"{short}_runs_as_built_from_the_archive", False, str(exc)[-500:])

        try:
            sbom = sbom_of_archive(archive, OUT / f"sbom_{short}.cdx.json")
            components = sbom["components"]
            ev.check(
                f"{short}_sbom_generated_from_the_exact_archive",
                len(components) > 0,
                f"{len(components)} components",
            )
            if name == "aiops-backend":
                found = {
                    c["name"].lower().replace("_", "-"): c.get("version")
                    for c in components
                    if c.get("type") == "library"
                }
                missing = sorted(p for p, v in lock_pins.items() if found.get(p) != v)
                extra = sorted(d for d in DEV_ONLY if d in found)
                ev.check(
                    "backend_sbom_lists_every_locked_package_at_its_pinned_version_and_no_development_package",
                    not missing and not extra,
                    f"{len(lock_pins)} pinned; missing/different {missing[:5]}; dev {extra}",
                )
            signed.append(Path(sbom["path"]))
        except RuntimeError as exc:
            ev.check(f"{short}_sbom_generated_from_the_exact_archive", False, str(exc)[-500:])

        try:
            scan = trivy_of_archive(archive, OUT / f"trivy_{short}.json")
            ev.check(
                f"{short}_no_fixable_critical_or_high_vulnerability",
                not scan["fixable"],
                f"{len(scan['fixable'])} fixable; {scan['total']} critical/high in all; unfixable {scan['unfixable_ids'][:5]}",
            )
            ev.metrics[f"trivy_{short}"] = {
                "total": scan["total"],
                "fixable": scan["fixable"][:20],
                "unfixable_ids": scan["unfixable_ids"],
            }
        except RuntimeError as exc:
            ev.check(f"{short}_no_fixable_critical_or_high_vulnerability", False, str(exc)[-500:])

        provenance = {
            "_type": "https://in-toto.io/Statement/v1",
            "predicateType": "https://slsa.dev/provenance/v1",
            "subject": [
                {"name": f"{name}:{version}", "digest": {"sha256": first.removeprefix("sha256:")}}
            ],
            "predicate": {
                "buildType": "https://aiops-project.local/dockerfile-build/v1",
                "builder": {"id": "local-devsecops-workstation"},
                "invocation": {
                    "configSource": {
                        "path": str(spec["dockerfile"].relative_to(br.REPO_ROOT)).replace("\\", "/")
                    },
                    "parameters": {
                        "SOURCE_DATE_EPOCH": epoch,
                        "VERSION": version,
                        "REVISION": revision,
                    },
                },
                "materials": [
                    {
                        "uri": "git+local-repo",
                        "digest": {"sha1": git["head"]},
                        "dirty": git["dirty"],
                    },
                    {"uri": "tree:build-context-inputs", "digest": {"sha256": tree_hash}},
                    *[{"uri": f"docker+{b}"} for b in bases],
                    *(
                        [
                            {
                                "uri": "file:backend/requirements-lock.txt",
                                "digest": {"sha256": br.sha256_bytes(lock_text.encode())},
                            }
                        ]
                        if name == "aiops-backend"
                        else []
                    ),
                ],
                "metadata": {
                    "buildFinishedOn": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "reproducible": first == second,
                    "completeness": {
                        "materials": True,
                        "note": "source identity is a content hash of every file the Dockerfile COPYs; the working tree may not be fully committed",
                    },
                },
            },
        }
        path = OUT / f"provenance_{short}.json"
        path.write_text(json.dumps(provenance, indent=2, sort_keys=True), encoding="utf-8")
        signed.append(path)
        ev.metrics[short] = {
            "digest": first,
            "version": version,
            "revision": revision,
            "user": user,
            "context_tree_sha256": tree_hash,
        }

    # ---- sign and verify every artifact, then prove the admission gate
    done: list[tuple[Path, Path]] = []
    ok_all = True
    for artifact in signed:
        try:
            sig = br.cosign_sign_blob(artifact)
            ok_all &= br.cosign_verify_blob(artifact, sig)
            done.append((artifact, sig))
        except RuntimeError as exc:
            ev.check(f"sign_{artifact.name}", False, str(exc)[-500:])
            ok_all = False
    ev.check(
        "every_sbom_and_provenance_statement_is_signed_and_the_signature_verifies",
        bool(done) and ok_all and len(done) == len(signed),
        f"{len(done)}/{len(signed)} signed",
    )
    prov = next(((a, s) for a, s in done if a.name.startswith("provenance_")), None)
    if prov:
        tampered = prov[0].with_name(prov[0].stem + ".tampered" + prov[0].suffix)
        data = bytearray(prov[0].read_bytes())
        data[-1] ^= 0xFF
        tampered.write_bytes(bytes(data))
        genuine, forged = br.admit(prov[0], prov[1]), br.admit(tampered, prov[1])
        ev.check(
            "the_admission_gate_admits_the_genuine_provenance_and_rejects_a_one_byte_tampered_copy",
            genuine and not forged,
            f"genuine admitted={genuine}, tampered admitted={forged}",
        )
    ev.notes["not_proven"] = (
        "nothing is pushed to a registry and no image signature is attached in a registry (P13.06/P13.07); the cluster admission policy is enforced only once a cluster runs it (P11.03)"
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
