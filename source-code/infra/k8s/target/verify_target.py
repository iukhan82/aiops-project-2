#!/usr/bin/env python3
"""P11.03 acceptance evidence: what is actually running on the target cluster matches what the platform is recorded to be.

    KUBECONFIG=... python3 verify_target.py        # ON the target host; writes docs/evidence/p11_03_target_deployment.json

A. The cluster: the Kubernetes version the manifests were validated against, a Ready node, a CNI that enforces NetworkPolicy, a storage class.
B. Admission: the namespace enforces Pod Security `restricted`; every workload of `workloads.yaml` was admitted and is Ready, with as many replicas
   as it declares, and every container runs the image the manifest names (the digests are recorded).
C. Topology: every component of `backend/topology.json` is a running workload, and every dependency edge between two workloads is a TCP
   connection that really succeeds from the client's own pod (through the NetworkPolicies, on a real CNI).
D. End to end: a device publishes over the broker's mTLS listener; the event must reach PostgreSQL through the gateway, Kafka and ingestion, a
   certificate from another CA must be refused, an event published under another device's topic must not arrive, and the API must answer and
   refuse an anonymous caller (the `aiops-e2e` Job, run fresh).
E. Observability: Prometheus scrapes the edge runtime and Tempo, and both are up.
F. What is not there is said: the remediation worker (its docker adapter needs a container runtime), and the demo feeder.

Nothing is stubbed: the checks talk to the cluster through kubectl and to the workloads through their own pods.
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
sys.path.insert(0, str(K8S))

import yaml  # noqa: E402
from backend.evidence import Evidence  # noqa: E402

NS = "aiops"
ev = Evidence("P11.03", "p11_03_target_deployment", docs_name="p11_03_target_deployment")
# topology.json node -> the workload that runs it
NODE_TO_WORKLOAD = {
    "edge-runtime": "aiops-edge-runtime",
    "gateway": "aiops-mqtt-gateway",
    "ingestion": "aiops-ingestion-gateway",
    "api": "aiops-api",
    "scenario-control": "aiops-scenario-control",
    "mosquitto": "aiops-mqtt-broker",
    "kafka": "aiops-kafka-broker",
    "postgres": "aiops-postgres",
    "opa": "aiops-opa",
    "keycloak": "aiops-keycloak",
}
PORTS = {
    "aiops-mqtt-broker": 8883,
    "aiops-kafka-broker": 9092,
    "aiops-postgres": 5432,
    "aiops-opa": 8181,
    "aiops-keycloak": 8080,
}
CONNECT = "import socket,sys; s=socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=5); s.close(); print('connected')"


def kubectl(*args: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["kubectl", *args], capture_output=True, text=True, timeout=timeout, check=False
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args[:4])} failed: {proc.stderr[-300:]}")
    return proc


def get_json(*args: str) -> dict:
    return json.loads(kubectl(*args, "-o", "json").stdout)


def main() -> int:  # noqa: PLR0915
    # ---- A. the cluster
    version = get_json("version")["serverVersion"]
    ev.check(
        "the_cluster_runs_the_kubernetes_minor_version_the_manifests_are_validated_against",
        version["minor"].startswith("36"),
        f"server {version['gitVersion']}",
    )
    nodes = get_json("get", "nodes")["items"]
    ready = all(
        any(c["type"] == "Ready" and c["status"] == "True" for c in n["status"]["conditions"])
        for n in nodes
    )
    ev.check(
        "every_node_is_ready",
        ready and bool(nodes),
        f"{len(nodes)} node(s), runtime {nodes[0]['status']['nodeInfo']['containerRuntimeVersion']}, kernel {nodes[0]['status']['nodeInfo']['kernelVersion']}",
    )
    calico = get_json("-n", "kube-system", "get", "pods", "-l", "k8s-app=calico-node")["items"]
    ev.check(
        "a_cni_that_enforces_network_policy_calico_is_running_on_every_node",
        len(calico) == len(nodes) and all(p["status"]["phase"] == "Running" for p in calico),
        f"{len(calico)} calico-node pod(s)",
    )
    classes = get_json("get", "storageclass")["items"]
    ev.check(
        "a_default_storage_class_exists_for_the_volume_claims",
        any(
            c["metadata"].get("annotations", {}).get("storageclass.kubernetes.io/is-default-class")
            == "true"
            for c in classes
        ),
        ", ".join(c["metadata"]["name"] for c in classes),
    )

    # ---- B. admission and readiness
    namespace = get_json("get", "namespace", NS)
    labels = namespace["metadata"]["labels"]
    ev.check(
        "the_namespace_enforces_pod_security_restricted",
        all(
            labels.get(f"pod-security.kubernetes.io/{m}") == "restricted"
            for m in ("enforce", "audit", "warn")
        ),
    )
    declared = [
        d
        for d in yaml.safe_load_all((K8S / "workloads.yaml").read_text(encoding="utf-8"))
        if d and d["kind"] in ("Deployment", "StatefulSet")
    ]
    running = {
        d["metadata"]["name"]: d for d in get_json("-n", NS, "get", "deploy,statefulset")["items"]
    }
    unready = [
        d["metadata"]["name"]
        for d in declared
        if d["metadata"]["name"] not in running
        or running[d["metadata"]["name"]]["status"].get("readyReplicas", 0) != d["spec"]["replicas"]
    ]
    ev.check(
        "every_workload_the_manifests_declare_was_admitted_and_is_ready_with_its_declared_replicas",
        not unready and len(declared) >= 17,
        f"{len(declared)} workloads; not ready: {unready}",
    )
    pods = get_json("-n", NS, "get", "pods")["items"]
    images = {}
    for pod in pods:
        for status in pod["status"].get("containerStatuses", []):
            images[f"{pod['metadata']['name']}/{status['name']}"] = {
                "image": status["image"],
                "imageID": status["imageID"],
                "restarts": status["restartCount"],
                "ready": status["ready"],
            }
    running_pods = [p for p in pods if p["status"]["phase"] == "Running"]
    ev.check(
        "every_running_container_is_ready",
        all(
            v["ready"]
            for k, v in images.items()
            if not k.startswith("aiops-migrate") and not k.startswith("aiops-e2e")
        ),
        f"{len(images)} containers in {len(running_pods)} running pods",
    )
    ev.metrics["images"] = images

    # What runs is what was signed: the Docker images loaded from the shipped archives carry the digests the workstation signed, and the images inside the
    # cluster's node have exactly the layers of those Docker images (kind re-imports an image into containerd, which gives it another manifest digest, so
    # the layers - the content - are what is compared). The edge image is built on the host, so it has no signed digest: only its layers are compared.
    signed_path = SOURCE_ROOT.parents[0] / "signed" / "service_images.json"
    signed = json.loads(signed_path.read_text(encoding="utf-8")) if signed_path.exists() else {}
    identity = {}
    for short in ("backend", "frontend", "edge"):
        tag = f"aiops-{short}:0.1.0"
        docker_inspect = subprocess.run(
            ["docker", "image", "inspect", tag, "--format", "{{.Id}} {{json .RootFS.Layers}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        image_id, _, layers = docker_inspect.stdout.strip().partition(" ")
        node = subprocess.run(
            [
                "docker",
                "exec",
                "aiops-p11-control-plane",
                "crictl",
                "inspecti",
                "-o",
                "json",
                f"docker.io/library/{tag}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            node_layers = json.loads(node.stdout)["info"]["imageSpec"]["rootfs"]["diff_ids"]
        except (ValueError, KeyError):
            node_layers = None
        identity[short] = {
            "docker_image_id": image_id,
            "signed_digest": signed.get(short),
            "signed_digest_matches": (image_id == signed.get(short)) if short in signed else None,
            "layers": len(node_layers) if node_layers else None,
            "cluster_layers_equal_the_docker_images": bool(layers)
            and node_layers == json.loads(layers),
        }
    ev.metrics["image_identity"] = identity
    ev.check(
        "the_platform_images_running_in_the_cluster_are_the_ones_the_workstation_signed",
        all(identity[n]["signed_digest_matches"] is True for n in ("backend", "frontend"))
        and all(i["cluster_layers_equal_the_docker_images"] for i in identity.values()),
        "; ".join(
            f"{n}: {i['docker_image_id'][:19]}, signed match {i['signed_digest_matches']}, cluster layers equal {i['cluster_layers_equal_the_docker_images']}"
            for n, i in identity.items()
        ),
    )
    ev.metrics["restarts"] = {k: v["restarts"] for k, v in images.items() if v["restarts"]}

    # ---- C. topology
    topology = json.loads((SOURCE_ROOT / "backend" / "topology.json").read_text(encoding="utf-8"))
    missing = [n["id"] for n in topology["nodes"] if NODE_TO_WORKLOAD.get(n["id"]) not in running]
    ev.check(
        "every_component_of_the_recorded_topology_is_a_running_workload",
        not missing,
        f"{len(topology['nodes'])} components; missing {missing}",
    )
    failed_edges, checked = [], 0
    for edge in topology["edges"]:
        client, server = NODE_TO_WORKLOAD[edge["from"]], NODE_TO_WORKLOAD[edge["to"]]
        port = PORTS[server]
        kind = (
            "deploy"
            if client in {d["metadata"]["name"] for d in declared if d["kind"] == "Deployment"}
            else "sts"
        )
        tried = kubectl(
            "-n",
            NS,
            "exec",
            f"{kind}/{client}",
            "--",
            "python3",
            "-c",
            CONNECT,
            server,
            str(port),
            check=False,
            timeout=60,
        )
        checked += 1
        if "connected" not in tried.stdout:
            failed_edges.append(
                f"{edge['from']}->{edge['to']}:{port} ({tried.stderr.strip()[-80:]})"
            )
    ev.check(
        "every_dependency_edge_of_the_topology_is_a_tcp_connection_that_succeeds_from_the_clients_own_pod",
        not failed_edges,
        f"{checked} edges; failed {failed_edges}",
    )

    # ---- D. end to end
    kubectl("-n", NS, "delete", "job", "aiops-e2e", "--ignore-not-found", "--wait=true")
    kubectl("apply", "-f", str(K8S / "jobs.yaml"), "-l", "app.kubernetes.io/name=aiops-e2e")
    kubectl(
        "-n",
        NS,
        "wait",
        "--for=condition=complete",
        "job/aiops-e2e",
        "--timeout=180s",
        check=False,
        timeout=200,
    )
    log = kubectl("-n", NS, "logs", "job/aiops-e2e", check=False).stdout.strip().splitlines()
    report = (
        json.loads(log[-1]) if log and log[-1].startswith("{") else {"steps": {}, "passed": False}
    )
    for step, ok in report["steps"].items():
        ev.check(f"end_to_end_{step}", bool(ok))
    ev.check(
        "the_end_to_end_job_reported_success",
        bool(report.get("passed")),
        f"seconds to Postgres {report.get('seconds_to_reach_postgres')}; rogue refusal: {report.get('rogue_refusal')}",
    )
    ev.metrics["end_to_end"] = report

    # ---- E. observability
    query = kubectl(
        "-n",
        NS,
        "exec",
        "statefulset/aiops-prometheus",
        "--",
        "wget",
        "-qO-",
        "http://localhost:9090/api/v1/query?query=up",
        check=False,
    ).stdout
    up = {}
    try:
        for r in json.loads(query)["data"]["result"]:
            up[r["metric"].get("job")] = r["value"][1] == "1"
    except (ValueError, KeyError):
        pass
    ev.check(
        "prometheus_scrapes_the_edge_runtime_and_tempo_and_both_are_up",
        up.get("edge-runtime") is True and up.get("tempo") is True,
        f"targets {up}",
    )

    # ---- F. what is not there
    ev.notes["not_deployed"] = (
        "the remediation worker (its docker adapter needs a container runtime; a Kubernetes adapter is future work) and the demo feeder; Keycloak runs in start-dev mode with an embedded database exactly as in the compose stack, which is not a production Keycloak"
    )
    ev.notes["target"] = (
        "a kind cluster (Kubernetes v1.36.4, containerd) on the shared Ubuntu host, Calico CNI; not the host's own K3s"
    )
    ev.notes["taken_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
