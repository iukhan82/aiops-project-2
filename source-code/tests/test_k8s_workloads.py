"""P11.02: the workload checks catch what they claim to catch (each rule broken on a copy of the real manifests must be found)."""

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

K8S = Path(__file__).resolve().parents[1] / "infra" / "k8s"
sys.path.insert(0, str(K8S))

import generate_workloads as gen  # noqa: E402
import yaml  # noqa: E402

spec = importlib.util.spec_from_file_location("verify_workloads", K8S / "verify_workloads.py")
vw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vw)

WORKLOADS = [
    d
    for path in (gen.OUTPUT, gen.JOBS)
    for d in yaml.safe_load_all(path.read_text(encoding="utf-8"))
    if d
]
PODS = [d for d in WORKLOADS if d["kind"] in vw.POD_KINDS]
SERVICES = [d for d in WORKLOADS if d["kind"] == "Service"]
THIRD_PARTY = set(gen.compose_images().values())


def mutated(mutate):
    pods = copy.deepcopy(PODS)
    mutate(vw.pod_spec(pods[0]))
    return pods


def container(spec):
    return spec["containers"][0]


def test_the_real_manifests_have_no_violation_and_no_problem():
    assert vw.restricted_violations(PODS) == []
    assert (
        vw.hardening_problems(
            PODS,
            SERVICES,
            THIRD_PARTY,
            {"cpu_request_m": 0, "cpu_limit_m": 0, "memory_request_mi": 0, "memory_limit_mi": 0},
        )
        == []
    )


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("host network", lambda s: s.update(hostNetwork=True)),
        (
            "host path volume",
            lambda s: s.setdefault("volumes", []).append({"name": "h", "hostPath": {"path": "/"}}),
        ),
        ("no seccomp", lambda s: s["securityContext"].pop("seccompProfile")),
        (
            "privilege escalation",
            lambda s: container(s)["securityContext"].update(allowPrivilegeEscalation=True),
        ),
        ("runs as root", lambda s: s["securityContext"].update(runAsUser=0)),
        ("not non-root", lambda s: s["securityContext"].pop("runAsNonRoot")),
        (
            "capabilities kept",
            lambda s: container(s)["securityContext"].update(capabilities={"drop": ["NET_RAW"]}),
        ),
        (
            "capability added",
            lambda s: container(s)["securityContext"].update(
                capabilities={"drop": ["ALL"], "add": ["SYS_ADMIN"]}
            ),
        ),
        ("privileged", lambda s: container(s)["securityContext"].update(privileged=True)),
        (
            "host port",
            lambda s: (
                container(s).setdefault("ports", []).append({"containerPort": 1, "hostPort": 1})
            ),
        ),
    ],
)
def test_each_restricted_profile_rule_is_caught_when_broken(label, mutate):
    assert vw.restricted_violations(mutated(mutate)), label


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        (
            "writable root",
            lambda s: container(s)["securityContext"].update(readOnlyRootFilesystem=False),
        ),
        ("service account token", lambda s: s.update(automountServiceAccountToken=True)),
        ("no limits", lambda s: container(s).pop("resources")),
        ("latest tag", lambda s: container(s).update(image="aiops-backend:latest")),
        ("third-party not pinned", lambda s: container(s).update(image="postgis/postgis:16-3.4")),
        (
            "literal password",
            lambda s: (
                container(s)
                .setdefault("env", [])
                .append({"name": "POSTGRES_PASSWORD", "value": "hunter2"})
            ),
        ),
        (
            "writable mount that is not scratch",
            lambda s: (
                container(s)
                .setdefault("volumeMounts", [])
                .append({"name": "config", "mountPath": "/etc/x"})
            ),
        ),
    ],
)
def test_each_least_privilege_rule_is_caught_when_broken(label, mutate):
    pods = copy.deepcopy(PODS)
    api = next(
        p for p in pods if p["metadata"]["name"] == "aiops-postgres"
    )  # a third-party image, so pinning applies
    mutate(vw.pod_spec(api))
    assert vw.hardening_problems(
        pods,
        SERVICES,
        THIRD_PARTY,
        {"cpu_request_m": 0, "cpu_limit_m": 0, "memory_request_mi": 0, "memory_limit_mi": 0},
    ), label


def test_a_service_without_a_readiness_probe_is_caught():
    pods = copy.deepcopy(PODS)
    api = next(p for p in pods if p["metadata"]["name"] == "aiops-api")
    vw.containers(api)[0].pop("readinessProbe")
    problems = vw.hardening_problems(
        pods,
        SERVICES,
        THIRD_PARTY,
        {"cpu_request_m": 0, "cpu_limit_m": 0, "memory_request_mi": 0, "memory_limit_mi": 0},
    )
    assert any("readiness" in p for p in problems)


def test_the_generated_files_are_current():
    assert gen.OUTPUT.read_text(encoding="utf-8").replace("\r\n", "\n") == gen.build()
    assert gen.JOBS.read_text(encoding="utf-8").replace("\r\n", "\n") == gen.build(jobs=True)
    assert gen.POLICIES.read_text(encoding="utf-8").replace("\r\n", "\n") == gen.rbac_and_policies()


def test_every_dependency_is_a_declared_workload_and_the_database_list_is_the_documented_one():
    names = {w.name for w in gen.specs()}
    for client, servers in gen.DEPENDENCIES.items():
        assert client in names
        assert {s for s, _ in servers} <= names
    reaching_db = {
        c for c, deps in gen.DEPENDENCIES.items() if any(s == "aiops-postgres" for s, _ in deps)
    }
    assert reaching_db == {
        "aiops-api", "aiops-scenario-control", "aiops-command-executor", "aiops-outcome-verifier",
        "aiops-ingestion-gateway", "aiops-platform-correlator", "aiops-platform-probe",
        "aiops-migrate", "aiops-e2e", "aiops-security", "aiops-load",
    }  # fmt: skip


def test_the_cluster_broker_keeps_an_offline_queue_for_the_gateways_persistent_session():
    """Measured on the target: with `per_listener_settings true` mosquitto 2.0.18 queues nothing for a persistent subscriber that is away, so the
    gateway lost everything published during its own restart. The cluster's broker has one listener and must not use the option, and must raise the queue
    limits the persistent session relies on."""
    conf = (K8S / "target" / "mosquitto.conf").read_text(encoding="utf-8")
    directives = [
        line.split()[0]
        for line in conf.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert "per_listener_settings" not in directives
    assert directives.count("listener") == 1
    assert "max_queued_messages" in directives
    assert "max_inflight_messages" in directives


def test_a_failed_check_job_is_not_retried_but_the_migration_is():
    jobs = {d["metadata"]["name"]: d for d in WORKLOADS if d["kind"] == "Job"}
    assert jobs["aiops-migrate"]["spec"]["backoffLimit"] == 2
    assert all(j["spec"]["backoffLimit"] == 0 for n, j in jobs.items() if n != "aiops-migrate")
