#!/usr/bin/env python3
"""P09.09 on the TARGET: Falco, running as a DaemonSet in the target cluster, detects real actions inside the platform's pods and names the pod.

    KUBECONFIG=... python3 verify_falco_target.py        # ON the target host; writes docs/evidence/p09_09_falco_target.json

`falco-daemonset.yaml` runs Falco 0.44.1 (modern eBPF) inside the node with the node's containerd socket mounted, so every process resolves to its
Kubernetes namespace and pod. It loads its default rules and `aiops_rules.yaml` with `rule_matching=all`. Every action below is performed with
`kubectl exec` inside a platform pod, and the alert must name that pod:

  fires     an interactive shell in a hardened pod; a read of the gateway's client key by a program that has no business with it; a write to a `.onnx`
            file (the model registry); an installer run inside a platform pod; a downloader run in a pod that has one; the edge runtime reaching for a
            public address (the pod's NetworkPolicy drops it, but the attempt is the event).
  prevented a write below /usr/local/bin and a read of /etc/shadow by the service user are REFUSED by the pods' own hardening (read-only root file system,
            non-root user), so no event is left to detect: the check is that the action fails.
  quiet     benign actions and the platform's own steady state raise no platform rule.

Not proven: the actions are performed by this script with kubectl exec, not by an adversary; alert routing to an operator is not built; Falco is
privileged, in its own namespace, which is the one place in the deployment that Pod Security does not hold to `restricted`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.evidence import Evidence  # noqa: E402

HERE = Path(__file__).resolve().parent
NS = "aiops"
FALCO_NS = "aiops-falco"
ev = Evidence("P09.09", "p09_09_falco_target", docs_name="p09_09_falco_target")
RULES = {
    "shell": ("AIOps shell in a platform container", "aiops-api"),
    "credential": ("AIOps sensitive file read by an unexpected program", "aiops-mqtt-gateway"),
    "registry": ("AIOps model registry written at run time", "aiops-api"),
    "installer": ("AIOps package manager in a platform container", "aiops-api"),
    "downloader": ("AIOps package manager in a platform container", "aiops-frontend"),
    "egress": ("AIOps edge runtime connecting outside the platform network", "aiops-edge-runtime"),
}


def sh(cmd: list[str], timeout: int = 120, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=False, input=stdin
    )


def exec_in(target: str, *args: str) -> subprocess.CompletedProcess:
    return sh(["kubectl", "-n", NS, "exec", target, "--", *args], timeout=90)


def exec_tty(target: str, command: str) -> subprocess.CompletedProcess:
    """A real pseudo-terminal (`script` provides one): kubectl only asks the runtime for a TTY when its own stdin is one."""
    return sh(
        ["script", "-qc", f"kubectl -n {NS} exec -it {target} -- {command}", "/dev/null"],
        timeout=90,
    )


def main() -> int:  # noqa: PLR0915
    sh(["kubectl", "apply", "-f", str(HERE / "falco-daemonset.yaml")])
    config = sh(
        [
            "kubectl",
            "-n",
            FALCO_NS,
            "create",
            "configmap",
            "aiops-falco-rules",
            f"--from-file=aiops_rules.yaml={HERE / 'aiops_rules.yaml'}",
            "--dry-run=client",
            "-o",
            "yaml",
        ]
    )
    sh(["kubectl", "apply", "-f", "-"], stdin=config.stdout)
    sh(["kubectl", "-n", FALCO_NS, "rollout", "restart", "ds/aiops-falco"])
    ready = sh(
        ["kubectl", "-n", FALCO_NS, "rollout", "status", "ds/aiops-falco", "--timeout=240s"],
        timeout=270,
    )
    time.sleep(20)
    startup = sh(["kubectl", "-n", FALCO_NS, "logs", "ds/aiops-falco"]).stdout
    ev.check(
        "Falco_runs_in_the_cluster_loads_its_default_rules_and_the_platform_rules_and_opens_the_modern_eBPF_probe",
        ready.returncode == 0
        and "aiops_rules.yaml | schema validation: ok" in startup
        and "modern BPF probe" in startup,
        "; ".join(
            ln.split(": ", 1)[-1] for ln in startup.splitlines() if "schema validation" in ln
        )[:160],
    )
    pods = {
        name: sh(
            [
                "kubectl",
                "-n",
                NS,
                "get",
                "pods",
                "-l",
                f"app.kubernetes.io/name={name}",
                "-o",
                "jsonpath={.items[0].metadata.name}",
            ]
        ).stdout
        for name in {"aiops-api", "aiops-frontend", "aiops-mqtt-gateway", "aiops-edge-runtime"}
    }
    began = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # quiet window: benign actions only, then the platform left alone
    exec_in("deploy/aiops-api", "ls", "/srv/repo/source-code")
    exec_in("deploy/aiops-api", "python3", "-c", "import os; print(len(os.environ))")
    time.sleep(45)
    quiet_end = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # the actions that must fire
    shell = exec_tty("deploy/aiops-api", "sh -c true")
    key = exec_in("sts/aiops-mqtt-gateway", "cat", "/certs/tls.key")
    exec_in("deploy/aiops-api", "python3", "-c", "open('/tmp/model.onnx', 'w').write('x')")
    exec_in("deploy/aiops-api", "pip", "--version")
    exec_in(
        "deploy/aiops-frontend", "wget", "-q", "-T", "1", "-O", "/dev/null", "http://192.0.2.1/"
    )
    exec_in(
        "deploy/aiops-edge-runtime",
        "python3",
        "-c",
        "import socket\ntry:\n socket.create_connection(('1.1.1.1', 443), timeout=3)\nexcept OSError:\n pass",
    )
    # the actions the pod's own hardening must refuse
    bin_write = exec_in("deploy/aiops-api", "sh", "-c", "echo x > /usr/local/bin/aiops-evil")
    shadow = exec_in("deploy/aiops-api", "cat", "/etc/shadow")
    time.sleep(12)
    logs = sh(
        ["kubectl", "-n", FALCO_NS, "logs", "ds/aiops-falco", f"--since-time={began}"], timeout=90
    ).stdout
    alerts = []
    for line in logs.splitlines():
        if line.startswith("{") and '"rule"' in line:
            try:
                alerts.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    mine = [
        a
        for a in alerts
        if a["rule"].startswith("AIOps") and a.get("output_fields", {}).get("k8s.ns.name") == NS
    ]

    def fired(rule: str, workload: str) -> list[dict]:
        return [
            a
            for a in mine
            if a["rule"] == rule
            and str(a["output_fields"].get("k8s.pod.name", "")).startswith(workload)
        ]

    ev.check(
        "the_key_read_actually_returned_the_key_so_the_alert_is_about_a_real_read",
        key.returncode == 0 and "PRIVATE KEY" in key.stdout,
    )
    ev.check(
        "the_interactive_shell_really_had_a_terminal",
        shell.returncode == 0,
        (shell.stdout + shell.stderr).strip()[-80:],
    )
    for name, (rule, workload) in RULES.items():
        hits = fired(rule, workload)
        ev.check(
            f"rule_fires_{name}_and_names_the_pod_{workload}",
            bool(hits),
            f"{len(hits)} alert(s); e.g. {hits[0]['output'][:170] if hits else 'none'}",
        )
    # A service user usually has no name in its container's /etc/passwd, and Falco prints "<NA>" for it: the numeric uid is what names the user, so it
    # is required to be a real number, and "<NA>" does not count as a name for anything else.
    named = all(
        str(a["output_fields"].get(f)) not in ("", "None", "<NA>")
        for a in mine
        for f in ("k8s.pod.name", "container.name", "proc.name")
    ) and all(str(a["output_fields"].get("user.uid")).isdigit() for a in mine)
    ev.check(
        "every_platform_alert_names_the_pod_the_container_the_process_and_the_numeric_uid",
        bool(mine) and named,
        f"{len(mine)} platform alerts",
    )
    ev.check(
        "a_write_below_a_binary_directory_is_refused_by_the_pods_read_only_root_file_system",
        bin_write.returncode != 0 and "Read-only" in (bin_write.stderr + bin_write.stdout),
        (bin_write.stderr + bin_write.stdout).strip()[-90:],
    )
    ev.check(
        "the_service_user_cannot_read_etc_shadow",
        shadow.returncode != 0,
        (shadow.stderr + shadow.stdout).strip()[-90:],
    )
    early = [a for a in mine if a["output"][:19] + "Z" < quiet_end]
    ev.check(
        "benign_actions_and_the_platforms_steady_state_raise_none_of_the_platform_rules",
        not early,
        f"{len(early)} platform alert(s) before the first hostile action: {[(a['rule'], a['output_fields'].get('k8s.pod.name'), a['output_fields'].get('proc.name')) for a in early][:3]}",
    )
    ev.metrics = {
        "alerts_by_rule_and_pod": {
            f"{a['rule']} | {a['output_fields'].get('k8s.pod.name')}": sum(
                1
                for b in mine
                if b["rule"] == a["rule"]
                and b["output_fields"].get("k8s.pod.name") == a["output_fields"].get("k8s.pod.name")
            )
            for a in mine
        },
        "sample_alerts": [a["output"][:230] for a in mine[:8]],
        "pods": pods,
        "falco_image": "falcosecurity/falco:0.44.1",
        "kernel": sh(["uname", "-r"]).stdout.strip(),
    }
    ev.notes["not_proven"] = (
        "the actions are performed by this script with kubectl exec, not by an adversary; alert routing to an operator is not built; Falco is privileged, in its own namespace (the one place Pod Security is not `restricted`)"
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
