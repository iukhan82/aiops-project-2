"""P10.04 acceptance evidence: device, stream, API, model, storage, certificate and configuration alert rules.

    python source-code/backend/aiops/verify_alerts.py                  # everything (about 35 minutes)
    ALERT_LAB_SKIP_LIVE=1 python source-code/backend/aiops/verify_alerts.py   # static + promtool only

A. Static. The rule file is well formed; every SLO-backed alert uses the SLO's own target and window; every metric a
   rule reads is one the code really emits (names derived from the source, in the form Prometheus stores them); the
   generated promtool test file is current.
B. Rule tests. `promtool check rules` and `promtool test rules` (a firing case and a healthy case per alert), then two
   mutation runs - every rule replaced by "never fires" and by "always fires" - and every alert must be caught by its
   own tests, so a test that cannot fail does not count.
C. Live, against the real running Prometheus. Real instruments (the API middleware, the ingestion, gateway and
   network-state counters and histograms), the real platform probe, a scratch database, real forged certificates and the
   real edge runtime container (genuine model, then a tampered one) are driven into each fault together. Each expected
   alert must reach `firing`, the alerts of a healthy platform must never fire, and after the faults are cleared every
   alert must resolve. What is induced rather than natural is stated in the evidence notes.
"""

from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import requests
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend import observability  # noqa: E402
from backend.aiops import platform_probe as probe  # noqa: E402
from backend.evidence import Evidence  # noqa: E402

PROM_URL = "http://127.0.0.1:9090"
PROM_DIR = SOURCE_ROOT / "infra" / "platform" / "observability" / "prometheus"
RULES = PROM_DIR / "rules" / "aiops-alerts.yml"
TESTS = PROM_DIR / "rule-tests" / "aiops-alerts.test.yml"
SLOS = SOURCE_ROOT / "backend" / "slos.json"
PROM_IMAGE = (
    "prom/prometheus:v3.7.3@sha256:49214755b6153f90a597adcbff0252cc61069f8ab69ce8411285cd4a560e8038"
)
LAB_DB = "aiops_alertlab"
EDGE_IMAGE = "aiops-edge-runtime:alertlab"
EDGE_NAME = "aiops-edge-runtime"
EDGE_OUT = SOURCE_ROOT / "models" / "evaluation" / "output"

CLASSES = {"device", "stream", "api", "model", "storage", "cert", "config", "pipeline"}
REQUIRED_CLASSES = CLASSES - {"pipeline"}

ev = Evidence("P10.04", "p10_04_alerts", docs_name="p10_04_alert_rules")


# ------------------------------------------------------------------------------------------------ docker plumbing
def _docker_argv(*args: str) -> list[str]:
    if shutil.which("docker"):
        return ["docker", *args]
    return ["wsl", "-e", "docker", *args]


def docker(*args: str, timeout: float = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        _docker_argv(*args),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _mnt(path: Path) -> str:
    """A Windows path as WSL sees it (`D:\\x` -> `/mnt/d/x`); unchanged on Linux."""
    if os.name != "nt":
        return str(path)
    text = str(path.resolve())
    return f"/mnt/{text[0].lower()}{text[2:].replace(chr(92), '/')}"


def promtool(*args: str, mount: Path) -> subprocess.CompletedProcess:
    return docker(
        "run",
        "--rm",
        "--entrypoint",
        "/bin/promtool",
        "-v",
        f"{_mnt(mount)}:/p:ro",
        PROM_IMAGE,
        *args,
    )


# ------------------------------------------------------------------------------------------------ A. static
def load_rules() -> dict[str, dict]:
    doc = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    return {r["alert"]: r for g in doc["groups"] for r in g["rules"]}


def expected_metric_names() -> set[str]:
    """Every Prometheus series name the code can produce, derived from the source, not typed here."""
    names: set[str] = set()

    def counter(name: str) -> None:
        names.add(name if name.endswith("_total") else f"{name}_total")

    def histogram(name: str, unit: str = "ms") -> None:
        suffix = {"ms": "_milliseconds"}.get(unit, "")
        names.add(f"{name}{suffix}_bucket")

    backend = SOURCE_ROOT / "backend"
    for path in backend.rglob("*.py"):
        if "verify_" in path.name or path.name == "observability.py":
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'BoundedCounter\(\s*"([a-z_]+)"', text):
            counter(match.group(1))
        for match in re.finditer(r'BoundedHistogram\(\s*"([a-z_]+)"(?:[^)]*unit="(\w+)")?', text):
            histogram(match.group(1), match.group(2) or "ms")
    # The ingestion outcome counters are built from the Outcome enum.
    ingest = (backend / "ingestion" / "ingest.py").read_text(encoding="utf-8")
    for match in re.finditer(r'^\s+[A-Z_]+ = "([a-z_]+)"$', ingest, re.MULTILINE):
        counter(f"ingestion_events_{match.group(1)}")
    # The API middleware names its instruments after the service it is added to.
    obs = (backend / "observability.py").read_text(encoding="utf-8")
    app = (backend / "api" / "app.py").read_text(encoding="utf-8")
    service = re.search(r'add_middleware\(HTTPTracingMiddleware, service_name="(\w+)"', app)
    if service and "_http_requests" in obs:
        counter(f"{service.group(1)}_http_requests")
        histogram(f"{service.group(1)}_http_request_duration")
    edge = (SOURCE_ROOT / "edge" / "runtime.py").read_text(encoding="utf-8")
    for match in re.finditer(r'm\.(counter|gauge|histogram)\(\s*"([a-z_]+)"', edge):
        kind, name = match.groups()
        names.add(f"{name}_bucket" if kind == "histogram" else name)
    for match in re.finditer(
        r'Sample\(\s*"(platform_[a-z_]+)"',
        (SOURCE_ROOT / "backend" / "aiops" / "platform_probe.py").read_text(encoding="utf-8"),
    ):
        names.add(match.group(1))
    names.update({"up"})
    return names


