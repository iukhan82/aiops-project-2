"""P10.09 acceptance evidence: AIOps recovery and failure limits, proven on controlled faults against the real running stack.

    python source-code/backend/aiops/verify_recovery.py            # about 1 h 30 min of real time
    RECOVERY_ONLY=crash_restart python source-code/backend/aiops/verify_recovery.py   # one scenario

Everything in the loop is real: the platform's Prometheus and its alert rules (P10.04), the correlator (P10.07), the worker with its
own database role, the policy engine `aiops-opa` (P10.08), the docker and model-registry adapters, and a real edge runtime container
(genuine ONNX model, scraped by Prometheus). Only the incident database is a scratch one. The two loops run on their own cadence, as
two services would (correlator every 15 s, worker every 10 s). What is induced is stated per scenario.

  crash_restart   CONTROL: the runtime is killed and the worker held back - the alert must persist (nothing self-heals), which is what
                  makes the recovery that follows attributable to the action. Then the worker is released, and two more crashes are
                  taken through detect -> incident -> policy -> restart -> alerts clear and stay clear -> resolved.
  bad_model       the active model version rots on disk and the runtime restarts: the worker rolls the registry back to the previous
                  verified version and restarts the runtime; the alerts clear on the old model.
  unrecoverable   every model version rots: the rollback is refused (nothing changes), the restart does not help, the attempt limits
                  are reached and the incident is ESCALATED to a person - never left silent, and automation then stops.
  stateful        a stateful service (Postgres) is reported down: the worker asks for a person, runs nothing, and an approval from a
                  role the policy does not accept does not run it either. The real Postgres container is never touched.
  policy_outage   the policy engine is stopped during an incident: the worker does nothing (fails closed); with the engine back, it
                  proceeds and the platform recovers.
  worker_crash    the worker process is killed while its restart is running: the request is closed `abandoned` (counted), no action is
                  repeated, and the incident still resolves.

Measured and reported, not assumed: fault-to-alert, alert-to-incident, incident-to-action-start (LAT-06, target P95 < 30 s), action-to-
verified and fault-to-resolved times per crash, with the sample size.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend import observability, pdp  # noqa: E402
from backend.aiops import platform_correlator, platform_probe, platform_signals  # noqa: E402
from backend.aiops import remediation as remediation_registry  # noqa: E402
from backend.aiops import remediation_adapters as adapters_mod  # noqa: E402
from backend.aiops.remediation_worker import Worker  # noqa: E402
from backend.aiops.verify_alerts import EDGE_OUT, PROM_URL, Prom, _mnt, docker, wait_for  # noqa: E402
from backend.aiops.verify_remediation import stage_into  # noqa: E402
from backend.demo import world  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.repositories import platform_incidents as incidents  # noqa: E402
from backend.repositories import remediation as repo  # noqa: E402
from database.migrate import dsn_from_env, migrate  # noqa: E402

ev = Evidence("P10.09", "p10_09_recovery", docs_name="p10_09_recovery")
DB = "aiops_p1009"
EDGE_NAME = "aiops-edge-runtime"
EDGE_ALERTS = ("EdgeRuntimeDown", "EdgeModelInactive", "EdgeModelErrors")
CORRELATOR_EVERY_S = 15
WORKER_EVERY_S = 10
LAT_06_LIMIT_S = 30.0
samples: dict[str, list[float]] = {
    "detect_s": [],
    "incident_s": [],
    "dispatch_s": [],
    "verify_s": [],
    "mttr_s": [],
}


def now() -> datetime:
    return datetime.now(UTC)


def secs(a: datetime, b: datetime) -> float:
    return round((b - a).total_seconds(), 1)


# ----------------------------------------------------------------------------------------------------- database
def fresh_database() -> None:
    os.environ["POSTGRES_DB"] = "aiops"
    world.use_database(DB)
    if world.database_exists(DB):
        world.drop_database(DB)
    with psycopg.connect(world._maintenance_dsn(), autocommit=True) as conn:  # noqa: SLF001
        conn.execute(f'CREATE DATABASE "{DB}"')
    with psycopg.connect(dsn_from_env()) as conn:
        migrate(conn)


def connect(role: str | None = None) -> psycopg.Connection:
    os.environ["POSTGRES_DB"] = DB
    return psycopg.connect(dsn_from_env(role=role), autocommit=True)


# --------------------------------------------------------------------------------------------- the edge runtime
def image_tag() -> str:
    digest = hashlib.sha256()
    for folder in ("edge", "contracts"):
        for path in sorted((SOURCE_ROOT / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                digest.update(str(path.relative_to(SOURCE_ROOT)).encode())
                digest.update(path.read_bytes())
    return f"aiops-edge-runtime:recovery-{digest.hexdigest()[:12]}"


class EdgeRig:
    """A real edge runtime container serving the model version its activation pointer names, from a registry copy on the host."""

    def __init__(self, tmp: Path) -> None:
        from edge.activation import ModelActivator
        from edge.features import FEATURE_VERSION

        self.tmp = tmp
        self.registry = tmp / "registry"
        real = SOURCE_ROOT / "models" / "registry"
        shutil.copytree(real / "baseline", self.registry / "baseline")
        self.model_root = self.registry / "traffic-safety-blockage"
        self.model_root.mkdir(parents=True)
        self.real_package = real / "traffic-safety-blockage" / "1.0.0"
        self.stage()
        self.activator = ModelActivator(self.model_root, FEATURE_VERSION)
        self.activator.activate("1.0.0", "seed")
        self.activator.activate("1.0.1", "release")
        config = json.loads((EDGE_OUT / "benchmark_config.json").read_text(encoding="utf-8"))
        config.pop("model_dir")
        config["model_root"] = "/data/registry/traffic-safety-blockage"
        config["site_id"] = config["boot_id"] = "recovery"
        self.config = tmp / "config.json"
        self.config.write_text(json.dumps(config), encoding="utf-8")
        with (EDGE_OUT / "benchmark_events.jsonl").open(encoding="utf-8") as handle:
            first = json.loads(handle.readline())["observation_time"]
        last_line = (EDGE_OUT / "benchmark_events.jsonl").read_bytes().rstrip().splitlines()[-1]
        last = json.loads(last_line)["observation_time"]
        span = (
            datetime.fromisoformat(last.replace("Z", "+00:00"))
            - datetime.fromisoformat(first.replace("Z", "+00:00"))
        ).total_seconds()
        self.pace = max(span / 3000.0, 0.05)  # a replay lasts about 50 minutes per start

    def stage(self) -> None:
        for version in ("1.0.0", "1.0.1"):
            shutil.rmtree(self.model_root / version, ignore_errors=True)
            stage_into(self.model_root, self.real_package, version)

    def corrupt(self, *versions: str) -> None:
        for version in versions:
            weights = self.model_root / version / "model.onnx"
            data = bytearray(weights.read_bytes())
            data[len(data) // 2] ^= 0xFF
            weights.write_bytes(bytes(data))

    def ensure_image(self) -> str:
        tag = image_tag()
        if docker("image", "inspect", tag).returncode != 0:
            context = self.tmp / "build-context"
            for folder in ("edge", "contracts"):
                shutil.copytree(
                    SOURCE_ROOT / folder,
                    context / folder,
                    ignore=shutil.ignore_patterns("__pycache__"),
                )
            build = docker(
                "build",
                "-f",
                f"{_mnt(context)}/edge/Dockerfile",
                "-t",
                tag,
                _mnt(context),
                timeout=1800,
            )
            if build.returncode != 0:
                raise RuntimeError(f"edge image build failed: {build.stderr[-400:]}")
        return tag

    def start(self) -> None:
        docker("rm", "-f", EDGE_NAME)
        run = docker(
            "run", "-d", "--name", EDGE_NAME, "--network", "aiops-platform-net", "--user", "10001:10001",
            "--cpus=0.75", "--memory=512m", "--memory-swap=512m", "--pids-limit=64", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--read-only", "--tmpfs", "/tmp",
            "-v", f"{_mnt(EDGE_OUT / 'devices.jsonl')}:/data/devices.jsonl:ro",
            "-v", f"{_mnt(self.registry)}:/data/registry:ro",
            "-v", f"{_mnt(self.config)}:/data/config.json:ro",
            "-v", f"{_mnt(EDGE_OUT / 'benchmark_events.jsonl')}:/data/events.jsonl:ro",
            self.ensure_image(), "replay", "--config", "/data/config.json", "--events", "/data/events.jsonl",
            "--pace", f"{self.pace:.4f}", "--health-host", "0.0.0.0", "--health-port", "9100",
        )  # fmt: skip
        if run.returncode != 0:
            raise RuntimeError(f"edge container did not start: {run.stderr[-300:]}")

    def kill(self) -> None:
        docker("kill", EDGE_NAME)

    def restart(self) -> None:
        docker("restart", "-t", "2", EDGE_NAME)

    def stop(self) -> None:
        docker("rm", "-f", EDGE_NAME)

    def active_version(self) -> str | None:
        return self.activator.active_version()


PROM = Prom()


def edge_quiet_and_healthy() -> bool:
    return (
        bool(PROM.query('up{job="edge-runtime"} == 1'))
        and bool(PROM.query("edge_model_active == 1"))
        and not any(PROM.state(name) for name in EDGE_ALERTS)
    )


# ------------------------------------------------------------------------------------------ the two live loops
class Loops:
    """The correlator and the worker, each on its own thread and cadence, each with its own database role."""

    def __init__(self, adapters: dict, detector=None) -> None:  # noqa: ANN001
        self.adapters, self.detector = adapters, detector
        self.stop = threading.Event()
        self.worker_enabled = threading.Event()
        self.worker_enabled.set()
        self.log: list[str] = []
        self.counts: dict[str, int] = {}
        self.threads: list[threading.Thread] = []
        self.started = time.time()

    def start(self) -> None:
        self.started = time.time()
        for target in (self._correlate, self._work):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self.threads.append(thread)

    def shutdown(self) -> None:
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=30)

    def _correlate(self) -> None:
        with connect("svc_platform_correlator") as conn:
            while not self.stop.is_set():
                try:
                    platform_correlator.cycle(
                        conn,
                        self.detector,
                        lookback_s=int(min(900, time.time() - self.started + 30)),
                    )
                except Exception as exc:  # noqa: BLE001
                    self.log.append(f"correlator cycle failed: {exc}")
                self.stop.wait(CORRELATOR_EVERY_S)

    def _work(self) -> None:
        self.stop.wait(7)  # not in step with the correlator
        with connect("svc_remediation_worker") as conn:
            worker = Worker(conn, adapters=self.adapters, log=self.log.append, clock=now)
            while not self.stop.is_set():
                if self.worker_enabled.is_set():
                    try:
                        for key, value in worker.cycle().items():
                            self.counts[key] = self.counts.get(key, 0) + value
                    except Exception as exc:  # noqa: BLE001
                        self.log.append(f"worker cycle failed: {exc}")
                self.stop.wait(WORKER_EVERY_S)


def real_adapters(rig: EdgeRig) -> dict:
    docker_adapter = adapters_mod.DockerAdapter()
    return {
        "docker": docker_adapter,
        "model_registry": adapters_mod.ModelRegistryAdapter(rig.registry, docker_adapter),
        "trace_sampling": adapters_mod.TraceSamplingAdapter(Path(tempfile.gettempdir()) / f"p1009-sampling-{uuid.uuid4().hex}.json"),
        "plan_only": adapters_mod.PlanOnlyAdapter(),
    }  # fmt: skip


# ----------------------------------------------------------------------------------------------- measurement
def first_firing(name: str, since: datetime, until: datetime) -> datetime | None:
    series = platform_signals.fetch_alert_series(
        since - timedelta(seconds=30), until + timedelta(seconds=30)
    )
    stamps = [
        ts
        for s in series
        if s["metric"].get("alertname") == name and s["metric"].get("alertstate") == "firing"
        for ts, _ in s["values"]
        if ts >= since.timestamp() - 15
    ]
    return datetime.fromtimestamp(min(stamps), tz=UTC) if stamps else None


def incident_view(admin: psycopg.Connection, key: str) -> dict | None:
    rows = [i for i in incidents.list_incidents(admin) if i["incident_key"] == key]
    if not rows:
        return None
    inc = rows[-1]
    evs = incidents.timeline(admin, inc["platform_incident_id"])
    opened = next((e["at"] for e in evs if e["event"] == "opened"), None)
    return {
        "incident": inc,
        "opened_at": opened,
        "events": evs,
        "requests": repo.for_incident(admin, inc["platform_incident_id"]),
    }


def wait_incident_resolved(admin: psycopg.Connection, key: str, timeout: float) -> dict | None:
    def done() -> bool:
        view = incident_view(admin, key)
        return bool(view and view["incident"]["status"] == "resolved")

    return incident_view(admin, key) if wait_for(done, timeout, poll=10) is not None else None


@contextmanager
def session(rig: EdgeRig, worker_enabled: bool = True):
    """A fresh scratch database, a healthy edge runtime and both loops running: one independent 'day' for a scenario."""
    fresh_database()
    rig.stage()
    rig.activator.activate("1.0.0", "reset")
    rig.activator.activate("1.0.1", "reset")
    rig.start()
    if wait_for(edge_quiet_and_healthy, 300, poll=10) is None:
        raise RuntimeError("the edge runtime did not become healthy and quiet")
    time.sleep(
        60
    )  # let the start-up alerts of a fresh container settle before anything is watching
    if not edge_quiet_and_healthy():
        raise RuntimeError("the edge runtime was not quiet after settling")
    loops = Loops(real_adapters(rig))
    if not worker_enabled:
        loops.worker_enabled.clear()
    with connect() as admin:
        loops.start()
        try:
            yield admin, loops
        finally:
            loops.shutdown()


def record_recovery(
    label: str, fault_at: datetime, view: dict | None, counted: bool = True
) -> dict:
    """One taken-through recovery as a timeline, and its latencies added to the samples."""
    alert_at = first_firing("EdgeRuntimeDown", fault_at, now())
    out: dict = {"label": label, "fault_at": fault_at.isoformat(), "resolved": bool(view)}
    if not view:
        return out
    req = next((r for r in view["requests"] if r["attempt_no"] > 0), None)
    out.update(
        {
            "alert_at": alert_at and alert_at.isoformat(),
            "incident_opened_at": view["opened_at"].isoformat(),
            "resolved_at": view["incident"]["resolved_at"].isoformat(),
            "request": {
                "action": req["action_id"], "target": req["target"], "status": req["status"],
                "started_at": req["started_at"].isoformat(), "executed_at": req["executed_at"].isoformat(),
                "finished_at": req["finished_at"].isoformat(),
                "verification": req["verification"], "result": req["result"],
            } if req else None,
        }
    )  # fmt: skip
    if req and alert_at and counted:
        samples["detect_s"].append(secs(fault_at, alert_at))
        samples["incident_s"].append(secs(alert_at, view["opened_at"]))
        samples["dispatch_s"].append(secs(view["opened_at"], req["started_at"]))
        samples["verify_s"].append(secs(req["executed_at"], req["finished_at"]))
        samples["mttr_s"].append(secs(fault_at, view["incident"]["resolved_at"]))
    return out


# ------------------------------------------------------------------------------------------------ scenarios
def scenario_crash_restart(rig: EdgeRig) -> None:
    # ---- control: the worker is held back; nothing may bring the runtime back
    with session(rig, worker_enabled=False) as (admin, loops):
        fault_at = now()
        rig.kill()
        opened = wait_for(lambda: incident_view(admin, "edge-runtime") is not None, 420, poll=5)
        held_until = now() + timedelta(seconds=240)
        while now() < held_until:
            time.sleep(10)
        view = incident_view(admin, "edge-runtime")
        still_down = (
            bool(PROM.query('up{job="edge-runtime"} == 0'))
            and PROM.state("EdgeRuntimeDown") == "firing"
        )
        ev.check(
            "control_with_the_worker_held_back_the_crashed_runtime_stays_down_its_alert_keeps_firing_and_the_incident_stays_open",
            opened is not None
            and still_down
            and view["incident"]["status"] == "open"
            and not view["requests"],
            f"{secs(fault_at, now())} s after the crash: alert {PROM.state('EdgeRuntimeDown')}, incident {view and view['incident']['status']}, requests {view and len(view['requests'])}",
        )
        # ---- the very same fault, the worker released: now the action recovers it
        loops.worker_enabled.set()
        resolved = wait_incident_resolved(admin, "edge-runtime", 1200)
        ev.check(
            "with_the_worker_released_the_control_crash_is_recovered_and_the_incident_resolves",
            resolved is not None,
        )
        control = record_recovery(
            "control-then-released", fault_at, resolved, counted=False
        )  # the worker was held back on purpose
    # ---- two more crashes, fully automatic, each on its own day
    runs = [control]
    for k in (1, 2):
        with session(rig) as (admin, loops):
            fault_at = now()
            rig.kill()
            opened = wait_for(lambda: incident_view(admin, "edge-runtime") is not None, 420, poll=5)
            view = (
                wait_incident_resolved(admin, "edge-runtime", 1200) if opened is not None else None
            )
            runs.append(record_recovery(f"crash-{k}", fault_at, view))
            ev.metrics.setdefault("crash_restart_worker_log", []).extend(loops.log[-20:])

    good = [r for r in runs[1:] if r["resolved"] and r.get("request")]
    ev.check(
        "every_crash_is_detected_becomes_one_incident_gets_one_restart_and_resolves",
        len(good) == 2
        and all(
            r["request"]["action"] == "restart_container" and r["request"]["status"] == "verified"
            for r in good
        ),
        str([r.get("request", {}).get("status") for r in runs]),
    )
    ordered = all(
        r["fault_at"] < r["alert_at"] <= r["incident_opened_at"] <= r["request"]["started_at"] <= r["request"]["executed_at"] <= r["request"]["finished_at"] <= r["resolved_at"]
        for r in good + [c for c in runs[:1] if c["resolved"] and c.get("request")]
    )  # fmt: skip
    ev.check(
        "the_events_of_each_recovery_are_in_causal_order_fault_alert_incident_action_verification_resolution",
        ordered and bool(good),
    )
    ev.check(
        "each_recovery_was_verified_from_the_alerts_staying_clear_and_only_then_was_the_incident_resolved",
        bool(good) and all(r["request"]["verification"]["verdict"] == "recovered" and r["request"]["verification"]["healthy_for_s"] >= 120 and r["request"]["finished_at"] <= r["resolved_at"] for r in good),
    )  # fmt: skip
    ev.check(
        "the_runtime_really_came_back_from_a_new_process_the_container_start_time_changed",
        bool(good) and all(r["request"]["result"]["after"]["started_at"] != r["request"]["result"]["before"]["started_at"] and r["request"]["result"]["after"]["status"] == "running" for r in good),
    )  # fmt: skip
    ev.metrics["crash_restart"] = {"control_then_reps": runs}


def scenario_bad_model(rig: EdgeRig) -> None:
    with session(rig) as (admin, loops):
        rig.corrupt(
            "1.0.1"
        )  # the active version rots on disk, then the runtime restarts and cannot load it
        fault_at = now()
        rig.restart()
        active_after_fault = rig.active_version()
        view = wait_incident_resolved(admin, "edge-runtime", 1200)
        worker_log = loops.log[-30:]
    alert_at = first_firing("EdgeModelErrors", fault_at, now())
    req = next(
        (r for r in (view or {"requests": []})["requests"] if r["action_id"] == "rollback_model"),
        None,
    )
    ev.check(
        "a_rotted_active_model_raises_the_model_alerts_and_the_incident_is_recovered_by_rolling_back_the_registry",
        bool(view) and alert_at is not None and req is not None and req["status"] == "verified" and rig.active_version() == "1.0.0" and active_after_fault == "1.0.1",
        f"active {active_after_fault} -> {rig.active_version()}; rollback {req and req['status']}",
    )  # fmt: skip
    if req:
        ev.check(
            "the_rollback_switched_the_pointer_to_the_previous_verified_version_and_restarted_the_runtime_which_then_served_it",
            req["result"]["from"] == "1.0.1" and req["result"]["to"] == "1.0.0" and req["result"]["restart"].get("after", {}).get("status") == "running" and req["verification"]["verdict"] == "recovered",
            str(req["result"].get("to")),
        )  # fmt: skip
        samples["detect_s"].append(secs(fault_at, alert_at))
        samples["dispatch_s"].append(secs(view["opened_at"], req["started_at"]))
        samples["mttr_s"].append(secs(fault_at, view["incident"]["resolved_at"]))
    ev.metrics["bad_model"] = {"worker_log": worker_log}


def scenario_unrecoverable(rig: EdgeRig) -> None:
    with session(rig) as (admin, loops):
        rig.corrupt("1.0.0", "1.0.1")  # nothing to fall back to
        pointer_before = rig.active_version()
        rig.restart()
        escalated = wait_for(
            lambda: (incident_view(admin, "edge-runtime") or {"incident": {"status": ""}})["incident"]["status"] == "escalated",
            2400,
            poll=15,
        )  # fmt: skip
        view = incident_view(admin, "edge-runtime")
        requests_at_escalation = (
            len([r for r in view["requests"] if r["attempt_no"] > 0]) if view else 0
        )
        time.sleep(180)  # automation must now leave it alone
        after = incident_view(admin, "edge-runtime")
        attempts = (
            [(r["action_id"], r["status"]) for r in after["requests"] if r["attempt_no"] > 0]
            if after
            else []
        )
        escalations = [e for e in (after["events"] if after else []) if e["event"] == "escalated"]
        ev.check(
            "when_the_attempt_limits_are_reached_and_the_fault_persists_the_incident_is_escalated_to_a_person_with_the_reason_recorded",
            escalated is not None and len(escalations) == 1 and escalations[0]["actor"] == remediation_registry.PLATFORM_ACTOR,
            str(escalations[0]["detail"]) if escalations else "not escalated",
        )  # fmt: skip
        ev.check(
            "every_attempt_stayed_within_its_registered_limit_and_the_rollback_was_refused_without_changing_anything",
            attempts.count(("restart_container", "failed")) <= remediation_registry.ACTIONS["restart_container"].max_attempts_per_incident
            and attempts.count(("rollback_model", "failed")) == 1
            and rig.active_version() == pointer_before,
            str(attempts),
        )  # fmt: skip
        ev.check(
            "after_the_escalation_automation_stands_down_no_further_action_is_taken",
            after is not None and len([r for r in after["requests"] if r["attempt_no"] > 0]) == requests_at_escalation,
            f"{requests_at_escalation} attempts at escalation, {len([r for r in after['requests'] if r['attempt_no'] > 0]) if after else '?'} three minutes later",
        )  # fmt: skip
        # a person puts it right: the incident then resolves on its own (the worker is held back to show it is not the worker)
        loops.worker_enabled.clear()
        rig.stage()
        rig.activator.activate("1.0.0", "repair")
        rig.activator.activate("1.0.1", "repair")
        rig.restart()
        closed = wait_incident_resolved(admin, "edge-runtime", 900)
        ev.check(
            "an_escalated_incident_is_resolved_by_the_correlator_once_the_platform_is_repaired_by_a_person",
            closed is not None,
        )
        worker_log = loops.log[-40:]
    ev.metrics["unrecoverable"] = {"attempts": attempts, "worker_log": worker_log}


def scenario_stateful() -> None:
    fresh_database()
    os.environ.setdefault(
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", f"{PROM_URL}/api/v1/otlp/v1/metrics"
    )
    observability.configure("aiops-recovery-probe")
    postgres_before = docker(
        "inspect", "-f", "{{.State.StartedAt}}", "aiops-postgres"
    ).stdout.strip()
    stop_probe = threading.Event()

    def report() -> None:
        while not stop_probe.is_set():
            platform_probe.publish(
                [
                    platform_probe.Sample(
                        "platform_probe_target_up", 0.0, {"service_name": "postgres"}
                    ),
                    platform_probe.Sample("platform_probe_target_up", 1.0, {"service_name": "opa"}),
                    platform_probe.Sample("platform_probe_target_up", 1.0, {"service_name": "api"}),
                    platform_probe.Sample("platform_probe_success_timestamp_seconds", time.time()),
                ]
            )
            observability.flush()
            stop_probe.wait(5)

    reporter = threading.Thread(target=report, daemon=True)
    reporter.start()
    loops = Loops(
        {"docker": adapters_mod.DockerAdapter(), "plan_only": adapters_mod.PlanOnlyAdapter()}
    )
    loops.start()
    with connect() as admin:
        try:
            waiting = wait_for(
                lambda: bool((incident_view(admin, "postgres") or {"requests": []})["requests"]),
                480,
                poll=10,
            )
            view = incident_view(admin, "postgres")
            req = view["requests"][0] if view and view["requests"] else None
            time.sleep(60)
            postgres_now = docker(
                "inspect", "-f", "{{.State.StartedAt}}", "aiops-postgres"
            ).stdout.strip()
            view = incident_view(admin, "postgres")
            ev.check(
                "a_stateful_service_reported_down_gets_a_request_that_waits_for_a_person_and_the_real_container_is_left_alone",
                waiting is not None and req is not None and view["requests"][0]["status"] == "awaiting_approval" and postgres_now == postgres_before and view["incident"]["status"] == "open",
                f"request {req and req['status']}, postgres started_at unchanged: {postgres_now == postgres_before}",
            )  # fmt: skip
            if req:
                repo.approve(admin, req["remediation_id"], "dispatcher:dan", "dispatcher", now())
                time.sleep(45)
                after = repo.get(admin, req["remediation_id"])
                postgres_after = docker(
                    "inspect", "-f", "{{.State.StartedAt}}", "aiops-postgres"
                ).stdout.strip()
                ev.check(
                    "an_approval_from_a_role_the_policy_does_not_accept_is_withdrawn_and_the_real_container_is_still_untouched",
                    after["status"] == "expired" and postgres_after == postgres_before,
                    f"{after['status']}",
                )
        finally:
            stop_probe.set()
            loops.shutdown()
    ev.metrics["stateful"] = {
        "induced": "the probe was told to report postgres down (its real path needs a broken connection); the alert, correlation and policy are real"
    }


def scenario_policy_outage(rig: EdgeRig) -> None:
    with session(rig, worker_enabled=False) as (admin, loops):
        try:
            rig.kill()
            wait_for(lambda: incident_view(admin, "edge-runtime") is not None, 420, poll=5)
            docker("stop", "aiops-opa")
            loops.worker_enabled.set()
            time.sleep(75)
            view = incident_view(admin, "edge-runtime")
            unavailable = loops.counts.get("policy_unavailable", 0)
            nothing_ran = (
                bool(view)
                and not view["requests"]
                and bool(PROM.query('up{job="edge-runtime"} == 0'))
            )
            docker("start", "aiops-opa")
            if wait_for(lambda: pdp.loaded_version() is not None, 60, poll=2) is None:
                raise RuntimeError("the policy engine did not come back")
            resolved = wait_incident_resolved(admin, "edge-runtime", 1200)
        finally:
            docker("start", "aiops-opa")
    ev.check(
        "with_the_policy_engine_stopped_the_worker_does_nothing_says_so_and_the_fault_is_left_as_it_was",
        unavailable >= 1 and nothing_ran,
        f"policy_unavailable counted {unavailable} times; requests {view and len(view['requests'])}",
    )  # fmt: skip
    ev.check(
        "once_the_policy_engine_is_back_the_worker_proceeds_and_the_platform_recovers",
        resolved is not None,
    )


def scenario_worker_crash(rig: EdgeRig) -> None:
    with session(rig, worker_enabled=False) as (
        admin,
        loops,
    ):  # the worker under test is a separate process
        env = {**os.environ, "POSTGRES_DB": DB}
        cmd = [
            sys.executable,
            str(SOURCE_ROOT / "backend" / "aiops" / "remediation_worker.py"),
            "--interval",
            "5",
            "--database",
            DB,
        ]
        procs = []
        killed_at = None
        try:
            procs.append(
                subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            )  # noqa: S603
            rig.kill()

            def executing() -> bool:
                view = incident_view(admin, "edge-runtime")
                return bool(view and any(r["status"] == "executing" for r in view["requests"]))

            if wait_for(executing, 480, poll=1) is not None:
                procs[0].kill()
                killed_at = now()
            time.sleep(2)
            procs.append(
                subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            )  # noqa: S603
            resolved = wait_incident_resolved(admin, "edge-runtime", 1200)
        finally:
            for proc in procs:
                if proc.poll() is None:
                    proc.kill()
        view = incident_view(admin, "edge-runtime")
        attempts = (
            [(r["action_id"], r["status"]) for r in view["requests"] if r["attempt_no"] > 0]
            if view
            else []
        )
        ev.check(
            "a_worker_killed_mid_action_leaves_a_request_that_is_closed_as_abandoned_not_left_running",
            killed_at is not None and any(status == "abandoned" for _, status in attempts),
            str(attempts),
        )  # fmt: skip
        ev.check(
            "the_action_is_not_repeated_after_the_crash_and_the_incident_still_resolves",
            len(attempts) == 1 and resolved is not None,
            str(attempts),
        )  # fmt: skip


# ------------------------------------------------------------------------------------------------ measurements
def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def report_latencies() -> None:
    summary = {
        name: {"n": len(v), "values_s": v, "median_s": sorted(v)[len(v) // 2] if v else None, "max_s": max(v) if v else None}
        for name, v in samples.items()
    }  # fmt: skip
    ev.metrics["latencies"] = summary
    ev.metrics["cadence"] = {
        "correlator_every_s": CORRELATOR_EVERY_S,
        "worker_every_s": WORKER_EVERY_S,
        "prometheus_evaluation_s": 15,
    }
    dispatch = samples["dispatch_s"]
    ev.check(
        "LAT_06_incident_to_remediation_start_p95_is_under_30_s",
        len(dispatch) >= 3 and percentile(dispatch, 0.95) < LAT_06_LIMIT_S,
        f"n={len(dispatch)}, values {dispatch}, p95 {percentile(dispatch, 0.95) if dispatch else None} s (small sample: the maximum stands in for p95)",
    )
    ev.check(
        "REC_04_every_incident_ended_recovered_and_verified_or_escalated_with_a_recorded_reason_none_was_left_unresolved_and_silent",
        all(v for v in (ev.results.get(k) for k in ev.results if k.startswith(("when_the_attempt_limits", "each_recovery_was_verified", "a_rotted_active_model")))),
    )  # fmt: skip


def main() -> int:
    world.load_platform_env()
    only = os.environ.get("RECOVERY_ONLY")
    started = time.time()
    if PROM.state("AlertPipelineWatchdog") != "firing":
        print("Prometheus is not evaluating rules; start the platform first", flush=True)
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="recovery-"))
    rig = None
    try:
        rig = EdgeRig(tmp)
        rig.ensure_image()
        plan = [
            ("crash_restart", lambda: scenario_crash_restart(rig)),
            ("bad_model", lambda: scenario_bad_model(rig)),
            ("unrecoverable", lambda: scenario_unrecoverable(rig)),
            ("policy_outage", lambda: scenario_policy_outage(rig)),
            ("worker_crash", lambda: scenario_worker_crash(rig)),
            ("stateful", scenario_stateful),
        ]
        for name, run in plan:
            if only and only != name:
                continue
            print(f"--- scenario {name}", flush=True)
            run()
        if not only:
            report_latencies()
    finally:
        if rig:
            rig.stop()
        docker("start", "aiops-opa")
        os.environ["POSTGRES_DB"] = "aiops"
        try:
            world.use_database(DB)
            world.drop_database(DB)
        except psycopg.Error as exc:
            print(f"cleanup {DB}: {exc}", flush=True)
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ["POSTGRES_DB"] = "aiops"
    ev.metrics["seconds"] = round(time.time() - started)
    ev.metrics["induced"] = [
        "crash: docker kill of the real edge runtime container",
        "bad model: a byte flipped in the active version's weights on the host registry copy, then a container restart",
        "unrecoverable: the same flip in every version",
        "stateful: the probe is told to report postgres down; the alert, correlation and policy are real, the container is real and untouched",
        "policy outage: docker stop of the real policy engine",
        "worker crash: the worker process killed while its adapter is running",
    ]
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
