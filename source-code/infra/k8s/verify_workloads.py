"""P11.02 acceptance evidence: the Kubernetes workloads are valid, least-privilege and consistent with the platform they describe.

    python source-code/infra/k8s/verify_workloads.py

Nothing here talks to a cluster. Every check is against the generated files, the compose stack and the topology, so it runs anywhere:

A. Valid Kubernetes. Every document is validated against the OpenAPI schema of Kubernetes 1.36 (the K3s minor version of the
   target host) with `kubernetes-validate`, strictly: an unknown or misspelled field is an error, not a warning.
B. The Pod Security `restricted` profile, rule by rule (non-root, no privilege escalation, all capabilities dropped, RuntimeDefault seccomp,
   no host namespaces, no host ports, no host paths, only the volume types the profile allows), applied to every pod template - and the
   namespace enforces the same profile, so an admission controller would refuse a violation the check missed.
C. Least privilege beyond the profile: a read-only root file system, no ServiceAccount token, resource requests and limits on every
   container (and the totals reported), a health probe on every service, no `latest` tag, every third-party image pinned by digest and
   equal to the one the compose stack runs, no literal secret (a password-like variable is a Secret reference), only writable paths
   that are emptyDirs or claims.
D. Consistency. Every workload has its ServiceAccount and its own NetworkPolicy; every Service selects a real workload and targets a
   port its container declares; every NetworkPolicy peer is a real workload; and the network policy is CHECKED AGAINST THE PLATFORM'S OWN
   DEPENDENCY GRAPH (`backend/topology.json`, itself checked against the source by `verify_slos.py`): every edge between workloads that
   exists in both has an egress rule on the client and an ingress rule on the server for the same port.
E. The generated files are exactly what `generate_workloads.py` produces (no hand edit survives).

NOT proven here, stated in the evidence: nothing was admitted by a real API server and no traffic was sent through a real CNI - that is
P11.03 on the target host.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
SOURCE_ROOT = HERE.parents[1]
sys.path.insert(0, str(SOURCE_ROOT))
sys.path.insert(0, str(HERE))

import generate_workloads as gen  # noqa: E402
import kubernetes_validate  # noqa: E402
from backend.evidence import Evidence  # noqa: E402

ev = Evidence("P11.02", "p11_02_workloads", docs_name="p11_02_workloads")
K8S_VERSION = "1.36"
TOPOLOGY = SOURCE_ROOT / "backend" / "topology.json"
ALLOWED_VOLUMES = {
    "configMap",
    "csi",
    "downwardAPI",
    "emptyDir",
    "ephemeral",
    "persistentVolumeClaim",
    "projected",
    "secret",
}
POD_KINDS = {"Deployment", "StatefulSet", "Job"}
SECRETISH = re.compile(r"(PASSWORD|SECRET|TOKEN|PRIVATE|_KEY$|APIKEY)", re.I)
# topology.json node -> the workload that runs it
TOPOLOGY_TO_WORKLOAD = {
    "api": "aiops-api",
    "ingestion": "aiops-ingestion-gateway",
    "gateway": "aiops-mqtt-gateway",
    "edge-runtime": "aiops-edge-runtime",
    "scenario-control": "aiops-scenario-control",
    "postgres": "aiops-postgres",
    "opa": "aiops-opa",
    "keycloak": "aiops-keycloak",
    "kafka": "aiops-kafka-broker",
    "mosquitto": "aiops-mqtt-broker",
}
TOPOLOGY_PORTS = {
    "aiops-postgres": 5432,
    "aiops-opa": 8181,
    "aiops-keycloak": 8080,
    "aiops-kafka-broker": 9092,
    "aiops-mqtt-broker": 8883,
}


def load(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]


def pod_spec(doc: dict) -> dict:
    return doc["spec"]["template"]["spec"]


def containers(doc: dict) -> list[dict]:
    return pod_spec(doc)["containers"]


def restricted_violations(pods: list[dict]) -> list[str]:
    """The Pod Security `restricted` rules, applied to every pod template."""
    violations: list[str] = []
    for d in pods:
        spec, name = pod_spec(d), d["metadata"]["name"]
        pod_sc = spec.get("securityContext", {})
        for flag in ("hostNetwork", "hostPID", "hostIPC"):
            if spec.get(flag):
                violations.append(f"{name}: {flag}")
        for v in spec.get("volumes", []):
            kind = next(k for k in v if k != "name")
            if kind not in ALLOWED_VOLUMES:
                violations.append(f"{name}: volume type {kind}")
        if pod_sc.get("seccompProfile", {}).get("type") not in ("RuntimeDefault", "Localhost"):
            violations.append(f"{name}: seccomp")
        for c in spec["containers"]:
            sc, cname = c.get("securityContext", {}), f"{name}/{c['name']}"
            if sc.get("allowPrivilegeEscalation") is not False:
                violations.append(f"{cname}: allowPrivilegeEscalation")
            if not (sc.get("runAsNonRoot") or pod_sc.get("runAsNonRoot")):
                violations.append(f"{cname}: runAsNonRoot")
            if 0 in (sc.get("runAsUser"), pod_sc.get("runAsUser")):
                violations.append(f"{cname}: runs as uid 0")
            caps = sc.get("capabilities", {})
            if caps.get("drop") != ["ALL"] or set(caps.get("add", [])) - {"NET_BIND_SERVICE"}:
                violations.append(f"{cname}: capabilities")
            if sc.get("privileged"):
                violations.append(f"{cname}: privileged")
            if any("hostPort" in p for p in c.get("ports", [])):
                violations.append(f"{cname}: hostPort")
    return violations


def hardening_problems(
    pods: list[dict], services: list[dict], third_party: set[str], totals: dict
) -> list[str]:
    """Least privilege beyond the profile; adds resource totals into `totals`."""
    problems: list[str] = []
    problems: list[str] = []
    for d in pods:
        spec, name = pod_spec(d), d["metadata"]["name"]
        if spec.get("automountServiceAccountToken") is not False:
            problems.append(f"{name}: mounts a service account token")
        writable = {v["name"] for v in spec.get("volumes", []) if "emptyDir" in v} | {
            vct["metadata"]["name"] for vct in d["spec"].get("volumeClaimTemplates", [])
        }
        for c in spec["containers"]:
            cname = f"{name}/{c['name']}"
            if c["securityContext"].get("readOnlyRootFilesystem") is not True:
                problems.append(f"{cname}: writable root file system")
            res = c.get("resources", {})
            if not (
                res.get("requests", {}).get("cpu")
                and res.get("requests", {}).get("memory")
                and res.get("limits", {}).get("cpu")
                and res.get("limits", {}).get("memory")
            ):
                problems.append(f"{cname}: resources")
            else:
                totals["cpu_request_m"] += int(res["requests"]["cpu"].removesuffix("m"))
                totals["cpu_limit_m"] += int(res["limits"]["cpu"].removesuffix("m"))
                for k, key in (("memory_request_mi", "requests"), ("memory_limit_mi", "limits")):
                    value = res[key]["memory"]
                    totals[k] += (
                        int(value.removesuffix("Mi"))
                        if value.endswith("Mi")
                        else int(value.removesuffix("Gi")) * 1024
                    )
            image = c["image"]
            if image.endswith(":latest") or (":" not in image and "@" not in image):
                problems.append(f"{cname}: unpinned image {image}")
            if not image.startswith("aiops-") and "@sha256:" not in image:
                problems.append(f"{cname}: third-party image not pinned by digest")
            if not image.startswith("aiops-") and image not in third_party:
                problems.append(f"{cname}: image differs from the compose stack")
            if "@" not in image and not image.startswith("aiops-"):
                problems.append(f"{cname}: image without digest")
            for env in c.get("env", []):
                if (
                    SECRETISH.search(env["name"])
                    and "value" in env
                    and env["value"] not in ("false",)
                    and not env["name"].endswith(("_USER", "_USERNAME"))
                ):
                    problems.append(f"{cname}: {env['name']} carries a literal value")
            for m in c.get("volumeMounts", []):
                if not m.get("readOnly") and m["name"] not in writable:
                    problems.append(
                        f"{cname}: writable mount {m['mountPath']} is not an emptyDir or a claim"
                    )
            if not c.get("readinessProbe") and any(
                s["spec"]["selector"]["app.kubernetes.io/name"] == name for s in services
            ):
                problems.append(f"{cname}: a service without a readiness probe")
    return problems


def main() -> int:  # noqa: PLR0915
    workload_docs = load(gen.OUTPUT) + load(gen.JOBS)
    policy_docs = load(gen.POLICIES)
    docs = workload_docs + policy_docs
    pods = [d for d in workload_docs if d["kind"] in POD_KINDS]
    services = [d for d in workload_docs if d["kind"] == "Service"]
    accounts = {d["metadata"]["name"] for d in policy_docs if d["kind"] == "ServiceAccount"}
    policies = {d["metadata"]["name"]: d for d in policy_docs if d["kind"] == "NetworkPolicy"}
    namespace = next(d for d in policy_docs if d["kind"] == "Namespace")
    names = {d["metadata"]["name"] for d in pods}

    # ---- A. valid Kubernetes
    errors = []
    for d in docs:
        try:
            kubernetes_validate.validate(d, K8S_VERSION, strict=True)
        except Exception as exc:  # noqa: BLE001 - every validation error type
            errors.append(f"{d['kind']}/{d['metadata']['name']}: {str(exc).splitlines()[0][:160]}")
    ev.check(
        "every_document_validates_strictly_against_the_kubernetes_1_36_openapi_schema",
        not errors,
        f"{len(docs)} documents; {errors[:3]}",
    )

    # ---- B. Pod Security restricted
    labels = namespace["metadata"]["labels"]
    ev.check(
        "the_namespace_enforces_pod_security_restricted_so_an_admission_controller_refuses_what_this_check_missed",
        all(
            labels.get(f"pod-security.kubernetes.io/{m}") == "restricted"
            for m in ("enforce", "audit", "warn")
        ),
    )
    violations = restricted_violations(pods)
    ev.check(
        "every_pod_template_meets_the_pod_security_restricted_profile_rule_by_rule",
        not violations,
        f"{len(pods)} workloads; {violations[:5]}",
    )

    # ---- C. least privilege beyond the profile
    totals = {"cpu_request_m": 0, "cpu_limit_m": 0, "memory_request_mi": 0, "memory_limit_mi": 0}
    compose = gen.compose_images()
    third_party = set(compose.values())
    problems = hardening_problems(pods, services, third_party, totals)
    ev.check(
        "read_only_root_filesystems_no_service_account_tokens_resource_limits_pinned_images_no_literal_secrets_and_only_emptydir_or_claim_writes",
        not problems,
        str(problems[:5]),
    )
    ev.check(
        "the_platform_images_are_versioned_and_every_third_party_image_is_the_digest_pinned_one_the_compose_stack_runs",
        all(
            c["image"] in third_party
            or c["image"].startswith(("aiops-backend:", "aiops-frontend:", "aiops-edge:"))
            for d in pods
            for c in containers(d)
        ),
    )
    ev.metrics["resource_totals"] = totals
    ev.metrics["workloads"] = {
        d["metadata"]["name"]: {
            "kind": d["kind"],
            "uid": pod_spec(d)["securityContext"]["runAsUser"],
            "image": containers(d)[0]["image"],
        }
        for d in pods
    }

    # ---- D. consistency
    ev.check(
        "every_workload_has_its_own_service_account_and_its_own_network_policy",
        names <= accounts and names <= set(policies),
        f"missing: {sorted(names - accounts - set(policies))}",
    )
    ev.check(
        "every_service_account_belongs_to_a_workload_and_every_named_network_policy_to_a_workload",
        accounts <= names and set(policies) - {"default-deny-all", "allow-dns-egress"} <= names,
    )
    bad_services = []
    by_name = {d["metadata"]["name"]: d for d in pods}
    for s in services:
        target = s["spec"]["selector"]["app.kubernetes.io/name"]
        if target not in by_name:
            bad_services.append(f"{s['metadata']['name']}: no workload {target}")
            continue
        declared = {
            p["containerPort"] for c in containers(by_name[target]) for p in c.get("ports", [])
        }
        for p in s["spec"]["ports"]:
            if p["targetPort"] not in declared:
                bad_services.append(f"{s['metadata']['name']}: port {p['targetPort']}")
    ev.check(
        "every_service_selects_a_real_workload_and_targets_a_port_its_container_declares",
        not bad_services,
        str(bad_services),
    )

    def rules(policy: dict, direction: str) -> list[tuple[str | None, set[int]]]:
        out = []
        for rule in policy["spec"].get(direction, []):
            ports = {p["port"] for p in rule.get("ports", [])}
            peers = rule.get("from", []) + rule.get("to", [])
            if not peers:
                out.append((None, ports))
            for peer in peers:
                selector = peer.get("podSelector", {}).get("matchLabels", {})
                out.append((selector.get("app.kubernetes.io/name"), ports))
        return out

    unknown_peers = sorted(
        {
            peer
            for p in policies.values()
            for direction in ("ingress", "egress")
            for peer, _ in rules(p, direction)
            if peer and peer not in names
        }
    )
    ev.check("every_network_policy_peer_is_a_real_workload", not unknown_peers, str(unknown_peers))
    egress_without_ingress = []
    for client, p in policies.items():
        for server, ports in rules(p, "egress"):
            if server is None or server not in policies:
                continue
            accepted = {
                (peer, port)
                for peer, ps in rules(policies[server], "ingress")
                for port in ps
                if peer in (client, None)
            }
            egress_without_ingress += [
                f"{client}->{server}:{port}"
                for port in ports
                if (client, port) not in accepted and (None, port) not in accepted
            ]
    ev.check(
        "every_egress_rule_has_the_matching_ingress_rule_on_the_server_for_the_same_port",
        not egress_without_ingress,
        str(egress_without_ingress),
    )

    topology = json.loads(TOPOLOGY.read_text(encoding="utf-8"))
    missing_edges, checked = [], 0
    for edge in topology["edges"]:
        client, server = (
            TOPOLOGY_TO_WORKLOAD.get(edge["from"]),
            TOPOLOGY_TO_WORKLOAD.get(edge["to"]),
        )
        if not client or not server or client == server:
            continue
        checked += 1
        port = TOPOLOGY_PORTS[server]
        if not any(
            peer == server and port in ports for peer, ports in rules(policies[client], "egress")
        ):
            missing_edges.append(f"{edge['from']}->{edge['to']}")
    ev.check(
        "every_dependency_in_the_platforms_own_topology_graph_between_two_workloads_is_allowed_by_the_network_policy",
        not missing_edges and checked >= 8,
        f"{checked} topology edges checked; missing {missing_edges}",
    )
    extra = []
    allowed_pairs = {(c, s) for c, deps in gen.DEPENDENCIES.items() for s, _ in deps}
    for client, p in policies.items():
        for server, _ in rules(p, "egress"):
            if server and (client, server) not in allowed_pairs:
                extra.append(f"{client}->{server}")
    ev.check(
        "the_network_policy_allows_no_workload_to_workload_path_the_dependency_specification_does_not_list",
        not extra,
        str(extra),
    )
    reaches_db = sorted(
        c for c, p in policies.items() if any(s == "aiops-postgres" for s, _ in rules(p, "egress"))
    )
    ev.metrics["workloads_that_can_reach_the_database"] = reaches_db
    ev.check(
        "the_database_is_reachable_only_by_the_services_that_hold_a_database_identity",
        set(reaches_db)
        == {
            "aiops-api",
            "aiops-scenario-control",
            "aiops-command-executor",
            "aiops-outcome-verifier",
            "aiops-ingestion-gateway",
            "aiops-platform-correlator",
            "aiops-platform-probe",
            "aiops-migrate",
            "aiops-e2e",
            "aiops-security",
            "aiops-load",
        },
        str(reaches_db),
    )

    # ---- E. generated, not hand-written
    ev.check(
        "the_files_on_disk_are_exactly_what_the_generator_produces",
        gen.JOBS.read_text(encoding="utf-8").replace("\r\n", "\n") == gen.build(jobs=True)
        and gen.OUTPUT.read_text(encoding="utf-8").replace("\r\n", "\n") == gen.build()
        and gen.POLICIES.read_text(encoding="utf-8").replace("\r\n", "\n")
        == gen.rbac_and_policies(),
    )
    ev.notes["not_proven"] = (
        "This file proves the manifests, not the cluster: what a real API server admitted and a real CNI enforced is P11.03 and P11.04 on the target "
        "(infra/k8s/target/verify_target.py and verify_security_target.py). Keycloak runs in start-dev mode (embedded database) exactly as in the compose stack, which is not a production Keycloak; "
        "the remediation worker (its docker adapter needs a container runtime) and the demo feeder are not part of these workloads."
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