def rule_metric_names(expr: str) -> set[str]:
    prefixes = ("gateway_", "ingestion_", "network_state_", "api_http_", "edge_", "platform_")
    return {
        t
        for t in re.findall(r"[a-zA-Z_:][a-zA-Z0-9_:]*", expr)
        if t.startswith(prefixes) or t == "up"
    }


def static_checks() -> None:
    rules = load_rules()
    problems = []
    for name, rule in rules.items():
        labels, notes = rule.get("labels", {}), rule.get("annotations", {})
        if labels.get("severity") not in {"none", "warning", "critical"}:
            problems.append(f"{name}: severity")
        if labels.get("alert_class") not in CLASSES:
            problems.append(f"{name}: alert_class")
        if not {"summary", "description", "action"} <= set(notes):
            problems.append(f"{name}: annotations")
    ev.check(
        "every_rule_has_a_severity_a_known_class_and_summary_description_and_action",
        not problems,
        str(problems),
    )
    present = {r["labels"]["alert_class"] for r in rules.values()}
    ev.check(
        "all_seven_required_alert_classes_are_covered",
        REQUIRED_CLASSES <= present,
        f"{len(rules)} alerts; classes {sorted(present)}",
    )
    ev.metrics["alerts_by_class"] = {
        c: sorted(n for n, r in rules.items() if r["labels"]["alert_class"] == c)
        for c in sorted(present)
    }

    slos = {s["slo_id"]: s for s in json.loads(SLOS.read_text(encoding="utf-8"))["slos"]}
    wrong = []
    for name, rule in rules.items():
        slo_id = rule["labels"].get("slo")
        if not slo_id:
            continue
        slo = slos.get(slo_id)
        if slo is None:
            wrong.append(f"{name}: unknown SLO {slo_id}")
            continue
        target = int(slo["target_value"])
        window = f"[{slo['window_seconds'] // 60}m]"
        if not re.search(rf"[<>]=?\s*{target}\b", rule["expr"]):
            wrong.append(f"{name}: threshold is not the SLO target {target}")
        if window not in rule["expr"]:
            wrong.append(f"{name}: window is not the SLO window {window}")
    ev.check(
        "every_slo_backed_alert_uses_that_slos_own_target_and_window",
        not wrong,
        str(wrong)
        or f"{sum(1 for r in rules.values() if r['labels'].get('slo'))} alerts checked against slos.json",
    )

    known = expected_metric_names()
    unknown = {name: sorted(rule_metric_names(r["expr"]) - known) for name, r in rules.items()}
    unknown = {k: v for k, v in unknown.items() if v}
    ev.check(
        "every_metric_a_rule_reads_is_one_the_code_really_emits_in_the_form_prometheus_stores_it",
        not unknown,
        str(unknown) or f"{len(known)} emitted names derived from source",
    )

    check = subprocess.run(
        [sys.executable, str(PROM_DIR / "build_rule_tests.py"), "--check"],
        capture_output=True,
        text=True,
    )
    ev.check(
        "the_generated_promtool_test_file_is_current",
        check.returncode == 0,
        (check.stdout + check.stderr).strip(),
    )


