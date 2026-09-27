"""P11.02: generate `workloads.yaml`, the least-privilege Kubernetes workloads of the platform, from one compact specification.

    python source-code/infra/k8s/generate_workloads.py            # rewrite workloads.yaml
    python source-code/infra/k8s/generate_workloads.py --check    # fail if the file on disk is not what this produces

The specification is the single place a workload's image, user, ports, probes, resources, volumes and environment are written; the
generated YAML is checked by `verify_workloads.py` against the Kubernetes Pod Security `restricted` profile, the ServiceAccounts and
NetworkPolicies of `rbac-and-network-policy.yaml`, and the compose stack it mirrors. Nothing is applied here.

Third-party images (Postgres, Kafka, Keycloak, OPA, the broker, the observability stack) are read from
`infra/platform/docker-compose.yml`, so a digest is pinned in one place. The platform's own images are `aiops-backend` and
`aiops-frontend` (P11.01); their tag is the release version and the deployment substitutes the digest recorded in the release
provenance. No workload here holds a secret: passwords, keys and certificates are Secret references (`aiops-secrets`, `aiops-tls-*`)
that the deployment step creates from the git-ignored files, never from this repository.

Deliberately NOT here, because a container cannot do it under least privilege: the remediation worker (its docker adapter needs the
container runtime; a Kubernetes adapter with a Role scoped to `patch` on named Deployments is future work) and the demo feeder.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bundles  # noqa: E402

POLICIES = HERE / "rbac-and-network-policy.yaml"
COMPOSE = HERE.parent / "platform" / "docker-compose.yml"
OUTPUT = HERE / "workloads.yaml"
JOBS = HERE / "jobs.yaml"
NAMESPACE = "aiops"
VERSION = "0.1.0"
BACKEND = f"aiops-backend:{VERSION}"
FRONTEND = f"aiops-frontend:{VERSION}"
EDGE = f"aiops-edge:{VERSION}"
PYTHON_UID = 10002  # backend/Dockerfile
NGINX_UID = 101
EDGE_UID = 10001  # edge/Dockerfile

ENV_DB = [
    {"name": "POSTGRES_HOST", "value": "aiops-postgres"},
    {"name": "POSTGRES_PORT", "value": "5432"},
    {"name": "POSTGRES_DB", "value": "aiops"},
    {
        "name": "POSTGRES_USER",
        "valueFrom": {"secretKeyRef": {"name": "aiops-secrets", "key": "postgres-user"}},
    },
    {
        "name": "POSTGRES_PASSWORD",
        "valueFrom": {"secretKeyRef": {"name": "aiops-secrets", "key": "postgres-password"}},
    },
]
ROLE_SECRETS_PATH = "/srv/repo/source-code/infra/platform/output/service_role_secrets.json"
ROLE_VOLUMES = [
    {"name": "service-roles", "secret": {"secretName": "aiops-service-roles", "defaultMode": 0o440}}
]
ROLE_MOUNTS = [
    {
        "name": "service-roles",
        "mountPath": ROLE_SECRETS_PATH,
        "subPath": "service_role_secrets.json",
        "readOnly": True,
    }
]
ENV_OPA = [{"name": "OPA_URL", "value": "http://aiops-opa:8181"}]
ENV_OTEL = [
    {"name": "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "value": "http://aiops-tempo:4318/v1/traces"},
    {
        "name": "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "value": "http://aiops-prometheus:9090/api/v1/otlp/v1/metrics",
    },
]
ENV_KEYCLOAK = [
    {"name": "KEYCLOAK_ISSUER", "value": "http://aiops-keycloak:8080/realms/aiops"},
    {"name": "KEYCLOAK_AUDIENCE", "value": "aiops-api"},
    # the API fetches the signing keys from here (it defaults to the workstation's 127.0.0.1:8180: found on the target, where every request was a 503)
    {
        "name": "KEYCLOAK_JWKS_URL",
        "value": "http://aiops-keycloak:8080/realms/aiops/protocol/openid-connect/certs",
    },
]


@dataclass
class Workload:
    name: str  # also the app.kubernetes.io/name label and the ServiceAccount name
    image: str
    uid: int
    kind: str = "Deployment"
    args: list[str] = field(default_factory=list)
    command: list[str] | None = None
    ports: dict[str, int] = field(default_factory=dict)
    env: list[dict] = field(default_factory=list)
    cpu: str = "250m"
    memory: str = "256Mi"
    probe: dict | None = None  # {"http": path, "port": n} or {"tcp": port}
    tmp_dirs: tuple[str, ...] = ("/tmp",)
    volumes: list[dict] = field(default_factory=list)  # extra volumes
    mounts: list[dict] = field(default_factory=list)
    pvc: tuple[str, str, str] | None = None  # (mount path, size, volume name)
    service: bool = True
    startup_seconds: int = 30
    note: str = ""
    extra_containers: list[dict] = field(default_factory=list)
    rolling: bool = False  # RollingUpdate with no unavailable pod: a bad image never takes the service down (stateless, no claim, safe to run twice for a moment)
    scratch: tuple[
        tuple[str, str, str], ...
    ] = ()  # (volume name, mount path, size limit): a writable emptyDir
    seed: tuple[str, str] | None = (
        None  # (scratch volume, image path): copy that path from the image into the volume before start
    )


def compose_images() -> dict[str, str]:
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return {name: svc["image"] for name, svc in doc["services"].items()}


def specs() -> list[Workload]:
    img = compose_images()
    http = lambda path, port: {"http": path, "port": port}  # noqa: E731
    return [
        Workload("aiops-api", BACKEND, PYTHON_UID, args=["backend/api/serve.py", "--host", "0.0.0.0", "--port", "8100", "--database", "aiops"],
                 ports={"http": 8100}, env=ENV_DB + ENV_OPA + ENV_OTEL + ENV_KEYCLOAK + [{"name": "AIOPS_AUTH_MODE", "value": "oidc"}],
                 probe=http("/api/v1/health", 8100), note="operator API", rolling=True),
        Workload("aiops-scenario-control", BACKEND, PYTHON_UID, args=["backend/scenario_control/serve.py", "--host", "0.0.0.0", "--port", "8101", "--database", "aiops"],
                 ports={"http": 8101}, env=ENV_DB + ENV_OPA + ENV_OTEL + ENV_KEYCLOAK, probe={"tcp": 8101}, note="demo-only scenario control, no operational authority",
                 volumes=ROLE_VOLUMES, mounts=ROLE_MOUNTS, rolling=True),
        Workload("aiops-command-executor", BACKEND, PYTHON_UID, args=["backend/control/executor_worker.py", "--database", "aiops"],
                 env=ENV_DB + ENV_OPA + ENV_OTEL, service=False, note="the only identity that executes an approved command", volumes=ROLE_VOLUMES, mounts=ROLE_MOUNTS),
        Workload("aiops-outcome-verifier", BACKEND, PYTHON_UID, args=["backend/control/verifier_worker.py", "--database", "aiops"],
                 env=ENV_DB + ENV_OPA + ENV_OTEL, service=False, note="independent outcome verification", volumes=ROLE_VOLUMES, mounts=ROLE_MOUNTS),
        Workload("aiops-ingestion-gateway", BACKEND, PYTHON_UID, args=["backend/ingestion/ingest.py"],
                 env=ENV_DB + ENV_OTEL + [{"name": "KAFKA_BOOTSTRAP", "value": "aiops-kafka-broker:9092"}], service=False,
                 note="central ingestion (Kafka to PostgreSQL)"),
        Workload("aiops-mqtt-gateway", BACKEND, PYTHON_UID, kind="StatefulSet", args=["backend/gateway/serve.py"],
                 env=ENV_OTEL + [{"name": "MQTT_HOST", "value": "aiops-mqtt-broker"}, {"name": "MQTT_PORT", "value": "8883"},
                                 {"name": "MQTT_CAFILE", "value": "/certs/ca.crt"}, {"name": "MQTT_CERTFILE", "value": "/certs/tls.crt"}, {"name": "MQTT_KEYFILE", "value": "/certs/tls.key"},
                                 {"name": "KAFKA_BOOTSTRAP", "value": "aiops-kafka-broker:9092"}, {"name": "GATEWAY_OUTBOX", "value": "/data/outbox.sqlite3"}],
                 volumes=[{"name": "tls", "secret": {"secretName": "aiops-tls-gateway", "defaultMode": 0o440}}],
                 mounts=[{"name": "tls", "mountPath": "/certs", "readOnly": True}], pvc=("/data", "1Gi", "data"), service=False,
                 note="MQTT-to-Kafka gateway: the only client that reads every device's telemetry (its own certificate, CN gateway); a durable outbox between the two"),
        Workload("aiops-edge-runtime", EDGE, EDGE_UID,
                 args=["replay", "--config", "/site/config.json", "--events", "/site/events.jsonl", "--pace", "0.01", "--health-host", "0.0.0.0", "--health-port", "9100"],
                 ports={"metrics": 9100}, cpu="500m", memory="384Mi", probe=http("/readyz", 9100),
                 volumes=[{"name": "site", "configMap": {"name": "aiops-edge-site"}}, {"name": "model", "configMap": {"name": "aiops-edge-model"}}],
                 mounts=[{"name": "site", "mountPath": "/site", "readOnly": True}, {"name": "model", "mountPath": "/model", "readOnly": True}],
                 note="one edge site replaying a recorded ten-minute run at one hundredth of real time, serving its health and /metrics; its outbox has no drain to MQTT in this repository yet (backend/topology.json says so)"),
        Workload("aiops-platform-correlator", BACKEND, PYTHON_UID, args=["backend/aiops/platform_correlator.py", "--interval", "15"],
                 env=ENV_DB + [{"name": "AIOPS_PROMETHEUS_URL", "value": "http://aiops-prometheus:9090"}], service=False, note="platform incident correlation (P10.07)",
                 volumes=ROLE_VOLUMES, mounts=ROLE_MOUNTS),
        Workload("aiops-platform-probe", BACKEND, PYTHON_UID, args=["backend/aiops/platform_probe.py", "--interval", "15"],
                 env=ENV_DB + ENV_OTEL + ENV_OPA + [{"name": "AIOPS_API_HEALTH_URL", "value": "http://aiops-api:8100/api/v1/health"}], service=False, note="platform gauges for the alert rules (P10.04)"),
        Workload("aiops-frontend", FRONTEND, NGINX_UID, ports={"http": 8080}, probe=http("/index.html", 8080), cpu="100m", memory="64Mi",
                 tmp_dirs=("/tmp", "/var/cache/nginx"), note="operations console (static files behind an unprivileged nginx)", rolling=True),
        Workload("aiops-opa", img["opa"], 1000, args=["run", "--server", "--addr=0.0.0.0:8181", "--log-level=info", "--log-format=json",
                 "--set=decision_logs.console=true", "--bundle", "/policy/bundle.tar.gz"],
                 ports={"http": 8181}, cpu="250m", memory="128Mi", probe={"tcp": 8181}, tmp_dirs=(),
                 volumes=[{"name": "policy", "configMap": {"name": "aiops-policy"}}], mounts=[{"name": "policy", "mountPath": "/policy", "readOnly": True}],
                 note="policy decision point; the bundle is one archive in the aiops-policy ConfigMap, generated from policy/", rolling=True),
        Workload("aiops-postgres", img["postgres"], 999, kind="StatefulSet",
                 args=["postgres", "-c", "ssl=on", "-c", "ssl_cert_file=/certs/server.crt", "-c", "ssl_key_file=/certs/server.key", "-c", "ssl_ca_file=/certs/ca.crt",
                       "-c", "hba_file=/etc/postgres-hba/pg_hba.conf"],
                 ports={"postgres": 5432}, cpu="750m", memory="1024Mi", probe={"tcp": 5432}, tmp_dirs=("/tmp", "/var/run/postgresql"),
                 env=[{"name": "POSTGRES_DB", "value": "aiops"},
                      {"name": "POSTGRES_USER", "valueFrom": {"secretKeyRef": {"name": "aiops-secrets", "key": "postgres-user"}}},
                      {"name": "POSTGRES_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "aiops-secrets", "key": "postgres-password"}}},
                      {"name": "PGDATA", "value": "/var/lib/postgresql/data/pgdata"}],
                 volumes=[{"name": "tls", "secret": {"secretName": "aiops-tls-postgres", "defaultMode": 0o440}}, {"name": "hba", "configMap": {"name": "aiops-postgres-hba"}}],
                 mounts=[{"name": "tls", "mountPath": "/certs", "readOnly": True}, {"name": "hba", "mountPath": "/etc/postgres-hba", "readOnly": True}],
                 pvc=("/var/lib/postgresql/data", "10Gi", "data"), startup_seconds=60,
                 note="PostGIS, TLS only (pg_hba refuses a plain connection); server.crt, server.key and ca.crt come from the aiops-tls-postgres Secret"),
        Workload("aiops-kafka-broker", img["kafka-broker"], 101, kind="StatefulSet",
                 args=["redpanda", "start", "--mode", "dev-container", "--smp", "1", "--memory", "800M", "--reserve-memory", "0M", "--node-id", "0", "--check=false",
                       "--kafka-addr", "internal://0.0.0.0:9092", "--advertise-kafka-addr", "internal://aiops-kafka-broker:9092", "--rpc-addr", "127.0.0.1:33145", "--advertise-rpc-addr", "127.0.0.1:33145"],
                 ports={"kafka": 9092, "admin": 9644}, cpu="750m", memory="1024Mi", probe=http("/v1/status/ready", 9644), pvc=("/var/lib/redpanda/data", "5Gi", "data"), tmp_dirs=("/tmp", "/etc/redpanda"),
                 startup_seconds=60, note="Redpanda (Kafka API), dev-container mode as in the compose stack: one node, no replication"),
        Workload("aiops-mqtt-broker", img["mqtt-broker"], 1883, kind="StatefulSet", command=["/usr/sbin/mosquitto"], args=["-c", "/mosquitto/config/mosquitto.conf"],
                 ports={"mqtts": 8883}, cpu="150m", memory="192Mi", probe={"tcp": 8883},
                 volumes=[{"name": "config", "configMap": {"name": "aiops-mosquitto"}}, {"name": "tls", "secret": {"secretName": "aiops-tls-mqtt", "defaultMode": 0o440}}],
                 mounts=[{"name": "config", "mountPath": "/mosquitto/config", "readOnly": True}, {"name": "tls", "mountPath": "/mosquitto/certs", "readOnly": True}],
                 pvc=("/mosquitto/data", "1Gi", "data"), note="MQTT broker with per-device mTLS and default-deny topic ACLs (CTL-09); no plain listener"),
        Workload("aiops-keycloak", img["keycloak"], 1000, kind="StatefulSet",
                 args=["start-dev", "--import-realm", "--http-enabled=true", "--hostname-strict=false", "--health-enabled=true"],
                 ports={"http": 8080, "management": 9000}, cpu="1000m", memory="1024Mi", probe=http("/health/ready", 9000), startup_seconds=120,
                 env=[{"name": "KC_BOOTSTRAP_ADMIN_USERNAME", "valueFrom": {"secretKeyRef": {"name": "aiops-secrets", "key": "keycloak-admin"}}},
                      {"name": "KC_BOOTSTRAP_ADMIN_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "aiops-secrets", "key": "keycloak-admin-password"}}}],
                 volumes=[{"name": "realm", "configMap": {"name": "aiops-realm"}}], mounts=[{"name": "realm", "mountPath": "/opt/keycloak/data/import", "readOnly": True}],
                 pvc=("/opt/keycloak/data", "1Gi", "data"), tmp_dirs=("/tmp",),
                 scratch=(("quarkus", "/opt/keycloak/lib/quarkus", "512Mi"),), seed=("quarkus", "/opt/keycloak/lib/quarkus"),
                 note="identity provider in start-dev mode (embedded H2 database) as in the compose stack: NOT a production Keycloak"),
        Workload("aiops-prometheus", img["prometheus"], 65534, kind="StatefulSet",
                 args=["--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.path=/prometheus", "--storage.tsdb.retention.time=24h", "--web.enable-otlp-receiver"],
                 ports={"http": 9090}, cpu="200m", memory="384Mi", probe=http("/-/ready", 9090), pvc=("/prometheus", "5Gi", "data"),
                 volumes=[{"name": "config", "configMap": {"name": "aiops-prometheus", "items": bundles.items("aiops-prometheus")}}], mounts=[{"name": "config", "mountPath": "/etc/prometheus", "readOnly": True}],
                 note="metrics, OTLP receiver and the P10.04 alert rules (ConfigMap aiops-prometheus: prometheus.yml and rules/)"),
        Workload("aiops-tempo", img["tempo"], 10001, kind="StatefulSet", args=["-config.file=/etc/tempo/tempo.yaml"],
                 ports={"http": 3200, "otlp-http": 4318}, cpu="200m", memory="768Mi", probe=http("/ready", 3200), pvc=("/var/tempo", "2Gi", "data"),
                 volumes=[{"name": "config", "configMap": {"name": "aiops-tempo"}}], mounts=[{"name": "config", "mountPath": "/etc/tempo", "readOnly": True}], note="traces"),
        Workload("aiops-loki", img["loki"], 10001, kind="StatefulSet", args=["-config.file=/etc/loki/loki.yaml"],
                 ports={"http": 3100}, cpu="150m", memory="256Mi", probe=http("/ready", 3100), pvc=("/loki", "2Gi", "data"),
                 volumes=[{"name": "config", "configMap": {"name": "aiops-loki"}}], mounts=[{"name": "config", "mountPath": "/etc/loki", "readOnly": True}], note="logs"),
        Workload("aiops-grafana", img["grafana"], 472, kind="StatefulSet", ports={"http": 3000}, cpu="200m", memory="512Mi", probe=http("/api/health", 3000),
                 pvc=("/var/lib/grafana", "1Gi", "data"),
                 env=[{"name": "GF_SECURITY_ADMIN_USER", "value": "admin"},
                      {"name": "GF_SECURITY_ADMIN_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "aiops-secrets", "key": "grafana-admin-password"}}},
                      {"name": "GF_AUTH_ANONYMOUS_ENABLED", "value": "false"}, {"name": "GF_USERS_ALLOW_SIGN_UP", "value": "false"}],
                 volumes=[{"name": "provisioning", "configMap": {"name": "aiops-grafana-provisioning", "items": bundles.items("aiops-grafana-provisioning")}}, {"name": "dashboards", "configMap": {"name": "aiops-grafana-dashboards"}}],
                 mounts=[{"name": "provisioning", "mountPath": "/etc/grafana/provisioning", "readOnly": True}, {"name": "dashboards", "mountPath": "/etc/grafana/dashboards", "readOnly": True}],
                 note="dashboards; no anonymous access, no sign-up"),
        Workload("aiops-migrate", BACKEND, PYTHON_UID, kind="Job", args=["database/migrate.py"], env=ENV_DB, cpu="250m", memory="192Mi", service=False, tmp_dirs=("/tmp",),
                 note="applies the ordered, checksummed migrations as the database owner, once per release"),
        Workload("aiops-load", BACKEND, PYTHON_UID, kind="Job", args=["backend/load_check.py"], cpu="1000m", memory="512Mi", service=False,
                 env=ENV_DB + [{"name": "MQTT_HOST", "value": "aiops-mqtt-broker"}, {"name": "MQTT_PORT", "value": "8883"}],
                 volumes=[{"name": "devices", "secret": {"secretName": "aiops-tls-devices", "defaultMode": 0o440}}], mounts=[{"name": "devices", "mountPath": "/devices", "readOnly": True}],
                 note="the load generator: real mTLS device clients publish at a chosen rate and the run counts what reaches PostgreSQL; run on demand by verify_load_target.py"),
        Workload("aiops-security", BACKEND, PYTHON_UID, kind="Job", args=["backend/target_security.py", "main"], cpu="250m", memory="256Mi", service=False,
                 env=ENV_DB + ENV_OPA + [{"name": "MQTT_HOST", "value": "aiops-mqtt-broker"}, {"name": "MQTT_PORT", "value": "8883"}, {"name": "API_URL", "value": "http://aiops-api:8100"},
                                {"name": "KEYCLOAK_BASE_URL", "value": "http://aiops-keycloak:8080"}, {"name": "KEYCLOAK_PUBLIC_URL", "value": "http://aiops-keycloak:8080"},
                                {"name": "KC_ADMIN", "valueFrom": {"secretKeyRef": {"name": "aiops-secrets", "key": "keycloak-admin"}}},
                                {"name": "KC_ADMIN_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "aiops-secrets", "key": "keycloak-admin-password"}}}],
                 volumes=[{"name": "devices", "secret": {"secretName": "aiops-tls-devices", "defaultMode": 0o440}}], mounts=[{"name": "devices", "mountPath": "/devices", "readOnly": True}],
                 note="the in-cluster security checks (transport, identity, the API's enforcement matrix, the policy engine); run on demand by verify_security_target.py"),
        Workload("aiops-e2e", BACKEND, PYTHON_UID, kind="Job", args=["backend/e2e_check.py"], cpu="250m", memory="256Mi", service=False,
                 env=ENV_DB + [{"name": "MQTT_HOST", "value": "aiops-mqtt-broker"}, {"name": "MQTT_PORT", "value": "8883"}, {"name": "API_URL", "value": "http://aiops-api:8100"},
                                {"name": "KEYCLOAK_URL", "value": "http://aiops-keycloak:8080"}, {"name": "KAFKA_BOOTSTRAP", "value": "aiops-kafka-broker:9092"}],
                 volumes=[{"name": "devices", "secret": {"secretName": "aiops-tls-devices", "defaultMode": 0o440}}], mounts=[{"name": "devices", "mountPath": "/devices", "readOnly": True}],
                 note="the end-to-end check: a device publishes over mTLS and the event must reach the API; it is run on demand by verify_target.py"),
    ]  # fmt: skip


# ---- who talks to whom: (client, server, [ports]). The NetworkPolicies are generated from this and nothing else, so a service can
# neither reach nor be reached by anything not listed here. `verify_workloads.py` checks the list against backend/topology.json.
TELEMETRY = [("aiops-tempo", [4318]), ("aiops-prometheus", [9090])]
DEPENDENCIES: dict[str, list[tuple[str, list[int]]]] = {
    "aiops-api": [("aiops-postgres", [5432]), ("aiops-opa", [8181]), ("aiops-keycloak", [8080]), *TELEMETRY],
    "aiops-scenario-control": [("aiops-postgres", [5432]), ("aiops-opa", [8181]), ("aiops-keycloak", [8080]), *TELEMETRY],
    "aiops-command-executor": [("aiops-postgres", [5432]), ("aiops-opa", [8181]), *TELEMETRY],
    "aiops-outcome-verifier": [("aiops-postgres", [5432]), ("aiops-opa", [8181]), *TELEMETRY],
    "aiops-ingestion-gateway": [("aiops-postgres", [5432]), ("aiops-kafka-broker", [9092]), *TELEMETRY],
    "aiops-mqtt-gateway": [("aiops-mqtt-broker", [8883]), ("aiops-kafka-broker", [9092]), *TELEMETRY],
    "aiops-migrate": [("aiops-postgres", [5432])],
    "aiops-load": [("aiops-postgres", [5432]), ("aiops-mqtt-broker", [8883])],
    "aiops-security": [("aiops-postgres", [5432]), ("aiops-mqtt-broker", [8883]), ("aiops-api", [8100]), ("aiops-keycloak", [8080]), ("aiops-opa", [8181])],
    "aiops-e2e": [("aiops-postgres", [5432]), ("aiops-mqtt-broker", [8883]), ("aiops-api", [8100]), ("aiops-keycloak", [8080]), ("aiops-kafka-broker", [9092])],
    "aiops-prometheus": [("aiops-edge-runtime", [9100]), ("aiops-tempo", [3200])],
    "aiops-platform-correlator": [("aiops-postgres", [5432]), ("aiops-prometheus", [9090])],
    "aiops-platform-probe": [("aiops-postgres", [5432]), ("aiops-opa", [8181]), ("aiops-api", [8100]), ("aiops-prometheus", [9090])],
    "aiops-frontend": [("aiops-api", [8100]), ("aiops-scenario-control", [8101])],
    "aiops-grafana": [("aiops-prometheus", [9090]), ("aiops-tempo", [3200]), ("aiops-loki", [3100])],
}  # fmt: skip
# Reached from the ingress controller's namespace (the users' browsers), on these ports.
USER_FACING = {
    "aiops-frontend": [8080], "aiops-api": [8100], "aiops-scenario-control": [8101], "aiops-keycloak": [8080], "aiops-grafana": [3000],
}  # fmt: skip
# Reached from outside the namespace by devices (the broker authenticates every one with its own certificate, CTL-09).
EXTERNAL_INGRESS = {"aiops-mqtt-broker": [8883]}
JOB_KINDS = ("Job",)


def _peer(name: str) -> dict:
    return {"podSelector": {"matchLabels": {"app.kubernetes.io/name": name}}}


def network_policies(workloads: list[Workload]) -> list[dict]:
    docs: list[dict] = [
        {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": "default-deny-all", "namespace": NAMESPACE},
         "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}},
        {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": "allow-dns-egress", "namespace": NAMESPACE},
         "spec": {"podSelector": {}, "policyTypes": ["Egress"],
                  "egress": [{"to": [{"namespaceSelector": {}}], "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}]}]}},
    ]  # fmt: skip
    clients_of: dict[str, dict[str, list[int]]] = {}
    for client, servers in DEPENDENCIES.items():
        for server, ports in servers:
            clients_of.setdefault(server, {}).setdefault(client, [])
            clients_of[server][client] = sorted(set(clients_of[server][client]) | set(ports))
    for w in workloads:
        ingress: list[dict] = []
        for client, ports in sorted(clients_of.get(w.name, {}).items()):
            ingress.append(
                {"from": [_peer(client)], "ports": [{"protocol": "TCP", "port": p} for p in ports]}
            )
        if w.name in USER_FACING:
            ingress.append({
                "from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "ingress-nginx"}}}],
                "ports": [{"protocol": "TCP", "port": p} for p in USER_FACING[w.name]],
            })  # fmt: skip
        if w.name in EXTERNAL_INGRESS:
            ingress.append(
                {"ports": [{"protocol": "TCP", "port": p} for p in EXTERNAL_INGRESS[w.name]]}
            )
        egress = [
            {"to": [_peer(server)], "ports": [{"protocol": "TCP", "port": p} for p in ports]}
            for server, ports in DEPENDENCIES.get(w.name, [])
        ]
        spec: dict = {
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": w.name}},
            "policyTypes": ["Ingress", "Egress"],
        }
        if ingress:
            spec["ingress"] = ingress
        if egress:
            spec["egress"] = egress
        docs.append(
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {"name": w.name, "namespace": NAMESPACE},
                "spec": spec,
            }
        )
    return docs


def rbac_and_policies() -> str:
    workloads = specs()
    docs: list[dict] = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {
        "name": NAMESPACE,
        "labels": {"app.kubernetes.io/part-of": "aiops-platform", "pod-security.kubernetes.io/enforce": "restricted",
                   "pod-security.kubernetes.io/audit": "restricted", "pod-security.kubernetes.io/warn": "restricted"}}}]  # fmt: skip
    for w in workloads:
        docs.append(
            {
                "apiVersion": "v1",
                "kind": "ServiceAccount",
                "metadata": {"name": w.name, "namespace": NAMESPACE},
                "automountServiceAccountToken": False,
            }
        )
    docs.extend(network_policies(workloads))
    header = (
        "# GENERATED by infra/k8s/generate_workloads.py - edit its specification, not this file. (P09.04 wrote the first version by\n"
        "# hand; P11.02 generates it so the ServiceAccounts, workloads and policies cannot drift apart.)\n"
        "#\n"
        "# Namespace (Pod Security `restricted` enforced), one ServiceAccount per workload, default-deny network policy for the\n"
        "# namespace, then exactly the egress and ingress each workload's dependencies require and nothing else. No workload calls\n"
        "# the Kubernetes API, so no ServiceAccount mounts a token and none has a Role. DNS egress (port 53) is allowed\n"
        "# namespace-wide once.\n"
    )
    return (
        header
        + "---\n"
        + "\n---\n".join(yaml.safe_dump(d, sort_keys=False, width=140) for d in docs)
    )


def labels(w: Workload) -> dict:
    return {"app.kubernetes.io/name": w.name, "app.kubernetes.io/part-of": "aiops-platform"}


def container(w: Workload) -> dict:
    probes = {}
    if w.probe:
        if "http" in w.probe:
            handler = {"httpGet": {"path": w.probe["http"], "port": w.probe["port"]}}
        else:
            handler = {"tcpSocket": {"port": w.probe["tcp"]}}
        probes = {
            "readinessProbe": {
                **handler,
                "periodSeconds": 10,
                "timeoutSeconds": 5,
                "failureThreshold": 6,
            },
            "livenessProbe": {
                **handler,
                "initialDelaySeconds": w.startup_seconds,
                "periodSeconds": 20,
                "timeoutSeconds": 5,
                "failureThreshold": 6,
            },
        }
    mounts = (
        [{"name": f"tmp{i}", "mountPath": path} for i, path in enumerate(w.tmp_dirs)]
        + [{"name": name, "mountPath": path} for name, path, _ in w.scratch]
        + list(w.mounts)
    )
    if w.pvc:
        mounts.append({"name": w.pvc[2], "mountPath": w.pvc[0]})
    spec = {
        "name": w.name.removeprefix("aiops-"),
        "image": w.image,
        "imagePullPolicy": "IfNotPresent",
        **({"command": w.command} if w.command else {}),
        **({"args": w.args} if w.args else {}),
        **(
            {"ports": [{"name": n, "containerPort": p} for n, p in w.ports.items()]}
            if w.ports
            else {}
        ),
        **({"env": w.env} if w.env else {}),
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
        "resources": {
            "requests": {"cpu": _half(w.cpu), "memory": _half(w.memory)},
            "limits": {"cpu": w.cpu, "memory": w.memory},
        },
        **probes,
        **({"volumeMounts": mounts} if mounts else {}),
    }
    return spec


def _half(quantity: str) -> str:
    number, unit = re.fullmatch(r"(\d+)(m|Mi|Gi)", quantity).groups()
    return f"{max(int(number) // 2, 1)}{unit}"


def pod_template(w: Workload) -> dict:
    volumes = (
        [{"name": f"tmp{i}", "emptyDir": {"sizeLimit": "64Mi"}} for i, _ in enumerate(w.tmp_dirs)]
        + [{"name": name, "emptyDir": {"sizeLimit": size}} for name, _, size in w.scratch]
        + list(w.volumes)
    )
    init = []
    if w.seed:
        volume, path = w.seed
        init.append({
            "name": "seed-" + volume, "image": w.image, "imagePullPolicy": "IfNotPresent",
            "command": ["sh", "-c", f"cp -R {path}/. /seed/"],
            "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}},
            "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": "200m", "memory": "256Mi"}},
            "volumeMounts": [{"name": volume, "mountPath": "/seed"}],
        })  # fmt: skip
    return {
        "metadata": {"labels": labels(w)},
        "spec": {
            "serviceAccountName": w.name,
            "automountServiceAccountToken": False,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": w.uid,
                "runAsGroup": w.uid,
                "fsGroup": w.uid,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            **({"initContainers": init} if init else {}),
            "containers": [container(w), *w.extra_containers],
            **({"volumes": volumes} if volumes else {}),
        },
    }


def workload_docs(w: Workload) -> list[dict]:
    docs: list[dict] = []
    meta = {
        "name": w.name,
        "namespace": NAMESPACE,
        "labels": labels(w),
        **({"annotations": {"aiops/note": w.note}} if w.note else {}),
    }
    template = pod_template(w)
    if w.kind == "Job":
        template["spec"]["restartPolicy"] = "Never"
        docs.append({
            "apiVersion": "batch/v1", "kind": "Job", "metadata": meta,
            # A check that failed has FAILED: a retry runs it again against a platform it has already disturbed, and the job's status stops saying which
            # run failed (measured: a load that lost 2008 events was retried, and the database then held two runs under one label). Only the migration is retried.
            "spec": {"backoffLimit": 2 if w.name == "aiops-migrate" else 0, "ttlSecondsAfterFinished": 86400, "template": template},
        })  # fmt: skip
        return docs
    if w.kind == "Deployment":
        docs.append({
            "apiVersion": "apps/v1", "kind": "Deployment", "metadata": meta,
            "spec": {"replicas": 1, "selector": {"matchLabels": {"app.kubernetes.io/name": w.name}}, "template": template,
                     "strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}} if w.rolling else {"type": "Recreate"}},
        })  # fmt: skip
    else:
        spec = {
            "replicas": 1, "serviceName": w.name, "selector": {"matchLabels": {"app.kubernetes.io/name": w.name}}, "template": template,
        }  # fmt: skip
        if w.pvc:
            spec["volumeClaimTemplates"] = [{
                "metadata": {"name": w.pvc[2]},
                "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": w.pvc[1]}}},
            }]  # fmt: skip
        docs.append(
            {"apiVersion": "apps/v1", "kind": "StatefulSet", "metadata": meta, "spec": spec}
        )
    if w.service and w.ports:
        docs.append({
            "apiVersion": "v1", "kind": "Service", "metadata": {"name": w.name, "namespace": NAMESPACE, "labels": labels(w)},
            "spec": {"selector": {"app.kubernetes.io/name": w.name}, "ports": [{"name": n, "port": p, "targetPort": p} for n, p in w.ports.items()]},
        })  # fmt: skip
    return docs


def build(jobs: bool = False) -> str:
    header = (
        "# P11.02: GENERATED by infra/k8s/generate_workloads.py from its specification - edit that, not this file.\n"
        "# Least-privilege workloads: non-root, read-only root file system, all capabilities dropped, no privilege escalation, RuntimeDefault\n"
        "# seccomp, no ServiceAccount token, resource requests and limits, health probes, secrets by reference only. Pod Security `restricted`.\n"
        "# Applies together with rbac-and-network-policy.yaml (namespace, ServiceAccounts, default-deny NetworkPolicies).\n"
    )
    docs: list[dict] = []
    for w in specs():
        if (w.kind in JOB_KINDS) == jobs:
            docs.extend(workload_docs(w))
    if jobs:
        header = header.replace(
            "Least-privilege workloads:",
            "Least-privilege JOBS (run on demand, not part of the steady state):",
        ).replace("Applies together with", "Created by deploy.py / verify_target.py, together with")
    body = "\n---\n".join(yaml.safe_dump(d, sort_keys=False, width=140) for d in docs)
    return header + "---\n" + body


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    outputs = {OUTPUT: build(), JOBS: build(jobs=True), POLICIES: rbac_and_policies()}
    if args.check:
        stale = [
            path.name
            for path, text in outputs.items()
            if (path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.is_file() else "")
            != text
        ]
        if stale:
            print(f"{stale} out of date; run generate_workloads.py", file=sys.stderr)
            return 1
        print("workloads.yaml, jobs.yaml and rbac-and-network-policy.yaml are current")
        return 0
    for path, text in outputs.items():
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
