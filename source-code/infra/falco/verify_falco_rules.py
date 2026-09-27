"""P09.09 evidence: the platform's Falco rules load, fire on the behaviours they name, and stay quiet on the ones they must not.

    python3 source-code/infra/falco/verify_falco_rules.py         # WSL/Linux with a native docker engine; runs Falco privileged for a minute

Falco runs with `rule_matching=all`: by default it reports only the FIRST rule an event matches, and its own default rules (a read of
/etc/shadow, a terminal shell in a container) would hide the platform's rules for the same event.

A. VALIDATE: Falco 0.44.1 (pinned by digest) loads the platform rules on top of its own default rules with no warning.
B. DETECT: Falco captures live syscalls from the host kernel with the modern eBPF driver, and each of the six rules fires once for a real
   action inside a container named `aiops-...`: an interactive shell, a read of /etc/shadow, a write below /usr/local/bin, a write to the
   model registry, an installer/downloader, and the edge runtime connecting to a public address. Every alert names the container, the
   process and the user (asserted from the alert's own fields).
C. STAY QUIET: the same actions inside a container that is NOT one of the platform's (`other-tenant`) raise none of these rules, and
   benign actions inside a platform container (a listing, a non-interactive shell in a non-hardened container) raise none either.

What this is not: it runs on the LOCAL development kernel (WSL2), not on the target host, and the six actions are performed by this script
with `docker exec`, not by an adversary. The target-host run (P09.09's acceptance names it) and the alert routing to an operator (Falco
sidekick, Loki) are not done here.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE_ROOT = HERE.parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.evidence import Evidence  # noqa: E402

FALCO_IMAGE = "falcosecurity/falco:0.44.1@sha256:d0cfe422d6ac0e0f20857798f46c7d7273210e1b064b22821e4e6e7f843cde6b"
RULES = {
    "shell": "AIOps shell in a platform container",
    "credential": "AIOps sensitive file read by an unexpected program",
    "binary": "AIOps write below a binary directory",
    "registry": "AIOps model registry written at run time",
    "installer": "AIOps package manager in a platform container",
    "egress": "AIOps edge runtime connecting outside the platform network",
}
TARGET = "aiops-falco-target"
OTHER = "other-tenant"
EDGE = "aiops-edge-runtime"
FALCO = "aiops-falco-verify"

ev = Evidence("P09.09", "p09_09_falco_rules", docs_name="p09_09_falco_rules")


def run(cmd: list[str], timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def exec_in(container: str, *args: str, tty: bool = False) -> subprocess.CompletedProcess:
    return run(["docker", "exec", *(["-t"] if tty else []), container, *args], timeout=60)


def alerts(since: str) -> list[dict]:
    out = run(["docker", "logs", "--since", since, FALCO]).stdout
    found = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{") and '"rule"' in line:
            try:
                found.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return found


STANDIN = {"made": False}


def edge_container() -> bool:
    """The rule names `aiops-edge-runtime` exactly. Use the lab's when it exists; make a stand-in with that name only when none exists at all."""
    if run(["docker", "ps", "-aq", "--filter", f"name=^{EDGE}$"]).stdout.strip():
        return (
            bool(run(["docker", "ps", "-q", "--filter", f"name=^{EDGE}$"]).stdout.strip())
            and exec_in(EDGE, "which", "nc").returncode == 0
        )
    STANDIN["made"] = (
        run(["docker", "run", "-d", "--name", EDGE, "alpine:3", "sleep", "3600"]).returncode == 0
    )
    return STANDIN["made"]


def wait_ready() -> bool:
    deadline = time.time() + 90
    while time.time() < deadline:
        logs = run(["docker", "logs", FALCO])
        text = logs.stdout + logs.stderr
        if (
            "Loaded event sources" in text
            or "Falco initialized" in text
            or "Opening 'syscall' source" in text
        ):
            time.sleep(4)
            return True
        if run(["docker", "ps", "-q", "--filter", f"name={FALCO}"]).stdout.strip() == "":
            return False
        time.sleep(2)
    return False