# ------------------------------------------------------------------------------------------------ B. promtool
def promtool_checks() -> None:
    check = promtool("check", "rules", "/p/rules/aiops-alerts.yml", mount=PROM_DIR)
    rules = load_rules()
    ev.check(
        "promtool_check_rules_accepts_the_rule_file",
        check.returncode == 0 and f"{len(rules)} rules found" in check.stdout,
        check.stdout.strip().splitlines()[-1] if check.stdout.strip() else check.stderr[:200],
    )
    test = promtool("test", "rules", "/p/rule-tests/aiops-alerts.test.yml", mount=PROM_DIR)
    cases = len(yaml.safe_load(TESTS.read_text(encoding="utf-8"))["tests"])
    ev.check(
        "promtool_test_rules_passes_every_firing_and_healthy_case",
        test.returncode == 0,
        f"{cases} cases, {len(rules)} alerts"
        if test.returncode == 0
        else (test.stdout + test.stderr)[:400],
    )
    ev.metrics["rule_test_cases"] = cases

    with tempfile.TemporaryDirectory() as tmp:
        for label, replacement, exempt in (
            ("never_fires", "vector(0) > 1", set()),
            ("always_fires", "vector(1)", {"AlertPipelineWatchdog"}),
        ):
            work = Path(tmp) / label
            shutil.copytree(PROM_DIR / "rule-tests", work / "rule-tests")
            doc = yaml.safe_load(RULES.read_text(encoding="utf-8"))
            for group in doc["groups"]:
                for rule in group["rules"]:
                    rule["expr"] = replacement
                    rule.pop("for", None)
            (work / "rules").mkdir()
            (work / "rules" / "aiops-alerts.yml").write_text(yaml.safe_dump(doc), encoding="utf-8")
            run = promtool("test", "rules", "/p/rule-tests/aiops-alerts.test.yml", mount=work)
            caught = set(re.findall(r"alertname: (\w+)", run.stdout + run.stderr))
            survivors = sorted(set(rules) - exempt - caught)
            ev.check(
                f"mutation_every_rule_replaced_by_{label}_is_caught_by_that_alerts_own_tests",
                run.returncode != 0 and not survivors,
                f"{len(caught)} of {len(rules) - len(exempt)} caught; survivors {survivors}",
            )


# ------------------------------------------------------------------------------------------------ C. live
class Prom:
    def alerts(self) -> list[dict]:
        response = requests.get(f"{PROM_URL}/api/v1/alerts", timeout=10)
        return response.json()["data"]["alerts"]

    def state(self, name: str, **labels: str) -> str | None:
        states = {
            a["state"]
            for a in self.alerts()
            if a["labels"].get("alertname") == name
            and all(a["labels"].get(k) == v for k, v in labels.items())
        }
        return "firing" if "firing" in states else ("pending" if "pending" in states else None)

    def query(self, expr: str) -> list[dict]:
        response = requests.get(f"{PROM_URL}/api/v1/query", params={"query": expr}, timeout=10)
        return response.json()["data"]["result"]


def _utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def wait_for(condition, timeout: float, poll: float = 5.0) -> float | None:
    start = time.time()
    while time.time() - start < timeout:
        if condition():
            return time.time() - start
        time.sleep(poll)
    return None


def _cert(path: Path, valid_days: float) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    not_after = now + timedelta(days=valid_days)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, path.stem)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_after - timedelta(days=60))
        .not_valid_after(not_after)
        .sign(key, hashes.SHA256())
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


