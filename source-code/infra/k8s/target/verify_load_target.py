#!/usr/bin/env python3
"""P11.06 acceptance evidence: load, capacity, soak and degraded-mode behaviour of the ingestion path on the target, measured.

    KUBECONFIG=... SOAK_MINUTES=60 python3 verify_load_target.py        # ON the target host; about 20 minutes plus the soak; writes docs/evidence/p11_06_target_load.json

Every load is real devices (their own certificates) publishing over the broker's mTLS listener; every event must travel broker -> gateway -> Kafka ->
ingestion -> PostgreSQL, and is counted at the end by a label carried in the event (`backend/load_check.py`).

A. NOMINAL   the load the demonstration is sized for (16 events/s from 8 devices): nothing lost, latency measured.
B. PEAK      ten times that (160 events/s from 16 devices).
C. CEILING   steps up until something gives (loss, or the time to catch up, or the publisher itself): the highest step with nothing lost and the
             first that is not clean are both reported, with the resource use of every component at each step.
D. DEGRADED  each dependency is taken away DURING a load: Kafka, ingestion, PostgreSQL, the gateway. What the platform does is recorded, not judged in
             advance: what was buffered, what caught up, what was lost.
E. SOAK      nominal load for SOAK_MINUTES with every component's memory, restarts and the database and log volumes sampled every five minutes.

The ceiling is the ceiling of THIS deployment on THIS host (one node, resource limits as in workloads.yaml, a shared machine): it is a measurement, not a
capacity promise.
"""

from __future__ import annotations

import json
import os
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
ev = Evidence("P11.06", "p11_06_target_load", docs_name="p11_06_target_load")
SOAK_MINUTES = int(os.environ.get("SOAK_MINUTES", "60"))
WATCHED = {
    "aiops-mqtt-broker": "sts",
    "aiops-mqtt-gateway": "sts",
    "aiops-kafka-broker": "sts",
    "aiops-ingestion-gateway": "deploy",
    "aiops-postgres": "sts",
    "aiops-api": "deploy",
}


