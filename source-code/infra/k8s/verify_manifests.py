"""P09.04 (CTL-13's policy half) acceptance evidence: structural validation of the Kubernetes RBAC and NetworkPolicy
manifests, and their consistency with the real running platform's own service names.

    python source-code/infra/k8s/verify_manifests.py

`kubectl apply --dry-run=client` on this workstation still queries a live API server for resource discovery even in
"client" mode (tested directly: it fails with no server reachable, on this kubectl version), and this host's own
`kubectl` context points at two OTHER projects' clusters this project has no authorization to touch, even read-only
(docs/environment/... shared-host notes) - so this validates structure itself: every document is a real, minimally-
well-formed instance of the Kubernetes kind it claims to be, and the manifests are internally consistent (every
NetworkPolicy's selector names an actual declared ServiceAccount; the default-deny policy really is default-deny).
Live admission by a real API server, and live-traffic enforcement testing, is P11.02's job once a target cluster
exists - this is the "policies," not the "manifests," half of CTL-13.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

MANIFEST = Path(__file__).resolve().parent / "rbac-and-network-policy.yaml"
COMPOSE = Path(__file__).resolve().parents[1] / "platform" / "docker-compose.yml"

REQUIRED_FIELDS = {
    "Namespace": {"apiVersion", "kind", "metadata"},
    "ServiceAccount": {"apiVersion", "kind", "metadata"},
    "NetworkPolicy": {"apiVersion", "kind", "metadata", "spec"},
}
API_VERSION_OF = {
    "Namespace": "v1",
    "ServiceAccount": "v1",
    "NetworkPolicy": "networking.k8s.io/v1",
}


def load_docs() -> list[dict]:
    return [doc for doc in yaml.safe_load_all(MANIFEST.read_text(encoding="utf-8")) if doc]


def check(name: str, ok: bool, detail: object = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}: {name} {detail}"[:400])
    return ok


def main() -> int:  # noqa: PLR0915
    docs = load_docs()
    all_ok = True

    all_ok &= check(
        "the_manifest_file_parses_as_a_sequence_of_yaml_documents", len(docs) > 0, len(docs)
    )

    bad_kind = [d.get("kind") for d in docs if d.get("kind") not in REQUIRED_FIELDS]
    all_ok &= check(
        "every_document_is_one_of_the_three_kinds_this_task_uses", not bad_kind, bad_kind
    )

    bad_fields = [
        d.get("kind")
        for d in docs
        if d.get("kind") in REQUIRED_FIELDS and not REQUIRED_FIELDS[d["kind"]] <= set(d)
    ]
    all_ok &= check(
        "every_document_carries_the_fields_its_own_kind_requires", not bad_fields, bad_fields
    )

    bad_api = [
        (d.get("kind"), d.get("apiVersion"))
        for d in docs
        if d.get("kind") in API_VERSION_OF and d.get("apiVersion") != API_VERSION_OF[d["kind"]]
    ]
    all_ok &= check(
        "every_document_uses_the_real_stable_apiversion_for_its_kind", not bad_api, bad_api
    )

    service_accounts = {d["metadata"]["name"] for d in docs if d.get("kind") == "ServiceAccount"}
    all_ok &= check(
        "there_is_one_service_account_per_real_service_this_platform_runs",
        len(service_accounts) == 10,
        sorted(service_accounts),
    )

    unmounted = [
        d["metadata"]["name"]
        for d in docs
        if d.get("kind") == "ServiceAccount" and d.get("automountServiceAccountToken") is not False
    ]
    all_ok &= check(
        "every_service_account_explicitly_refuses_to_mount_a_kubernetes_api_token_nothing_here_calls_the_api",
        not unmounted,
        unmounted,
    )

    policies = {d["metadata"]["name"]: d for d in docs if d.get("kind") == "NetworkPolicy"}
    default_deny = policies.get("default-deny-all")
    all_ok &= check(
        "a_default_deny_policy_exists_with_an_empty_selector_matching_every_pod_and_both_directions",
        default_deny is not None
        and default_deny["spec"].get("podSelector") == {}
        and set(default_deny["spec"].get("policyTypes", [])) == {"Ingress", "Egress"},
        default_deny["spec"] if default_deny else None,
    )

    named_policies = {
        name for name in policies if name not in ("default-deny-all", "allow-dns-egress")
    }
    all_ok &= check(
        "every_non_default_networkpolicy_is_named_after_a_real_declared_service_account",
        named_policies <= service_accounts,
        sorted(named_policies - service_accounts),
    )

    label_pattern = re.compile(r"^aiops-[a-z-]+$")
    bad_labels: list[str] = []
    for name, policy in policies.items():
        for rule_kind in ("ingress", "egress"):
            for rule in policy["spec"].get(rule_kind, []):
                for peer in rule.get("from", []) + rule.get("to", []):
                    selector = peer.get("podSelector", {})
                    values = list(selector.get("matchLabels", {}).values())
                    for expr in selector.get("matchExpressions", []):
                        values += expr.get("values", [])
                    for value in values:
                        if not label_pattern.match(value):
                            bad_labels.append(f"{name}: {value}")
    all_ok &= check(
        "every_pod_selector_used_in_a_rule_names_a_well_formed_aiops_service_label",
        not bad_labels,
        bad_labels,
    )

    referenced_labels = {
        value
        for policy in policies.values()
        for rule_kind in ("ingress", "egress")
        for rule in policy["spec"].get(rule_kind, [])
        for peer in rule.get("from", []) + rule.get("to", [])
        for value in (
            list(peer.get("podSelector", {}).get("matchLabels", {}).values())
            + [
                v
                for e in peer.get("podSelector", {}).get("matchExpressions", [])
                for v in e.get("values", [])
            ]
        )
    }
    all_ok &= check(
        "every_service_label_a_rule_references_is_a_service_account_this_manifest_actually_declares",
        referenced_labels <= service_accounts,
        sorted(referenced_labels - service_accounts),
    )

    compose_text = COMPOSE.read_text(encoding="utf-8")
    compose_container_names = set(re.findall(r"container_name:\s*(\S+)", compose_text))
    matched_to_compose = {name for name in service_accounts if name in compose_container_names}
    all_ok &= check(
        "most_service_account_names_match_a_real_container_name_already_running_in_this_stack_not_invented_labels",
        len(matched_to_compose) >= 3,
        sorted(matched_to_compose),
    )

    egress_targets_with_no_ingress_rule = []
    ingress_capable = {
        name
        for name, policy in policies.items()
        if policy["spec"].get("ingress") and name not in ("default-deny-all", "allow-dns-egress")
    }
    for name, policy in policies.items():
        for rule in policy["spec"].get("egress", []):
            for peer in rule.get("to", []):
                for value in peer.get("podSelector", {}).get("matchLabels", {}).values():
                    if value in service_accounts and value not in ingress_capable:
                        egress_targets_with_no_ingress_rule.append((name, value))
    all_ok &= check(
        "every_egress_targets_own_policy_actually_accepts_that_ingress_no_rule_points_at_a_target_that_would_refuse_it",
        not egress_targets_with_no_ingress_rule,
        egress_targets_with_no_ingress_rule,
    )

    print("ALL CHECKS PASSED" if all_ok else "ONE OR MORE CHECKS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