class Lab:
    """Everything the live run drives. Faults are switched on and off here; nothing touches the real `aiops` database."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.stop = threading.Event()
        self.probe_paused = threading.Event()
        self.mode = "healthy"  # traffic mode: healthy | faulty
        self.devices_fresh = True
        self.api_auth_mode = "oidc"
        self.threads: list[threading.Thread] = []
        os.environ["POSTGRES_DB"] = LAB_DB
        from database.migrate import dsn_from_env

        self.lab_dsn = dsn_from_env()
        self.broken_dsn = self.lab_dsn.replace("port=5432", "port=5439")
        self.certs_ok = tmp / "certs-ok"
        self.certs_bad = tmp / "certs-bad"
        self.policy_ok = probe.POLICY_DATA
        self.policy_bad = tmp / "policy-bad.json"
        data = json.loads(probe.POLICY_DATA.read_text(encoding="utf-8"))
        data["version"] = "0000000000000000"
        self.policy_bad.write_text(json.dumps(data), encoding="utf-8")
        self._make_certs()
        self.auth_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        threading.Thread(target=self.auth_server.serve_forever, daemon=True).start()
        self.auth_url = f"http://127.0.0.1:{self.auth_server.server_address[1]}/api/v1/health"
        self.cfg = probe.ProbeConfig(
            dsn=self.lab_dsn,
            platform_dir=self.certs_ok,
            policy_data=self.policy_ok,
            api_health_url=self.auth_url,
        )

    def _make_certs(self) -> None:
        for base, days in (
            (self.certs_ok, {}),
            (self.certs_bad, {"postgres_server": 2, "mqtt_server": 10, "device_a": -1}),
        ):
            _cert(base / "postgres/certs/ca.crt", 3650)
            _cert(base / "postgres/certs/server.crt", days.get("postgres_server", 365))
            _cert(base / "mosquitto/certs/ca.crt", 3650)
            _cert(base / "mosquitto/certs/server.crt", days.get("mqtt_server", 365))
            _cert(base / "mosquitto/certs/gateway.crt", 365)
            _cert(base / "mosquitto/certs/device-a.crt", days.get("device_a", 365))
            _cert(base / "mosquitto/certs/device-b.crt", 365)

    def _handler(self):
        lab = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - stdlib name
                body = json.dumps({"status": "ok", "auth_mode": lab.api_auth_mode}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):  # noqa: D401 - silence
                return

        return Handler

    # ---- background workers
    def start(self) -> None:
        for target in (self._probe_loop, self._traffic_loop, self._api_loop, self._device_loop):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self.threads.append(thread)

    def shutdown(self) -> None:
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=10)
        self.auth_server.shutdown()

    def _probe_loop(self) -> None:
        while not self.stop.is_set():
            if not self.probe_paused.is_set():
                try:
                    probe.publish(probe.collect(self.cfg))
                    observability.flush()
                except Exception as exc:  # noqa: BLE001 - a probe hiccup must not kill the lab
                    print(f"probe iteration failed: {exc}", flush=True)
            self.stop.wait(5)

    def _traffic_loop(self) -> None:
        """The real ingestion, gateway and network-state instruments, one second at a time."""
        from backend.ingestion import ingest as ing
        from backend.state import network_state as ns

        received = observability.BoundedCounter("gateway_events_received")
        delivered = observability.BoundedCounter("gateway_events_delivered")
        latency = ing._ingest_latency_histogram()  # noqa: SLF001 - the real instrument, not a copy
        while not self.stop.is_set():
            faulty = self.mode == "faulty"
            for i in range(10):
                received.add(1)
                delivered.add(1) if (not faulty or i == 0) else None
                if faulty and i == 0:
                    ing._outcome_counter("rejected_schema").add()  # noqa: SLF001
                else:
                    ing._outcome_counter("inserted").add()  # noqa: SLF001
                latency.record(3000 if faulty and i in (1, 2) else 50)
                ns._freshness_counter().add(  # noqa: SLF001
                    1, freshness_status="fresh" if (not faulty or i < 2) else "stale"
                )
            self.stop.wait(1)

    def _api_loop(self) -> None:
        """The real HTTP middleware on a small app: ok, failing and slow routes."""
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
        from starlette.testclient import TestClient

        app = FastAPI()
        app.add_middleware(observability.HTTPTracingMiddleware, service_name="api")

        @app.get("/ok")
        def ok():
            return {"ok": True}

        @app.get("/boom")
        def boom():
            return JSONResponse({"error": "induced"}, status_code=500)

        @app.get("/slow")
        async def slow():
            import asyncio

            await asyncio.sleep(4.0)
            return {"ok": True}

        client = TestClient(app)
        with ThreadPoolExecutor(max_workers=6) as pool:
            while not self.stop.is_set():
                faulty = self.mode == "faulty"
                for _ in range(9 if faulty else 10):
                    client.get("/ok")
                if faulty:
                    client.get("/boom")
                    pool.submit(lambda: TestClient(app).get("/slow"))
                self.stop.wait(1)

    def _device_loop(self) -> None:
        """Healthy: every device reports every 20 s. Faulty: half report with an hour-old reading, half never."""
        while not self.stop.is_set():
            try:
                with psycopg.connect(self.lab_dsn, autocommit=True, connect_timeout=5) as conn:
                    if self.devices_fresh:
                        conn.execute(_INSERT_OBSERVATIONS, {"age": "0 seconds"})
            except psycopg.Error as exc:
                print(f"device loop: {exc}", flush=True)
            self.stop.wait(20)


_INSERT_OBSERVATIONS = """
INSERT INTO observation_events (event_id, event_type, device_id, agency_scope, observation_time, ingest_time,
    sequence_number, clock_quality, geometry_version, location, intersection_id, corridor_id, lane_id, measurements,
    truth_label, privacy_classification, retention_class)