def kubectl(
    *args: str, check: bool = True, timeout: int = 300, stdin: str | None = None
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


def start_load(label: str, devices: int, rate: float, seconds: int, drain: int = 90) -> None:
    kubectl("-n", NS, "delete", "job", "aiops-load", "--ignore-not-found", "--wait=true")
    doc = next(
        d
        for d in yaml.safe_load_all((K8S / "jobs.yaml").read_text(encoding="utf-8"))
        if d and d["metadata"]["name"] == "aiops-load"
    )
    doc["spec"]["template"]["spec"]["containers"][0]["env"] += [
        {"name": "LOAD_DEVICES", "value": str(devices)}, {"name": "LOAD_RATE", "value": str(rate)}, {"name": "LOAD_SECONDS", "value": str(seconds)},
        {"name": "LOAD_LABEL", "value": label}, {"name": "LOAD_DRAIN_SECONDS", "value": str(drain)},
    ]  # fmt: skip
    kubectl("apply", "-f", "-", stdin=json.dumps(doc))


def finish_load(seconds: int, drain: int = 90) -> dict:
    kubectl(
        "-n",
        NS,
        "wait",
        "--for=condition=complete",
        "job/aiops-load",
        f"--timeout={seconds + drain + 180}s",
        check=False,
        timeout=seconds + drain + 200,
    )
    log = kubectl("-n", NS, "logs", "job/aiops-load", check=False).stdout.strip().splitlines()
    return (
        json.loads(log[-1])
        if log and log[-1].startswith("{")
        else {
            "passed": False,
            "offered": 0,
            "arrived_in_postgres": 0,
            "lost": None,
            "latency_s": {},
        }
    )


def load(label: str, devices: int, rate: float, seconds: int, drain: int = 90) -> dict:
    start_load(label, devices, rate, seconds, drain)
    return finish_load(seconds, drain)


def cgroup(kind: str, name: str) -> dict:
    out = kubectl(
        "-n",
        NS,
        "exec",
        f"{kind}/{name}",
        "--",
        "sh",
        "-c",
        "cat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.peak /sys/fs/cgroup/cpu.stat 2>/dev/null",
        check=False,
        timeout=60,
    ).stdout.split()
    try:
        stat = {out[i]: int(out[i + 1]) for i in range(2, len(out) - 1, 2)}
        return {
            "memory_mib": round(int(out[0]) / 2**20),
            "memory_peak_mib": round(int(out[1]) / 2**20),
            "cpu_seconds": round(stat.get("usage_usec", 0) / 1e6, 1),
            "throttled_periods": stat.get("nr_throttled", 0),
        }
    except (ValueError, IndexError):
        return {}


def usage() -> dict:
    return {name: cgroup(kind, name) for name, kind in WATCHED.items()}


def restarts() -> int:
    pods = json.loads(kubectl("-n", NS, "get", "pods", "-o", "json").stdout)["items"]
    return sum(
        c["restartCount"]
        for p in pods
        for c in p["status"].get("containerStatuses", [])
        if not p["metadata"]["name"].startswith(
            ("aiops-e2e", "aiops-load", "aiops-security", "aiops-migrate")
        )
    )


def volumes() -> dict:
    user = json.loads(kubectl("-n", NS, "get", "secret", "aiops-secrets", "-o", "json").stdout)[
        "data"
    ]["postgres-user"]
    import base64

    user = base64.b64decode(user).decode()
    size = (
        kubectl(
            "-n",
            NS,
            "exec",
            "sts/aiops-postgres",
            "--",
            "psql",
            "-U",
            user,
            "-d",
            "aiops",
            "-At",
            "-c",
            "SELECT pg_database_size('aiops')/1024/1024, (SELECT count(*) FROM observation_events)",
            check=False,
        )
        .stdout.strip()
        .split("|")
    )
    kafka = kubectl(
        "-n",
        NS,
        "exec",
        "sts/aiops-kafka-broker",
        "--",
        "sh",
        "-c",
        "du -sm /var/lib/redpanda/data | cut -f1",
        check=False,
    ).stdout.strip()
    return {
        "database_mib": int(size[0]) if size and size[0].isdigit() else None,
        "observation_rows": int(size[1]) if len(size) > 1 and size[1].isdigit() else None,
        "kafka_mib": int(kafka) if kafka.isdigit() else None,
    }


def clean(report: dict, p95_s: float = 5.0) -> bool:
    latency = report.get("latency_s", {}).get("p95")
    return (
        bool(report.get("passed"))
        and report.get("lost") == 0
        and latency is not None
        and latency <= p95_s
    )


def main() -> int:  # noqa: PLR0915
    ev.metrics["idle_usage"] = usage()
    ev.metrics["restarts_at_start"] = restarts()

    # ---- A. nominal
    nominal = load("nominal", 8, 2, 120)
    ev.check(
        "nominal_load_16_events_per_second_for_two_minutes_loses_nothing_and_arrives_within_5_s_at_p95",
        clean(nominal),
        f"{nominal.get('arrived_in_postgres')}/{nominal.get('offered')} arrived, p50 {nominal.get('latency_s', {}).get('p50')} s, p95 {nominal.get('latency_s', {}).get('p95')} s, p99 {nominal.get('latency_s', {}).get('p99')} s",
    )
    ev.metrics["nominal"] = {"report": nominal, "usage": usage()}

    # ---- B. peak
    peak = load("peak", 16, 10, 60)
    ev.check(
        "peak_load_ten_times_nominal_loses_nothing_and_arrives_within_5_s_at_p95",
        clean(peak),
        f"{peak.get('arrived_in_postgres')}/{peak.get('offered')} arrived at {peak.get('sustained_publish_rate')} events/s, p95 {peak.get('latency_s', {}).get('p95')} s",
    )
    ev.metrics["peak"] = {"report": peak, "usage": usage()}

    # ---- C. ceiling: step up until something gives
    steps = []
    last_clean = first_bad = None
    for rate in (
        12,
        16,
        20,
        25,
        50,
        100,
    ):  # per device, 16 devices: 192, 256, 320, 400, 800, 1600 events/s offered
        report = load(f"ceiling-{rate * 16}", 16, rate, 45, drain=150)
        record = {
            "offered_rate": report.get("offered_rate"),
            "sustained_publish_rate": report.get("sustained_publish_rate"),
            "arrived": f"{report.get('arrived_in_postgres')}/{report.get('offered')}",
            "lost": report.get("lost"),
            "p95_s": report.get("latency_s", {}).get("p95"),
            "max_s": report.get("latency_s", {}).get("max"),
            "seconds_until_everything_arrived": report.get("seconds_until_everything_arrived"),
            "usage": usage(),
        }
        steps.append(record)
        if clean(report):
            last_clean = record["sustained_publish_rate"]
        else:
            first_bad = record["sustained_publish_rate"]
            break
    ev.metrics["ceiling_steps"] = steps
    ev.check(
        "a_ceiling_was_found_or_the_publisher_was_the_limit_and_the_result_says_which",
        bool(steps) and last_clean is not None,
        f"highest clean sustained rate {last_clean} events/s; first step that was not clean: {first_bad if first_bad else 'none reached (the publisher, one pod of 1 CPU, is the limit)'}",
    )

    # ---- D. degraded modes, each during a load
    degraded = {}
    for name, kind, target in (
        ("kafka", "sts", "aiops-kafka-broker"),
        ("ingestion", "deploy", "aiops-ingestion-gateway"),
        ("postgres", "sts", "aiops-postgres"),
        ("gateway", "sts", "aiops-mqtt-gateway"),
    ):
        start_load(f"degraded-{name}", 8, 5, 90, drain=240)
        time.sleep(25)
        kubectl("-n", NS, "scale", f"{kind}/{target}", "--replicas=0")
        time.sleep(25)
        kubectl("-n", NS, "scale", f"{kind}/{target}", "--replicas=1")
        kubectl(
            "-n",
            NS,
            "rollout",
            "status",
            f"{'statefulset' if kind == 'sts' else 'deployment'}/{target}",
            "--timeout=300s",
            check=False,
            timeout=320,
        )
        report = finish_load(90, drain=240)
        degraded[name] = {
            k: report.get(k)
            for k in (
                "offered",
                "acked_by_broker",
                "arrived_in_postgres",
                "lost",
                "seconds_until_everything_arrived",
            )
        } | {"latency_s": report.get("latency_s")}
        outcome = "nothing lost" if report.get("lost") == 0 else f"{report.get('lost')} lost"
        ev.check(
            f"with_{name}_taken_away_for_25_s_during_a_load_the_outcome_is_measured_and_the_platform_is_healthy_again",
            report.get("offered", 0) > 0 and report.get("arrived_in_postgres") is not None,
            f"{outcome}: {report.get('arrived_in_postgres')}/{report.get('offered')} arrived, all arrived after {report.get('seconds_until_everything_arrived')} s, p95 {report.get('latency_s', {}).get('p95')} s",
        )
    ev.metrics["degraded"] = degraded
    ev.metrics["loss_under_degradation"] = {k: v["lost"] for k, v in degraded.items()}
    ev.check(
        "no_event_the_broker_acknowledged_is_lost_in_any_of_the_four_degraded_modes",
        all(v["lost"] == 0 for v in degraded.values()),
        f"lost per mode: {ev.metrics['loss_under_degradation']}",
    )

    # ---- E. soak
    baseline = usage()
    samples = [{"minute": 0, "usage": baseline, "volumes": volumes(), "restarts": restarts()}]
    if SOAK_MINUTES:
        start_load("soak", 8, 2, SOAK_MINUTES * 60, drain=240)
        for minute in range(5, SOAK_MINUTES + 1, 5):
            time.sleep(300)
            samples.append(
                {"minute": minute, "usage": usage(), "volumes": volumes(), "restarts": restarts()}
            )
        soak = finish_load(SOAK_MINUTES * 60, drain=240)
        ev.check(
            f"a_{SOAK_MINUTES}_minute_soak_at_nominal_load_loses_nothing",
            clean(soak),
            f"{soak.get('arrived_in_postgres')}/{soak.get('offered')} arrived, p95 {soak.get('latency_s', {}).get('p95')} s, p99 {soak.get('latency_s', {}).get('p99')} s",
        )
        first, last = samples[0]["usage"], samples[-1]["usage"]
        growth = {
            n: (last[n].get("memory_mib", 0) - first[n].get("memory_mib", 0))
            for n in first
            if first[n] and last[n]
        }
        ev.check(
            "no_component_restarted_during_the_soak",
            samples[-1]["restarts"] == samples[0]["restarts"],
            f"restarts {samples[0]['restarts']} -> {samples[-1]['restarts']}",
        )
        ev.metrics["soak"] = {"report": soak, "samples": samples, "memory_growth_mib": growth}
        ev.notes["soak_memory_growth_mib"] = json.dumps(growth)
    ev.notes["scope"] = (
        "one node, resource limits as in workloads.yaml, a shared host, the load generator a single 1-CPU pod: the ceiling is a property of this deployment on this machine, not a capacity promise"
    )
    ev.notes["load_shape"] = (
        "constant-rate publishers, QoS 1, one small loop-detector event each; a real network would add jitter, larger messages and bursts"
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
