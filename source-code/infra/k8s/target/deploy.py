#!/usr/bin/env python3
"""P11.03: deploy the platform to the target cluster and take it down again. Runs ON the target host (kubectl, docker, openssl; nothing else).

    deploy.py cluster                  create the kind cluster (Kubernetes v1.36.4) and install Calico, which enforces NetworkPolicy
    deploy.py images ARCHIVE_DIR       load the platform images and build the edge image, then load them into the kind node
    deploy.py up                       namespace and policies, secrets, config, database, migrations, every workload; waits for Ready
    deploy.py status                   what is running
    deploy.py down                     delete the namespace (workloads, claims, secrets) and the generated PKI

Secrets are made HERE, at deployment, and only ever live in the cluster's Secret objects and in `~/aiops-p11/pki` (the test CA that signs the
broker, the gateway and the device certificates); none is read from the repository, none is printed. A second `up` keeps what exists.

Order matters and is the point of a script: the database first, then the migrations as the database owner, then each service's own database
role gets its password (the workloads that connect as a role read it from the `aiops-service-roles` Secret), then everything else.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[3]
K8S = SOURCE_ROOT / "infra" / "k8s"
sys.path.insert(0, str(K8S))

import bundles  # noqa: E402

NS = "aiops"
HOME = Path.home() / "aiops-p11"
PKI = HOME / "pki"
ROLES = (
    "svc_command_executor",
    "svc_outcome_verifier",
    "svc_scenario_control",
    "svc_platform_correlator",
    "svc_remediation_worker",
)
DEVICES = ("e2e-device-a", "e2e-device-b", *(f"e2e-load-{i}" for i in range(16)))
VERSION = "0.1.0"


def run(
    cmd: list[str], *, stdin: str | bytes | None = None, check: bool = True, timeout: int = 600
) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd,
        input=stdin,
        capture_output=True,
        text=isinstance(stdin, str) or stdin is None,
        timeout=timeout,
        check=False,
    )
    if check and proc.returncode != 0:
        err = (
            proc.stderr if isinstance(proc.stderr, str) else proc.stderr.decode("utf-8", "replace")
        )
        raise RuntimeError(f"{' '.join(cmd[:4])}... failed ({proc.returncode}): {err[-1500:]}")
    return proc


def kubectl(
    *args: str, stdin: str | None = None, check: bool = True, timeout: int = 600
) -> subprocess.CompletedProcess:
    return run(["kubectl", *args], stdin=stdin, check=check, timeout=timeout)


def apply_docs(docs: list[dict]) -> None:
    kubectl("apply", "-f", "-", stdin="\n---\n".join(json.dumps(d) for d in docs))


def b64(data: bytes | str) -> str:
    return base64.b64encode(data if isinstance(data, bytes) else data.encode("utf-8")).decode(
        "ascii"
    )


def labels() -> dict:
    return {"app.kubernetes.io/part-of": "aiops-platform"}


def secret(name: str, entries: dict[str, bytes | str], kind: str = "Opaque") -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": kind,
        "metadata": {"name": name, "namespace": NS, "labels": labels()},
        "data": {k: b64(v) for k, v in entries.items()},
    }


def configmap(
    name: str, text: dict[str, str] | None = None, binary: dict[str, bytes] | None = None
) -> dict:
    doc = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": name, "namespace": NS, "labels": labels()},
    }
    if text:
        doc["data"] = text
    if binary:
        doc["binaryData"] = {k: b64(v) for k, v in binary.items()}
    return doc


def exists(kind: str, name: str) -> bool:
    return kubectl("-n", NS, "get", kind, name, check=False).returncode == 0


# ------------------------------------------------------------------------------------------------------------------------------- PKI
def openssl(*args: str, cwd: Path) -> None:
    subprocess.run(["openssl", *args], cwd=cwd, capture_output=True, check=True, timeout=60)


def ca() -> tuple[Path, Path]:
    PKI.mkdir(parents=True, exist_ok=True)
    PKI.chmod(0o700)
    if not (PKI / "ca.crt").exists():
        openssl(
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-days",
            "3650",
            "-nodes",
            "-keyout",
            "ca.key",
            "-out",
            "ca.crt",
            "-subj",
            "/CN=aiops-platform-test-ca",
            cwd=PKI,
        )
        (PKI / "ca.key").chmod(0o600)
    return PKI / "ca.crt", PKI / "ca.key"


def issue(name: str, cn: str, san: str | None = None) -> tuple[bytes, bytes]:
    """A leaf certificate signed by the platform CA. Returns (certificate, key)."""
    ca()
    crt, key = PKI / f"{name}.crt", PKI / f"{name}.key"
    if not crt.exists():
        openssl(
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            f"{name}.key",
            "-out",
            f"{name}.csr",
            "-subj",
            f"/CN={cn}",
            cwd=PKI,
        )
        args = [
            "x509",
            "-req",
            "-in",
            f"{name}.csr",
            "-CA",
            "ca.crt",
            "-CAkey",
            "ca.key",
            "-CAcreateserial",
            "-out",
            f"{name}.crt",
            "-days",
            "3650",
        ]
        if san:
            (PKI / f"{name}.ext").write_text(f"subjectAltName={san}\n", encoding="utf-8")
            args += ["-extfile", f"{name}.ext"]
        openssl(*args, cwd=PKI)
        (PKI / f"{name}.csr").unlink()
        key.chmod(0o600)
    return crt.read_bytes(), key.read_bytes()


def rogue() -> tuple[bytes, bytes]:
    """A client certificate from a DIFFERENT CA, for the negative case (the broker must refuse it)."""
    crt, key = PKI / "rogue.crt", PKI / "rogue.key"
    if not crt.exists():
        openssl(
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-days",
            "3650",
            "-nodes",
            "-keyout",
            "rogue-ca.key",
            "-out",
            "rogue-ca.crt",
            "-subj",
            "/CN=untrusted-outside-ca",
            cwd=PKI,
        )
        openssl(
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            "rogue.key",
            "-out",
            "rogue.csr",
            "-subj",
            "/CN=rogue-device",
            cwd=PKI,
        )
        openssl(
            "x509",
            "-req",
            "-in",
            "rogue.csr",
            "-CA",
            "rogue-ca.crt",
            "-CAkey",
            "rogue-ca.key",
            "-CAcreateserial",
            "-out",
            "rogue.crt",
            "-days",
            "3650",
            cwd=PKI,
        )
        for junk in ("rogue.csr", "rogue-ca.srl"):
            (PKI / junk).unlink(missing_ok=True)
        key.chmod(0o600)
    return crt.read_bytes(), key.read_bytes()


# ------------------------------------------------------------------------------------------------------------------------ secrets, config
def ensure_secrets() -> None:
    ca_crt, _ = ca()
    docs = []
    if not exists("secret", "aiops-secrets"):
        docs.append(secret("aiops-secrets", {
            "postgres-user": "aiops_app", "postgres-password": secrets.token_urlsafe(32),
            "keycloak-admin": "admin", "keycloak-admin-password": secrets.token_urlsafe(24),
            "grafana-admin-password": secrets.token_urlsafe(24),
        }))  # fmt: skip
    pg_crt, pg_key = issue(
        "postgres",
        "aiops-postgres",
        "DNS:aiops-postgres,DNS:aiops-postgres.aiops.svc,DNS:aiops-postgres.aiops.svc.cluster.local",
    )
    mq_crt, mq_key = issue(
        "mqtt",
        "aiops-mqtt-broker",
        "DNS:aiops-mqtt-broker,DNS:aiops-mqtt-broker.aiops.svc,DNS:aiops-mqtt-broker.aiops.svc.cluster.local",
    )
    gw_crt, gw_key = issue("gateway", "gateway")
    docs.append(
        secret(
            "aiops-tls-postgres",
            {"ca.crt": ca_crt.read_bytes(), "server.crt": pg_crt, "server.key": pg_key},
        )
    )
    docs.append(
        secret(
            "aiops-tls-mqtt",
            {"ca.crt": ca_crt.read_bytes(), "server.crt": mq_crt, "server.key": mq_key},
        )
    )
    docs.append(
        secret(
            "aiops-tls-gateway",
            {"ca.crt": ca_crt.read_bytes(), "tls.crt": gw_crt, "tls.key": gw_key},
        )
    )
    devices: dict[str, bytes] = {"ca.crt": ca_crt.read_bytes()}
    for device in DEVICES:
        crt, key = issue(f"device-{device}", device)
        devices[f"{device}.crt"], devices[f"{device}.key"] = crt, key
    devices["rogue.crt"], devices["rogue.key"] = rogue()
    docs.append(secret("aiops-tls-devices", devices))
    apply_docs(docs)


def ensure_config() -> None:
    docs = []
    for name in bundles.names():
        files = bundles.bundle(name)
        text, binary = {}, {}
        for key, (_path, content) in files.items():
            try:
                text[key] = content.decode("utf-8")
            except UnicodeDecodeError:
                binary[key] = content
        docs.append(configmap(name, text, binary))
    site = SOURCE_ROOT.parent / "edge-site"
    baseline = SOURCE_ROOT / "models" / "registry" / "baseline" / "baseline_v1.json"
    docs.append(configmap("aiops-edge-site", {"config.json": (site / "config.json").read_text(encoding="utf-8"), "devices.jsonl": (site / "devices.jsonl").read_text(encoding="utf-8"),
                                              "events.jsonl": (site / "events.jsonl").read_text(encoding="utf-8"), "baseline_v1.json": baseline.read_text(encoding="utf-8")}))  # fmt: skip
    package = SOURCE_ROOT / "models" / "registry" / "traffic-safety-blockage" / "1.0.0"
    model = {
        n: (package / n).read_bytes()
        for n in ("artifact_manifest.json", "io_schema.json", "golden_vectors.json", "model.onnx")
    }
    docs.append(configmap("aiops-edge-model", binary=model))
    apply_docs(docs)


# ------------------------------------------------------------------------------------------------------------------------------ workloads
def wait_rollout(kind: str, name: str, timeout: int = 420) -> None:
    kubectl(
        "-n",
        NS,
        "rollout",
        "status",
        f"{kind}/{name}",
        f"--timeout={timeout}s",
        timeout=timeout + 30,
    )


def run_job(name: str, timeout: int = 300) -> str:
    kubectl("-n", NS, "delete", "job", name, "--ignore-not-found", "--wait=true")
    kubectl("apply", "-f", str(K8S / "jobs.yaml"), "-l", f"app.kubernetes.io/name={name}")
    done = kubectl(
        "-n",
        NS,
        "wait",
        "--for=condition=complete",
        f"job/{name}",
        f"--timeout={timeout}s",
        check=False,
        timeout=timeout + 30,
    )
    logs = kubectl("-n", NS, "logs", f"job/{name}", check=False).stdout
    if done.returncode != 0:
        raise RuntimeError(f"job {name} did not complete:\n{logs[-1500:]}\n{done.stderr[-500:]}")
    return logs


def set_role_passwords() -> None:
    """Give each workload-identity role its own password: set in the database, and put in the Secret the workloads read."""
    if exists("secret", "aiops-service-roles"):
        return
    passwords = {role: secrets.token_urlsafe(32) for role in ROLES}
    user = base64.b64decode(
        kubectl(
            "-n", NS, "get", "secret", "aiops-secrets", "-o", "jsonpath={.data.postgres-user}"
        ).stdout
    ).decode()
    for role, password in passwords.items():
        statement = f"ALTER ROLE {role} PASSWORD '{password}'"  # token_urlsafe: [A-Za-z0-9_-], nothing to escape
        kubectl(
            "-n",
            NS,
            "exec",
            "aiops-postgres-0",
            "--",
            "psql",
            "-U",
            user,
            "-d",
            "aiops",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            statement,
        )
    apply_docs(
        [
            secret(
                "aiops-service-roles",
                {"service_role_secrets.json": json.dumps(passwords, indent=2, sort_keys=True)},
            )
        ]
    )


def up() -> None:
    kubectl("apply", "-f", str(K8S / "rbac-and-network-policy.yaml"))
    ensure_secrets()
    ensure_config()
    kubectl(
        "apply", "-f", str(K8S / "workloads.yaml"), "-l", "app.kubernetes.io/name=aiops-postgres"
    )
    wait_rollout("statefulset", "aiops-postgres")
    print(run_job("aiops-migrate"))
    set_role_passwords()
    kubectl("apply", "-f", str(K8S / "workloads.yaml"))
    for kind, name in (
        ("statefulset", "aiops-kafka-broker"),
        ("statefulset", "aiops-mqtt-broker"),
        ("statefulset", "aiops-keycloak"),
        ("deployment", "aiops-opa"),
    ):
        wait_rollout(kind, name)
    docs = json.loads(kubectl("-n", NS, "get", "deploy,sts", "-o", "json").stdout)["items"]
    for d in docs:
        wait_rollout(d["kind"].lower(), d["metadata"]["name"], timeout=300)
    status()


def status() -> None:
    print(kubectl("-n", NS, "get", "pods", "-o", "wide").stdout)


CALICO_URL = "https://raw.githubusercontent.com/projectcalico/calico/v3.32.2/manifests/calico.yaml"
CALICO_SHA256 = "a8c828a06a87c629a282ebbc424895b77f3a030251993e41ea400a743675bb02"
POD_CIDR = "10.200.0.0/16"


def cluster() -> None:
    """kind cluster from kind-aiops-p11.yaml (its own kubeconfig file: the user's ~/.kube/config is not touched), then Calico with the cluster's pod range."""
    import hashlib
    import re
    import urllib.request

    if "aiops-p11" not in run(["kind", "get", "clusters"]).stdout.split():
        run(
            [
                "kind",
                "create",
                "cluster",
                "--config",
                str(K8S / "target" / "kind-aiops-p11.yaml"),
                "--wait",
                "0s",
            ],
            check=False,
            timeout=900,
        )
    HOME.mkdir(parents=True, exist_ok=True)
    kubeconfig = HOME / "kubeconfig"
    kubeconfig.write_text(
        run(["kind", "get", "kubeconfig", "--name", "aiops-p11"]).stdout, encoding="utf-8"
    )
    kubeconfig.chmod(0o600)
    os.environ["KUBECONFIG"] = str(kubeconfig)
    manifest = urllib.request.urlopen(CALICO_URL, timeout=60).read()  # noqa: S310 - a pinned https URL, its hash checked next
    if hashlib.sha256(manifest).hexdigest() != CALICO_SHA256:
        raise RuntimeError(
            "the Calico manifest does not match the pinned hash: refusing to apply it"
        )
    pattern = r'# - name: CALICO_IPV4POOL_CIDR\n(\s*)#   value: "192.168.0.0/16"'
    text, changed = re.subn(
        pattern,
        lambda m: f'- name: CALICO_IPV4POOL_CIDR\n{m.group(1)}  value: "{POD_CIDR}"',
        manifest.decode("utf-8"),
    )
    if changed != 1:
        raise RuntimeError(
            "the Calico manifest no longer has the pod-range line this script rewrites"
        )
    kubectl("apply", "-f", "-", stdin=text)
    kubectl("wait", "--for=condition=Ready", "node", "--all", "--timeout=300s", timeout=330)
    print("cluster ready:", kubectl("get", "nodes").stdout)


def images(archive_dir: str) -> None:
    """Load the platform images (built and signed on the workstation) and build the edge image here, then put them in the kind node."""
    for short, tag in (
        ("backend", f"aiops-backend:{VERSION}"),
        ("frontend", f"aiops-frontend:{VERSION}"),
    ):
        archive = next(
            p
            for p in (
                Path(archive_dir) / f"{short}-1.oci.tar",
                Path(archive_dir) / f"{short}.oci.tar",
            )
            if p.exists()
        )
        out = run(["docker", "load", "-i", str(archive)]).stdout
        image_id = out.strip().split()[-1]
        run(["docker", "tag", image_id, tag])
        print(f"{tag} = {image_id}")
    run(
        [
            "docker",
            "build",
            "-q",
            "-f",
            str(SOURCE_ROOT / "edge" / "Dockerfile"),
            "-t",
            f"aiops-edge:{VERSION}",
            str(SOURCE_ROOT),
        ],
        timeout=1200,
    )
    for tag in (f"aiops-backend:{VERSION}", f"aiops-frontend:{VERSION}", f"aiops-edge:{VERSION}"):
        run(["kind", "load", "docker-image", tag, "--name", "aiops-p11"], timeout=900)
    print("images loaded into the node")


def down() -> None:
    kubectl("delete", "namespace", NS, "--ignore-not-found", "--wait=true", timeout=600)
    print("namespace deleted; the PKI in", PKI, "is kept until you remove it")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["cluster", "images", "up", "status", "down"])
    parser.add_argument("archive_dir", nargs="?")
    args = parser.parse_args()
    if args.command == "images":
        images(args.archive_dir or str(HOME))
    else:
        {"cluster": cluster, "up": up, "status": status, "down": down}[args.command]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