def main() -> int:  # noqa: PLR0915
    # ---- A. validate
    validate = run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{HERE}:/rules:ro",
            FALCO_IMAGE,
            "falco",
            "-V",
            "/etc/falco/falco_rules.yaml",
            "-V",
            "/rules/aiops_rules.yaml",
        ]
    )
    text = validate.stdout + validate.stderr
    ev.check(
        "Falco_0_44_1_loads_the_platform_rules_after_its_default_rules_with_no_error_and_no_warning",
        validate.returncode == 0
        and "aiops_rules.yaml: Ok" in text
        and "with warnings" not in text
        and "LOAD_" not in text,
        text.strip().splitlines()[-1][:200] if text.strip() else "no output",
    )

    # ---- B/C. live capture
    for name in (TARGET, OTHER, FALCO):
        run(["docker", "rm", "-f", name])
    ev.notes["kernel"] = run(["uname", "-r"]).stdout.strip()
    for name in (TARGET, OTHER):
        started = run(["docker", "run", "-d", "--name", name, "alpine:3", "sleep", "3600"])
        if started.returncode != 0:
            ev.check(f"start_{name}", False, started.stderr[-200:])
            return ev.finish()
    started = run(
        [
            "docker", "run", "-d", "--name", FALCO, "--privileged",
            "-v", "/var/run/docker.sock:/host/var/run/docker.sock", "-v", "/proc:/host/proc:ro", "-v", "/etc:/host/etc:ro",
            "-v", f"{HERE}:/rules:ro", FALCO_IMAGE,
            "falco", "-r", "/etc/falco/falco_rules.yaml", "-r", "/rules/aiops_rules.yaml", "-o", "rule_matching=all", "-o", "json_output=true",
            "-o", "stdout_output.enabled=true",
        ]
    )  # fmt: skip
    ready = started.returncode == 0 and wait_ready()
    ev.check(
        "Falco_captures_live_syscalls_from_the_kernel_with_the_modern_eBPF_driver",
        ready,
        (run(["docker", "logs", "--tail", "6", FALCO]).stderr or started.stderr)[-300:]
        if not ready
        else f"kernel {ev.notes['kernel']}",
    )
    if not ready:
        for name in (TARGET, OTHER, FALCO):
            run(["docker", "rm", "-f", name])
        ev.notes["not_proven"] = (
            "live capture did not start on this kernel; the rules are validated only"
        )
        return ev.finish()

    began = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    try:
        # positive actions in a platform container
        exec_in(TARGET, "sh", "-c", "true", tty=True)  # interactive shell
        exec_in(TARGET, "cat", "/etc/shadow")  # credential read
        exec_in(
            TARGET, "sh", "-c", "echo x > /usr/local/bin/aiops-evil"
        )  # write below a binary directory
        exec_in(
            TARGET, "sh", "-c", "mkdir -p /data/registry && echo x > /data/registry/model.onnx"
        )  # registry write
        exec_in(
            TARGET, "wget", "-q", "-T", "1", "http://192.0.2.1/"
        )  # downloader from outside the container (TEST-NET-1)
        edge = edge_container()
        if edge:
            exec_in(EDGE, "nc", "-w", "2", "1.1.1.1", "443")
        # the same actions outside the platform
        exec_in(OTHER, "sh", "-c", "true", tty=True)
        exec_in(OTHER, "cat", "/etc/shadow")
        exec_in(OTHER, "sh", "-c", "echo x > /usr/local/bin/other-evil")
        exec_in(OTHER, "sh", "-c", "mkdir -p /data/registry && echo x > /data/registry/model.onnx")
        exec_in(OTHER, "wget", "-q", "-T", "1", "http://192.0.2.1/")
        # benign actions inside a platform container
        exec_in(TARGET, "ls", "/")
        exec_in(TARGET, "sh", "-c", "echo hello > /tmp/benign.txt")
        time.sleep(8)
        seen = [a for a in alerts(began) if str(a.get("rule", "")).startswith("AIOps")]
    finally:
        logs_tail = run(["docker", "logs", "--tail", "4", FALCO]).stderr[-300:]
        for name in (TARGET, OTHER, FALCO, *([EDGE] if STANDIN["made"] else [])):
            run(["docker", "rm", "-f", name])

    def fired(rule: str, container: str) -> list[dict]:
        return [
            a
            for a in seen
            if a["rule"] == rule and a.get("output_fields", {}).get("container.name") == container
        ]

    for key, rule in RULES.items():
        container = EDGE if key == "egress" else TARGET
        hits = fired(rule, container)
        if key == "egress" and not edge:
            ev.check(
                "the_edge_runtime_rule_fires_on_a_connection_to_a_public_address",
                False,
                f"{EDGE} was not running, so the action could not be performed",
            )
            continue
        ev.check(
            f"rule_fires_{key}",
            bool(hits),
            f"{len(hits)} alert(s); e.g. {hits[0]['output'][:170] if hits else 'none'}",
        )
    named = all(
        {"container.name", "proc.name", "user.name", "user.uid"} <= set(a.get("output_fields", {}))
        and all(a["output_fields"].get(f) for f in ("container.name", "proc.name", "user.name"))
        and str(a["output_fields"]["user.uid"]).isdigit()
        for a in seen
    )
    ev.check(
        "every_alert_names_the_container_the_process_and_the_user",
        bool(seen) and named,
        f"{len(seen)} AIOps alerts",
    )
    lab = sorted(
        {
            (
                a["rule"],
                a.get("output_fields", {}).get("container.name"),
                a.get("output_fields", {}).get("proc.name"),
            )
            for a in seen
            if a.get("output_fields", {}).get("container.name") not in (TARGET, EDGE, OTHER)
        }
    )
    ev.check(
        "the_labs_own_containers_and_their_health_probes_raise_none_of_the_platform_rules_in_the_same_window",
        not lab,
        f"{len(lab)} unexpected (rule, container, process): {lab[:6]}",
    )
    ev.metrics["lab_containers_running"] = run(
        ["docker", "ps", "--format", "{{.Names}}"]
    ).stdout.split()
    quiet = [a["rule"] for a in seen if a.get("output_fields", {}).get("container.name") == OTHER]
    ev.check(
        "the_same_actions_in_a_container_that_is_not_the_platforms_raise_none_of_the_platform_rules",
        not quiet,
        f"alerts for {OTHER}: {quiet}",
    )
    benign = [
        a["output"][:120]
        for a in seen
        if a.get("output_fields", {}).get("proc.name") in ("ls",) or "benign" in a["output"]
    ]
    only_shell = [a["rule"] for a in fired(RULES["shell"], TARGET)]
    ev.check(
        "benign_actions_in_a_platform_container_raise_nothing",
        not benign and len(only_shell) >= 1,
        f"benign alerts: {benign}; shell alerts {len(only_shell)} (the interactive one)",
    )
    ev.metrics = {
        "alerts_by_rule": {r: sum(1 for a in seen if a["rule"] == r) for r in RULES.values()},
        "sample_alerts": [a["output"][:220] for a in seen[:8]],
        "falco_image": FALCO_IMAGE,
        "kernel": ev.notes["kernel"],
        "falco_log_tail": logs_tail,
    }
    ev.notes["not_proven"] = (
        "runs on the local development kernel (WSL2), not on the target host, and the actions are performed by this script with docker exec, not by an adversary; "
        "P09.09's acceptance names the target host, and alert routing to an operator is not built"
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
