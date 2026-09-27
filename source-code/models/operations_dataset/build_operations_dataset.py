"""P10.05: capture the operational datasets - normal and degraded platform behaviour, measured, with truth and hashes.

    python source-code/models/operations_dataset/build_operations_dataset.py --workers 6
    python source-code/models/operations_dataset/build_operations_dataset.py --only-seed 20260918 --only-scenario normal   # smoke

What is measured. Each run is one OS process driving the platform's real instruments (`load.py`) under a seeded load and,
for a degraded run, one injected fault. The process pushes over OTLP to a DEDICATED Prometheus container (port 9091, its
own volume, no rules) - never the platform's own Prometheus, so a capture cannot trip its alerts or pollute its
aggregates. Afterwards every signal in `signals.py` is read back with PromQL at a 5 s step. Signals (model input) and
truth (fault window, fault type, severity, benign burst) are written to SEPARATE files: truth is never a feature.

Splits reuse P03.08's seed -> split assignment (train 5 / validation 2 / test 2 seeds), so a seed and therefore every one
of its seven runs lives in exactly one split. Nothing is byte-reproducible: these are measurements. What is fixed and
hashed is the captured data (`dataset_manifest.json`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from models.operations_dataset.load import RUN_SECONDS, SCENARIOS, WARMUP_SECONDS  # noqa: E402
from models.operations_dataset.signals import SIGNALS, WINDOW, query  # noqa: E402
from simulator.datasets.build_and_verify import SPLIT_SEEDS  # noqa: E402

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "output"
STEP_S = 5
CAPTURE_NAME = "aiops-ops-capture-prometheus"
CAPTURE_PORT = 9091
PROM_IMAGE = (
    "prom/prometheus:v3.7.3@sha256:49214755b6153f90a597adcbff0252cc61069f8ab69ce8411285cd4a560e8038"
)
OTLP_URL = f"http://127.0.0.1:{CAPTURE_PORT}/api/v1/otlp/v1/metrics"
PROM_URL = f"http://127.0.0.1:{CAPTURE_PORT}"


def _docker(*args: str, timeout: float = 300) -> subprocess.CompletedProcess:
    argv = ["docker", *args] if shutil.which("docker") else ["wsl", "-e", "docker", *args]
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def start_capture_prometheus() -> None:
    _docker("rm", "-f", CAPTURE_NAME)
    run = _docker(
        "run", "-d", "--name", CAPTURE_NAME, "-p", f"127.0.0.1:{CAPTURE_PORT}:9090",
        "--cpus=1.0", "--memory=768m", PROM_IMAGE,
        "--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.path=/prometheus",
        "--storage.tsdb.retention.time=12h", "--web.enable-otlp-receiver",
    )  # fmt: skip
    if run.returncode != 0:
        raise RuntimeError(f"capture prometheus did not start: {run.stderr[-300:]}")
    for _ in range(60):
        try:
            if requests.get(f"{PROM_URL}/-/ready", timeout=2).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise RuntimeError("capture prometheus never became ready")


def stop_capture_prometheus() -> None:
    _docker("rm", "-f", CAPTURE_NAME)


def run_id(seed: int, scenario: str, replicate: int) -> str:
    return f"ops-{seed}-{scenario.replace('_', '-')}-r{replicate}"


def split_of(seed: int) -> str:
    return next(split for split, seeds in SPLIT_SEEDS.items() if seed in seeds)


def launch(seed: int, scenario: str, replicate: int, tmp: Path) -> subprocess.Popen:
    rid = run_id(seed, scenario, replicate)
    return subprocess.Popen(
        [
            sys.executable, str(HERE / "capture_run.py"), "--run-id", rid, "--seed", str(seed),
            "--scenario", scenario, "--replicate", str(replicate), "--otlp", OTLP_URL, "--out", str(tmp / f"{rid}.json"),
        ],
        cwd=str(SOURCE_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=(tmp / f"{rid}.err").open("w"),
    )  # fmt: skip


def read_signal(name: str, rid: str, start: float, end: float) -> dict[int, float]:
    response = requests.get(
        f"{PROM_URL}/api/v1/query_range",
        params={"query": query(name, rid), "start": start, "end": end, "step": STEP_S},
        timeout=60,
    )
    result = response.json()["data"]["result"]
    series: dict[int, float] = {}
    if result:
        for ts, value in result[0]["values"]:
            number = float(value)
            if not math.isnan(number):
                series[round(float(ts))] = number
    return series


def phase(plan: dict, second: int) -> str:
    if second < WARMUP_SECONDS:
        return "warmup"
    start = plan["fault_start"]
    if start is not None and start <= second < start + plan["fault_seconds"]:
        return "fault"
    if start is not None and second >= start + plan["fault_seconds"]:
        return "recovery"
    return "baseline"


def write_run(root: Path, meta: dict) -> dict:
    rid, plan = meta["run_id"], meta["plan"]
    split = split_of(meta["seed"])
    directory = root / split / rid
    directory.mkdir(parents=True, exist_ok=True)
    start, end = meta["start_epoch"], meta["end_epoch"]
    series = {name: read_signal(name, rid, start, end + 5) for name in SIGNALS}

    signal_rows, truth_rows = [], []
    t0 = round(start)
    for t in range(t0, round(min(end, start + RUN_SECONDS)) + 1, STEP_S):
        second = t - t0
        signal_rows.append(
            {
                "t": datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "t_rel_s": second,
                "values": {name: series[name].get(t) for name in SIGNALS},
            }
        )
        fault = (
            plan["fault_start"] is not None
            and plan["fault_start"] <= second < plan["fault_start"] + plan["fault_seconds"]
        )
        truth_rows.append(
            {
                "t_rel_s": second,
                "phase": phase(plan, second),
                "fault_active": fault,
                "fault_type": plan["scenario"] if fault else None,
                "severity": plan["severity"] if fault else 0.0,
                "benign_burst": plan["burst_start"]
                <= second
                < plan["burst_start"] + plan["burst_seconds"],
            }
        )
    (directory / "signals.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in signal_rows),
        encoding="utf-8",
        newline="\n",
    )
    (directory / "truth.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in truth_rows),
        encoding="utf-8",
        newline="\n",
    )
    (directory / "run.json").write_text(
        json.dumps(
            {**meta, "split": split, "signal_rows": len(signal_rows), "step_s": STEP_S},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {
        "run_id": rid,
        "split": split,
        "seed": meta["seed"],
        "scenario": meta["scenario"],
        "replicate": meta["replicate"],
        "rows": len(signal_rows),
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(root: Path, runs: list[dict], prom_version: str, replicates: int) -> dict:
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.name != "dataset_manifest.json")
    hashes = {str(p.relative_to(root)).replace("\\", "/"): _sha256(p) for p in files}
    digest = hashlib.sha256(
        "".join(f"{name}:{h}\n" for name, h in sorted(hashes.items())).encode()
    ).hexdigest()
    manifest = {
        "schema": "operations-dataset-manifest-v1",
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": "MEASURED (not byte-reproducible): real instruments driven by a seeded synthetic load, read back from Prometheus",
        "splits": SPLIT_SEEDS,
        "scenarios": list(SCENARIOS),
        "replicates": replicates,
        "run_seconds": RUN_SECONDS,
        "warmup_seconds": WARMUP_SECONDS,
        "step_s": STEP_S,
        "signal_window": WINDOW,
        "signals": {
            n: {"description": d, "unit": u, "promql": q} for n, (d, u, q) in SIGNALS.items()
        },
        "measurement_environment": {
            "host": platform.platform(),
            "python": platform.python_version(),
            "prometheus_image": PROM_IMAGE,
            "prometheus_version": prom_version,
            "ingest": "OTLP push, 2 s export interval, dedicated Prometheus, one OS process per run",
        },
        "runs": sorted(runs, key=lambda r: r["run_id"]),
        "files": hashes,
        "dataset_sha256": digest,
    }
    (root / "dataset_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--replicates", type=int, default=2)
    parser.add_argument("--label", default="run-a")
    parser.add_argument("--only-seed", type=int, action="append")
    parser.add_argument("--only-scenario", action="append")
    args = parser.parse_args()

    seeds = [
        s
        for seeds in SPLIT_SEEDS.values()
        for s in seeds
        if not args.only_seed or s in args.only_seed
    ]
    scenarios = [s for s in SCENARIOS if not args.only_scenario or s in args.only_scenario]
    plan = [
        (seed, scenario, rep)
        for seed in seeds
        for scenario in scenarios
        for rep in range(args.replicates)
    ]
    root = OUTPUT / args.label
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    tmp = OUTPUT / f"_capture_{args.label}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    print(
        f"{len(plan)} runs, {args.workers} at a time, about {RUN_SECONDS // 60} min each",
        flush=True,
    )
    start_capture_prometheus()
    runs: list[dict] = []
    try:
        active: list[tuple[int, str, int, subprocess.Popen]] = []
        queue = list(plan)
        finished = 0
        while queue or active:
            while queue and len(active) < args.workers:
                seed, scenario, rep = queue.pop(0)
                active.append((seed, scenario, rep, launch(seed, scenario, rep, tmp)))
            time.sleep(2)
            for item in list(active):
                seed, scenario, rep, proc = item
                if proc.poll() is None:
                    continue
                active.remove(item)
                finished += 1
                rid = run_id(seed, scenario, rep)
                meta_file = tmp / f"{rid}.json"
                if proc.returncode != 0 or not meta_file.is_file():
                    raise RuntimeError(
                        f"run {rid} failed: {(tmp / (rid + '.err')).read_text()[-400:]}"
                    )
                time.sleep(4)  # let the last export land
                runs.append(write_run(root, json.loads(meta_file.read_text(encoding="utf-8"))))
                print(
                    f"[{finished}/{len(plan)}] {rid} captured ({runs[-1]['rows']} rows)",
                    flush=True,
                )
        version = requests.get(f"{PROM_URL}/api/v1/status/buildinfo", timeout=10).json()["data"][
            "version"
        ]
        manifest = write_manifest(root, runs, version, args.replicates)
        print(
            f"dataset_sha256 {manifest['dataset_sha256']}  ({len(runs)} runs, {len(manifest['files'])} files)"
        )
    finally:
        stop_capture_prometheus()
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
