#!/usr/bin/env python3
"""P11.04 acceptance evidence: transport, identity, policy, network and (separately, P09.09) runtime detection, checked NATIVELY on the target.

    KUBECONFIG=... python3 verify_security_target.py        # ON the target host; writes docs/evidence/p11_04_target_security.json

Each control has a positive check (the legitimate thing works) and a negative one (the attack or the mistake is refused), on the real cluster,
through the real Services and a NetworkPolicy-enforcing CNI:

  TLS/OIDC/OPA   the in-cluster Job `aiops-security` (backend/target_security.py): the mutually-authenticated MQTT listener, PostgreSQL TLS,
                 Keycloak's Authorization Code + PKCE and the ways around it, the API's role matrix, the policy engine's decisions.
  Policy outage  the policy engine is scaled to zero and a request that was permitted must now be refused (fail closed); then it is restored.
  Admission      the namespace's Pod Security `restricted` profile refuses a privileged pod, a pod with no hardening, a host-path mount and the host network
                 (server-side dry runs), and admits a pod that meets the profile. RBAC: no workload identity may read a secret, create a pod, exec into one
                 or list nodes, and no service-account token is mounted in a pod.
  Network        from the real pods: what a workload's NetworkPolicy allows connects, and everything else times out - a database that only the
                 services holding a database identity can reach, an edge runtime that can reach nothing, no way out to the internet, no plain
                 MQTT port, and a pod of another namespace that cannot reach a workload of this one.

Not covered here, stated in the evidence: the runtime detections (P09.09 has its own run), and anything about the host's K3s or the other projects'
clusters, which this deployment neither uses nor touches.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[3]
K8S = SOURCE_ROOT / "infra" / "k8s"
sys.path.insert(0, str(SOURCE_ROOT))

import yaml  # noqa: E402
from backend.evidence import Evidence  # noqa: E402

NS = "aiops"
ev = Evidence("P11.04", "p11_04_target_security", docs_name="p11_04_target_security")


def kubectl(
    *args: str, check: bool = True, timeout: int = 180, stdin: str | None = None
) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["kubectl", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        input=stdin,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args[:5])} failed: {proc.stderr[-300:]}")
    return proc


def run_job(name: str, phase: str, timeout: int = 420) -> dict:
    kubectl("-n", NS, "delete", "job", name, "--ignore-not-found", "--wait=true")
    docs = [
        d
        for d in yaml.safe_load_all((K8S / "jobs.yaml").read_text(encoding="utf-8"))
        if d and d["metadata"]["name"] == name
    ]
    docs[0]["spec"]["template"]["spec"]["containers"][0]["args"] = [
        "backend/target_security.py",
        phase,
    ]
    kubectl("apply", "-f", "-", stdin=json.dumps(docs[0]))
    kubectl(
        "-n",
        NS,
        "wait",
        "--for=condition=complete",
        f"job/{name}",
        f"--timeout={timeout}s",
        check=False,
        timeout=timeout + 20,
    )
    log = kubectl("-n", NS, "logs", f"job/{name}", check=False).stdout.strip().splitlines()
    return (
        json.loads(log[-1])
        if log and log[-1].startswith("{")
        else {
            "passed": False,
            "checks": [
                {
                    "name": f"job_{name}_{phase}_produced_a_report",
                    "ok": False,
                    "detail": "\n".join(log[-6:])[-500:],
                }
            ],
        }
    )


# a TCP connect from inside a pod: python where the image has it, busybox `nc` where it does not
PY_CONNECT = "import socket,sys\ntry:\n s=socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=4); s.close(); print('connected')\nexcept OSError as e:\n print('failed', type(e).__name__)"


def connect(kind: str, workload: str, host: str, port: int, python: bool = True) -> str:
    if python:
        out = kubectl(
            "-n",
            NS,
            "exec",
            f"{kind}/{workload}",
            "--",
            "python3",
            "-c",
            PY_CONNECT,
            host,
            str(port),
            check=False,
            timeout=60,
        ).stdout
    else:
        out = kubectl(
            "-n",
            NS,
            "exec",
            f"{kind}/{workload}",
            "--",
            "sh",
            "-c",
            f"nc -z -w 4 {host} {port} && echo connected || echo failed",
            check=False,
            timeout=60,
        ).stdout
    return "connected" if "connected" in out else "failed"


def listening_ports(kind: str, workload: str) -> set[int]:
    out = kubectl(
        "-n",
        NS,
        "exec",
        f"{kind}/{workload}",
        "--",
        "sh",
        "-c",
        "cat /proc/net/tcp /proc/net/tcp6",
        check=False,
        timeout=60,
    ).stdout
    ports = set()
    for line in out.splitlines()[0:]:
        parts = line.split()
        if len(parts) > 3 and parts[3] == "0A" and ":" in parts[1]:
            ports.add(int(parts[1].rsplit(":", 1)[1], 16))
    return ports


def main() -> int:  # noqa: PLR0915
    # ---- the in-cluster Job
    report = run_job("aiops-security", "main")
    for c in report["checks"]:
        ev.check(c["name"], c["ok"], c["detail"])
    ev.metrics["security_job_main"] = report

    # ---- policy outage: fail closed
    kubectl("-n", NS, "scale", "deploy/aiops-opa", "--replicas=0")
    kubectl("-n", NS, "rollout", "status", "deploy/aiops-opa", "--timeout=120s", check=False)
    try:
        outage = run_job("aiops-security", "outage")
    finally:
        kubectl("-n", NS, "scale", "deploy/aiops-opa", "--replicas=1")
        kubectl("-n", NS, "rollout", "status", "deploy/aiops-opa", "--timeout=180s", check=False)
    for c in outage["checks"]:
        ev.check(c["name"], c["ok"], c["detail"])
    ev.metrics["security_job_outage"] = outage

    # ---- network policy, from the real pods
    cases = [
        # (label, kind, workload, host, port, python?, expected)
        (
            "frontend_to_api_is_allowed",
            "deploy",
            "aiops-frontend",
            "aiops-api",
            8100,
            False,
            "connected",
        ),
        (
            "frontend_cannot_reach_the_database",
            "deploy",
            "aiops-frontend",
            "aiops-postgres",
            5432,
            False,
            "failed",
        ),
        (
            "api_reaches_the_database",
            "deploy",
            "aiops-api",
            "aiops-postgres",
            5432,
            True,
            "connected",
        ),
        (
            "api_reaches_the_policy_engine",
            "deploy",
            "aiops-api",
            "aiops-opa",
            8181,
            True,
            "connected",
        ),
        (
            "api_cannot_reach_kafka",
            "deploy",
            "aiops-api",
            "aiops-kafka-broker",
            9092,
            True,
            "failed",
        ),
        (
            "api_cannot_reach_the_mqtt_broker",
            "deploy",
            "aiops-api",
            "aiops-mqtt-broker",
            8883,
            True,
            "failed",
        ),
        ("api_cannot_reach_the_internet", "deploy", "aiops-api", "1.1.1.1", 443, True, "failed"),
        (
            "ingestion_reaches_kafka",
            "deploy",
            "aiops-ingestion-gateway",
            "aiops-kafka-broker",
            9092,
            True,
            "connected",
        ),
        (
            "ingestion_cannot_reach_keycloak",
            "deploy",
            "aiops-ingestion-gateway",
            "aiops-keycloak",
            8080,
            True,
            "failed",
        ),
        (
            "the_gateway_reaches_the_broker_on_the_tls_port",
            "sts",
            "aiops-mqtt-gateway",
            "aiops-mqtt-broker",
            8883,
            True,
            "connected",
        ),
        (
            "the_gateway_cannot_reach_the_database",
            "sts",
            "aiops-mqtt-gateway",
            "aiops-postgres",
            5432,
            True,
            "failed",
        ),
        (
            "the_edge_runtime_cannot_reach_kafka",
            "deploy",
            "aiops-edge-runtime",
            "aiops-kafka-broker",
            9092,
            True,
            "failed",
        ),
        (
            "the_edge_runtime_cannot_reach_the_database",
            "deploy",
            "aiops-edge-runtime",
            "aiops-postgres",
            5432,
            True,
            "failed",
        ),
        (
            "the_edge_runtime_cannot_reach_the_internet",
            "deploy",
            "aiops-edge-runtime",
            "1.1.1.1",
            443,
            True,
            "failed",
        ),
        (
            "prometheus_scrapes_the_edge_runtime",
            "sts",
            "aiops-prometheus",
            "aiops-edge-runtime",
            9100,
            False,
            "connected",
        ),
        (
            "prometheus_cannot_reach_the_database",
            "sts",
            "aiops-prometheus",
            "aiops-postgres",
            5432,
            False,
            "failed",
        ),
        (
            "grafana_reaches_prometheus",
            "sts",
            "aiops-grafana",
            "aiops-prometheus",
            9090,
            False,
            "connected",
        ),
        (
            "grafana_cannot_reach_the_database",
            "sts",
            "aiops-grafana",
            "aiops-postgres",
            5432,
            False,
            "failed",
        ),
    ]
    results = {}
    for label, kind, workload, host, port, python, expected in cases:
        got = connect(kind, workload, host, port, python)
        results[label] = got
        ev.check(f"network_{label}", got == expected, f"expected {expected}, got {got}")
    # a pod of ANOTHER namespace must not reach a workload of this one (default deny on ingress)
    outsider = kubectl(
        "-n", "default", "run", "p1104-outsider", "--image=busybox:1.37", "--restart=Never", "--rm", "-i", "--quiet", "--command", "--",
        "sh", "-c", "nc -z -w 4 aiops-postgres.aiops.svc.cluster.local 5432 && echo connected || echo failed",
        check=False, timeout=180,
    )  # fmt: skip
    got = "connected" if "connected" in outsider.stdout else "failed"
    results["outsider_namespace_to_postgres"] = got
    ev.check(
        "network_a_pod_of_another_namespace_cannot_reach_the_database",
        got == "failed",
        f"got {got}",
    )
    ev.metrics["network"] = results

    # ---- admission: the namespace's Pod Security `restricted` profile refuses what it should, admits what it should
    compliant = {
        "runAsNonRoot": True,
        "runAsUser": 10099,
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "seccompProfile": {"type": "RuntimeDefault"},
    }

    def pod(name: str, container_security: dict | None, spec: dict | None = None) -> str:
        return json.dumps({
            "apiVersion": "v1", "kind": "Pod", "metadata": {"name": name, "namespace": NS},
            "spec": {"containers": [{"name": "c", "image": "aiops-backend:0.1.0", **({"securityContext": container_security} if container_security is not None else {})}], **(spec or {})},
        })  # fmt: skip

    trials = {
        # (privileged excludes allowPrivilegeEscalation=false, which the API itself would reject as invalid: the profile, not the schema, must be what refuses it)
        "a_privileged_pod": (
            pod(
                "adm-privileged",
                {k: v for k, v in compliant.items() if k != "allowPrivilegeEscalation"}
                | {"privileged": True},
            ),
            False,
        ),
        "a_pod_that_may_run_as_root_with_no_hardening": (pod("adm-root", None), False),
        "a_pod_that_mounts_a_host_path": (
            pod("adm-hostpath", compliant, {"volumes": [{"name": "h", "hostPath": {"path": "/"}}]}),
            False,
        ),
        "a_pod_on_the_host_network": (pod("adm-hostnet", compliant, {"hostNetwork": True}), False),
        "a_pod_that_meets_the_restricted_profile": (pod("adm-good", compliant), True),
    }
    admission = {}
    for label, (manifest, should_pass) in trials.items():
        proc = kubectl("apply", "--dry-run=server", "-f", "-", check=False, stdin=manifest)
        refused_by_policy = proc.returncode != 0 and "PodSecurity" in proc.stderr
        admitted = proc.returncode == 0
        admission[label] = (
            "admitted"
            if admitted
            else (
                "refused by PodSecurity"
                if refused_by_policy
                else f"failed: {proc.stderr.strip()[-120:]}"
            )
        )
        ev.check(
            f"admission_{'admits' if should_pass else 'refuses'}_{label}",
            admitted if should_pass else refused_by_policy,
            admission[label],
        )
    ev.metrics["admission"] = admission

    # ---- RBAC: no workload's identity may read a secret, start a pod or exec into one; and no token is even mounted to try with
    rbac = {}
    for account in ("aiops-api", "aiops-mqtt-gateway", "aiops-postgres", "aiops-ingestion-gateway"):
        answers = {}
        for verb, resource in (
            ("get", "secrets"),
            ("list", "secrets"),
            ("create", "pods"),
            ("create", "pods/exec"),
            ("list", "nodes"),
        ):
            out = kubectl(
                "auth",
                "can-i",
                verb,
                resource,
                "-n",
                NS,
                f"--as=system:serviceaccount:{NS}:{account}",
                check=False,
            ).stdout.strip()
            answers[f"{verb} {resource}"] = out
        rbac[account] = answers
        ev.check(
            f"rbac_{account}_can_do_none_of_read_secrets_create_pods_exec_or_list_nodes",
            all(a == "no" for a in answers.values()),
            str(answers),
        )
    ev.metrics["rbac"] = rbac
    token = kubectl(
        "-n",
        NS,
        "exec",
        "deploy/aiops-api",
        "--",
        "ls",
        "/var/run/secrets/kubernetes.io/serviceaccount",
        check=False,
    )
    ev.check(
        "no_service_account_token_is_mounted_in_the_api_pod",
        token.returncode != 0,
        (token.stderr + token.stdout).strip()[-100:],
    )

    # ---- what each listener really exposes
    broker = listening_ports("sts", "aiops-mqtt-broker")
    ev.check(
        "the_mqtt_broker_listens_on_the_mtls_port_only_there_is_no_plain_listener",
        broker == {8883},
        f"listening {sorted(broker)}",
    )
    ev.metrics["mqtt_listening_ports"] = sorted(broker)

    ev.notes["not_covered"] = (
        "runtime detections on the target are P09.09's own run; nothing about the host's K3s or the other projects' clusters was touched"
    )
    ev.notes["taken_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