SELECT gen_random_uuid(), 'lab.device.reading', device_id, agency_scope, now() - %(age)s::interval, now(),
    (extract(epoch FROM now()))::bigint, 'synced', geometry_version, location, intersection_id, corridor_id, lane_id,
    '{}'::jsonb, 'simulated', 'none', 'short'
FROM devices WHERE status = 'active'
"""


# ---- fault switches -------------------------------------------------------------------------------------------
def induce(lab: Lab) -> None:
    lab.mode = "faulty"
    lab.devices_fresh = False
    with psycopg.connect(lab.lab_dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM observation_events")
        # Half the devices report an hour-old reading (stale), the rest never report at all.
        conn.execute(
            _INSERT_OBSERVATIONS.replace(
                "FROM devices WHERE status = 'active'",
                "FROM devices WHERE status = 'active' AND (hashtext(device_id) %% 2) = 0",
            ),
            {"age": "1 hour"},
        )
        conn.execute(
            "UPDATE devices SET certificate_not_after = now() + interval '5 days' "
            "WHERE device_type = (SELECT device_type FROM devices ORDER BY device_id LIMIT 1)"
        )
        conn.execute(
            "INSERT INTO retention_runs (run_at, storage_pressure, database_size_bytes, events_rolled_up, events_deleted, "
            "commands_deleted, incidents_deleted) VALUES (now() - interval '30 hours', true, 1, 0, 0, 0, 0)"
        )
        conn.execute(
            "UPDATE schema_migrations SET checksum = 'tampered' WHERE filename = '0001_topology.sql'"
        )
        conn.execute(
            "DELETE FROM schema_migrations WHERE filename = '0028_service_workload_roles.sql'"
        )
    lab.cfg.db_limit_bytes = 1_000_000
    lab.cfg.platform_dir = lab.certs_bad
    lab.cfg.policy_data = lab.policy_bad
    lab.api_auth_mode = "off"


def clear(lab: Lab) -> None:
    lab.mode = "healthy"
    with psycopg.connect(lab.lab_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE devices SET certificate_not_after = now() + interval '2 years' WHERE certificate_not_after IS NOT NULL"
        )
        conn.execute(
            "INSERT INTO retention_runs (run_at, storage_pressure, database_size_bytes, events_rolled_up, events_deleted, "
            "commands_deleted, incidents_deleted) VALUES (now(), false, 1, 0, 0, 0, 0)"
        )
        conn.execute(
            "UPDATE schema_migrations SET checksum = %s WHERE filename = '0001_topology.sql'",
            (
                next(
                    m.checksum
                    for m in probe.discover_migrations()
                    if m.filename == "0001_topology.sql"
                ),
            ),
        )
        m28 = next(
            m
            for m in probe.discover_migrations()
            if m.filename == "0028_service_workload_roles.sql"
        )
        conn.execute(
            "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (m28.filename, m28.checksum),
        )
        conn.execute(_INSERT_OBSERVATIONS, {"age": "0 seconds"})
    lab.devices_fresh = True
    lab.cfg.db_limit_bytes = probe.HARD_SIZE_BYTES
    lab.cfg.platform_dir = lab.certs_ok
    lab.cfg.policy_data = lab.policy_ok
    lab.api_auth_mode = "oidc"


# ---- the real edge runtime, scraped by Prometheus -----------------------------------------------------------------
class EdgeLab:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.registry_ok = SOURCE_ROOT / "models" / "registry"
        self.registry_bad = tmp / "registry-tampered"
        shutil.copytree(self.registry_ok, self.registry_bad)
        model = self.registry_bad / "traffic-safety-blockage" / "1.0.0" / "model.onnx"
        data = bytearray(model.read_bytes())
        data[len(data) // 2] ^= 0xFF
        model.write_bytes(bytes(data))
        with (EDGE_OUT / "benchmark_events.jsonl").open(encoding="utf-8") as handle:
            first = json.loads(handle.readline())["observation_time"]
        last = json.loads(_last_line(EDGE_OUT / "benchmark_events.jsonl"))["observation_time"]
        span = (
            datetime.fromisoformat(last.replace("Z", "+00:00"))
            - datetime.fromisoformat(first.replace("Z", "+00:00"))
        ).total_seconds()
        self.pace = max(span / 1800.0, 0.05)  # the replay lasts about 30 minutes per container

    def ensure_image(self) -> None:
        if docker("image", "inspect", EDGE_IMAGE).returncode != 0:
            # The Dockerfile only COPYs edge/ and contracts/; a context of just those is seconds, not the whole tree.
            context = self.tmp / "edge-build-context"
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
                EDGE_IMAGE,
                _mnt(context),
                timeout=1800,
            )
            if build.returncode != 0:
                raise RuntimeError(f"edge image build failed: {build.stderr[-500:]}")

    def start(self, registry: Path) -> None:
        docker("rm", "-f", EDGE_NAME)
        run = docker(
            "run",
            "-d",
            "--name",
            EDGE_NAME,
            "--network",
            "aiops-platform-net",
            "--user",
            "10001:10001",
            "--cpus=0.75",
            "--memory=512m",
            "--memory-swap=512m",
            "--pids-limit=64",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--read-only",
            "--tmpfs",
            "/tmp",
            "-v",
            f"{_mnt(EDGE_OUT / 'devices.jsonl')}:/data/devices.jsonl:ro",
            "-v",
            f"{_mnt(registry)}:/data/registry:ro",
            "-v",
            f"{_mnt(EDGE_OUT / 'benchmark_config.json')}:/data/config.json:ro",
            "-v",
            f"{_mnt(EDGE_OUT / 'benchmark_events.jsonl')}:/data/events.jsonl:ro",
            EDGE_IMAGE,
            "replay",
            "--config",
            "/data/config.json",
            "--events",
            "/data/events.jsonl",
            "--pace",
            f"{self.pace:.4f}",
            "--health-host",
            "0.0.0.0",
            "--health-port",
            "9100",
        )
        if run.returncode != 0:
            raise RuntimeError(f"edge container did not start: {run.stderr[-400:]}")

    def stop(self) -> None:
        docker("rm", "-f", EDGE_NAME)


def _last_line(path: Path) -> str:
    with path.open("rb") as handle:
        handle.seek(-4096, os.SEEK_END)
        return handle.read().decode("utf-8").strip().splitlines()[-1]


EXPECT_FIRING: list[tuple[str, dict[str, str]]] = [
    ("DevicesSilent", {}),
    ("DeviceTypeOutage", {}),
    ("DeviceCertificateExpiring", {}),
    ("DatabaseSizeNearLimit", {}),
    ("DatabaseSizeCritical", {}),
    ("RetentionOverdue", {}),
    ("RetentionUnderStoragePressure", {}),
    ("CertificateExpiringSoon", {"service_name": "mqtt-broker"}),
    ("CertificateExpiryCritical", {"service_name": "postgres"}),
    ("CertificateExpiryCritical", {"service_name": "mqtt-devices"}),
    ("PolicyVersionDrift", {}),
    ("MigrationDrift", {}),
    ("ApiAuthenticationNotEnforced", {}),
    ("GatewayDeliveryStalled", {}),
    ("IngestionRejectionRateHigh", {}),
    ("IngestionLatencyP95High", {}),
    ("NetworkStateFreshnessLow", {}),
    ("ApiErrorRateHigh", {}),
    ("ApiLatencyP95High", {}),
    ("EdgeModelInactive", {}),
    ("EdgeModelErrors", {"code": "integrity_mismatch"}),
]
MUST_STAY_QUIET = [
    "PlatformProbeStale",
    "PlatformProbeMissing",
    "PlatformTargetDown",
    "EdgeRuntimeDown",
    "EdgeModelLatencyP95High",
    "EdgePendingDevicesSaturated",
]


def key(name: str, labels: dict[str, str]) -> str:
    return name + (
        "{" + ",".join(f"{k}={v}" for k, v in sorted(labels.items())) + "}" if labels else ""
    )


def live_checks() -> None:
    prom = Prom()
    ev.check(
        "prometheus_is_reachable_and_evaluating_the_watchdog",
        prom.state("AlertPipelineWatchdog") == "firing",
    )

    subprocess.run(_docker_argv("kill", "-s", "HUP", "aiops-prometheus"), capture_output=True)
    time.sleep(5)
    loaded = requests.get(f"{PROM_URL}/api/v1/rules", timeout=10).json()["data"]["groups"]
    live_rules = {r["name"]: r for g in loaded for r in g["rules"] if r["type"] == "alerting"}
    wanted = load_rules()
    deployed = all(
        name in live_rules
        and re.sub(r"\s+", "", live_rules[name]["query"]) == re.sub(r"\s+", "", str(rule["expr"]))
        for name, rule in wanted.items()
    )
    ev.check(
        "the_running_prometheus_has_loaded_exactly_the_rule_file_expressions",
        deployed and all(r["health"] == "ok" for r in live_rules.values()),
        f"{len(live_rules)} alerting rules loaded, all healthy",
    )

    # A previous run's series stay readable for the 5 minute lookback and keep its alerts alive; starting on top of them
    # would make a "fires" or "resolves" result mean nothing. With nothing running, exactly the three "nothing reports"
    # alerts stand (no probe, no edge runtime); anything else, or a probe series still readable, is left over.
    idle_alerts = {"AlertPipelineWatchdog", "PlatformProbeMissing", "EdgeRuntimeDown"}

    def leftovers() -> list[str]:
        alerts = {a["labels"]["alertname"] for a in prom.alerts()} - idle_alerts
        series = [
            name
            for name in (
                "platform_probe_success_timestamp_seconds",
                "platform_probe_target_up",
                "edge_model_active",
            )
            if prom.query(name)
        ]
        return sorted(alerts) + series

    quiet = wait_for(lambda: not leftovers(), 900, poll=15)
    ev.check(
        "nothing_from_an_earlier_run_is_still_alerting_or_readable_when_the_lab_starts",
        quiet is not None,
        f"clean after {round(quiet)} s" if quiet is not None else f"left over: {leftovers()}",
    )

    os.environ.setdefault(
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", f"{PROM_URL}/api/v1/otlp/v1/metrics"
    )
    observability.configure("aiops-alertlab")
    tmp = Path(tempfile.mkdtemp(prefix="alertlab-"))
    lab = edge = None
    timing: dict[str, float] = {}
    try:
        from backend.demo import world

        world.load_platform_env()
        world.ensure(LAB_DB, reset=True)
        lab = Lab(tmp)
        with psycopg.connect(lab.lab_dsn, autocommit=True) as conn:
            conn.execute(_INSERT_OBSERVATIONS, {"age": "0 seconds"})
            device_count = conn.execute(
                "SELECT count(*) FROM devices WHERE status = 'active'"
            ).fetchone()[0]
        ev.metrics["lab_devices"] = device_count

        edge = EdgeLab(tmp)
        edge.ensure_image()
        edge.start(edge.registry_ok)
        window = {"lab_started": _utc()}
        lab.start()

        # ---- P0a: the probe cannot reach its database
        ok = wait_for(
            lambda: (
                prom.state("PlatformProbeMissing") is None
                and bool(prom.query('platform_probe_target_up{service_name="postgres"} == 1'))
            ),
            180,
        )
        ev.check("the_probe_is_live_and_reports_postgres_up_before_any_fault", ok is not None)
        lab.cfg.dsn = lab.broken_dsn
        t = wait_for(
            lambda: prom.state("PlatformTargetDown", service_name="postgres") == "firing", 240
        )
        ev.check(
            "live_PlatformTargetDown_fires_when_the_probe_cannot_reach_postgres",
            t is not None,
            f"{t and round(t)} s",
        )
        lab.cfg.dsn = lab.lab_dsn
        t = wait_for(lambda: prom.state("PlatformTargetDown") is None, 180)
        ev.check(
            "live_PlatformTargetDown_resolves_when_postgres_answers_again",
            t is not None,
            f"{t and round(t)} s",
        )

        # ---- P0b: the probe itself goes silent
        lab.probe_paused.set()
        t = wait_for(lambda: prom.state("PlatformProbeStale") == "firing", 300)
        ev.check(
            "live_PlatformProbeStale_fires_when_the_probe_stops_reporting",
            t is not None,
            f"{t and round(t)} s",
        )
        lab.probe_paused.clear()
        t = wait_for(lambda: prom.state("PlatformProbeStale") is None, 180)
        ev.check(
            "live_PlatformProbeStale_resolves_when_the_probe_reports_again",
            t is not None,
            f"{t and round(t)} s",
        )

        # ---- baseline: a healthy platform raises none of the alerts under test
        time.sleep(150)
        firing_now = sorted(
            {a["labels"]["alertname"] for a in prom.alerts() if a["state"] == "firing"}
            - {"AlertPipelineWatchdog"}
        )
        edge_up = bool(prom.query('up{job="edge-runtime"} == 1')) and bool(
            prom.query("edge_model_active == 1")
        )
        ev.check(
            "healthy_baseline_no_alert_fires_and_the_real_edge_runtime_serves_its_model",
            not firing_now and edge_up,
            f"firing: {firing_now}; edge up and model active: {edge_up}",
        )

        # ---- phase 1: every fault at once
        window["baseline_checked"] = _utc()
        edge.start(edge.registry_bad)
        induce(lab)
        window["induced"] = _utc()
        started = time.time()
        pending = {key(n, lb): (n, lb) for n, lb in EXPECT_FIRING}
        quiet_breaches: set[str] = set()
        deadline = started + 660
        while pending and time.time() < deadline:
            for k, (n, lb) in list(pending.items()):
                if prom.state(n, **lb) == "firing":
                    timing[k] = round(time.time() - started)
                    del pending[k]
            for name in MUST_STAY_QUIET:
                if prom.state(name) == "firing":
                    quiet_breaches.add(name)
            time.sleep(10)
        for n, lb in EXPECT_FIRING:
            k = key(n, lb)
            ev.check(
                f"live_{n}_fires_on_its_induced_fault",
                k in timing,
                f"after {timing.get(k)} s" if k in timing else "never fired",
            )
        ev.check(
            "live_the_alerts_that_must_not_fire_while_the_probe_edge_and_database_are_alive_stayed_quiet",
            not quiet_breaches,
            str(sorted(quiet_breaches)) or "none fired",
        )
        ev.metrics["seconds_to_fire"] = timing

        # ---- phase 2: clear the faults
        edge.start(edge.registry_ok)
        clear(lab)
        window["cleared"] = _utc()
        cleared_start = time.time()
        still = {key(n, lb): (n, lb) for n, lb in EXPECT_FIRING}
        resolve_time: dict[str, int] = {}
        deadline = cleared_start + 720
        while still and time.time() < deadline:
            for k, (n, lb) in list(still.items()):
                if prom.state(n, **lb) is None:
                    resolve_time[k] = round(time.time() - cleared_start)
                    del still[k]
            time.sleep(10)
        ev.check(
            "live_every_induced_alert_resolves_after_the_fault_is_cleared",
            not still,
            f"unresolved: {sorted(still)}"
            if still
            else f"all resolved, slowest {max(resolve_time.values())} s",
        )
        ev.metrics["seconds_to_resolve"] = resolve_time
        window["finished"] = _utc()
        ev.metrics["lab_window_utc"] = window
        ev.metrics["lab_prometheus_job"] = "aiops-alertlab"
    finally:
        if lab:
            lab.shutdown()
        if edge:
            edge.stop()
        try:
            os.environ["POSTGRES_DB"] = "aiops"
            with psycopg.connect(
                f"host=127.0.0.1 port=5432 dbname=aiops user={os.environ['POSTGRES_USER']} password={os.environ['POSTGRES_PASSWORD']}",
                autocommit=True,
            ) as conn:
                conn.execute(f"DROP DATABASE IF EXISTS {LAB_DB} WITH (FORCE)")
        except (psycopg.Error, KeyError) as exc:
            print(f"lab database cleanup: {exc}", flush=True)
        shutil.rmtree(tmp, ignore_errors=True)

    ev.metrics["induced_not_natural"] = [
        "device silence/staleness: rows written into a scratch database, read by the real probe",
        "certificates: real X.509 files with chosen notAfter dates in a temporary directory",
        "storage: the size limit the probe compares against is lowered to 1 MB",
        "config: a wrong policy version file, an altered migration checksum, a health endpoint that reports auth_mode=off",
        "stream/API: the real instrument objects driven at a chosen error, rejection and latency mix",
        "model: the real edge runtime container started once with a genuine model and once with a byte-flipped one",
        "NOT induced live: EdgeModelLatencyP95High and EdgePendingDevicesSaturated (promtool-tested; live they stayed quiet on a healthy runtime)",
    ]


def main() -> int:
    static_checks()
    promtool_checks()
    if os.environ.get("ALERT_LAB_SKIP_LIVE"):
        ev.notes["live"] = "skipped by ALERT_LAB_SKIP_LIVE"
        return ev.finish()
    live_checks()
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
