"""P10.04: the platform probe - real platform state turned into the gauges the alert rules evaluate.

    python source-code/backend/aiops/platform_probe.py --once
    python source-code/backend/aiops/platform_probe.py --interval 15          # until stopped

Nothing here invents a signal. Each gauge is read from the thing it describes: device freshness from the same query and
budgets the device-health screen uses (`routes_govern`), worker liveness from `service_heartbeats`, storage from
`pg_database_size` and the retention log, certificate expiry from the certificate files themselves, and configuration
drift from the applied-migration checksums and the policy version the engine actually serves. A source that cannot be
read is reported as `platform_probe_target_up = 0`, never as a healthy value.

Metric labels stay inside `contracts/metric-label-set/v1` (only `device_type` and `service_name` are used).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
from cryptography import x509

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend import observability, pdp  # noqa: E402
from backend.api import routes_govern  # noqa: E402
from database.migrate import discover_migrations  # noqa: E402
from database.retention import HARD_SIZE_BYTES  # noqa: E402

PLATFORM = SOURCE_ROOT / "infra" / "platform"
POLICY_DATA = SOURCE_ROOT / "policy" / "aiops" / "model" / "data.json"
API_HEALTH_URL = os.environ.get("AIOPS_API_HEALTH_URL", "http://127.0.0.1:8100/api/v1/health")

# Certificate groups by the service that presents them; the earliest expiry in a group is the one that matters.
# `untrusted-ca.crt` is a deliberate negative-test fixture, not something a service presents, so it is not watched.
CERTIFICATE_GROUPS: dict[str, tuple[str, ...]] = {
    "postgres": ("postgres/certs/ca.crt", "postgres/certs/server.crt"),
    "mqtt-broker": (
        "mosquitto/certs/ca.crt",
        "mosquitto/certs/server.crt",
        "mosquitto/certs/gateway.crt",
    ),
    "mqtt-devices": ("mosquitto/certs/device-*.crt",),
}


@dataclass(frozen=True)
class Sample:
    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)


def _now() -> datetime:
    return datetime.now(UTC)


def device_samples(conn: psycopg.Connection) -> list[Sample]:
    """Per device type: how many active devices, how many are stale and how many have never reported - with the exact
    numbers the device-health screen shows (`routes_govern._source_freshness`), so the alert and the screen agree."""
    out: list[Sample] = []
    for row in routes_govern._source_freshness(conn):  # noqa: SLF001 - one source of truth for "stale"
        labels = {"device_type": row["id"]}
        out.append(Sample("platform_devices_active", float(row["devices"]), labels))
        out.append(Sample("platform_devices_stale", float(row["stale"]), labels))
        out.append(Sample("platform_devices_never_reported", float(row["never"]), labels))
    with conn.cursor() as cur:
        cur.execute(
            "SELECT device_type, min(extract(epoch FROM certificate_not_after - now())) FROM devices "
            "WHERE status = 'active' AND certificate_not_after IS NOT NULL GROUP BY device_type"
        )
        for device_type, seconds in cur.fetchall():
            out.append(
                Sample(
                    "platform_device_certificate_expiry_seconds",
                    float(seconds),
                    {"device_type": device_type},
                )
            )
    return out


def heartbeat_samples(conn: psycopg.Connection) -> list[Sample]:
    budgets = {service: budget for service, _label, budget in routes_govern.WORKERS}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT service, extract(epoch FROM now() - last_seen) FROM service_heartbeats WHERE service = ANY(%s)",
            (list(budgets),),
        )
        ages = {service: float(age) for service, age in cur.fetchall()}
    out: list[Sample] = []
    for service, budget in budgets.items():
        labels = {"service_name": service}
        out.append(Sample("platform_service_heartbeat_budget_seconds", budget, labels))
        if service in ages:  # a worker that never reported has no age: absent, not zero
            out.append(Sample("platform_service_heartbeat_age_seconds", ages[service], labels))
    return out


def storage_samples(conn: psycopg.Connection, limit_bytes: int = HARD_SIZE_BYTES) -> list[Sample]:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_database_size(current_database())")
        (size,) = cur.fetchone()
        cur.execute(
            "SELECT extract(epoch FROM now() - run_at), storage_pressure FROM retention_runs ORDER BY run_at DESC LIMIT 1"
        )
        last = cur.fetchone()
    out = [
        Sample("platform_database_size_bytes", float(size)),
        Sample("platform_database_size_limit_bytes", float(limit_bytes)),
    ]
    if last is not None:
        out.append(Sample("platform_retention_last_run_age_seconds", float(last[0])))
        out.append(Sample("platform_retention_storage_pressure", 1.0 if last[1] else 0.0))
    return out


def migration_samples(conn: psycopg.Connection) -> list[Sample]:
    with conn.cursor() as cur:
        cur.execute("SELECT filename, checksum FROM schema_migrations")
        applied = dict(cur.fetchall())
    files = discover_migrations()
    pending = sum(1 for m in files if m.filename not in applied)
    mismatched = sum(
        1 for m in files if m.filename in applied and applied[m.filename] != m.checksum
    )
    return [
        Sample("platform_config_migrations_pending", float(pending)),
        Sample("platform_config_migrations_checksum_mismatch", float(mismatched)),
    ]


def certificate_samples(base: Path = PLATFORM, now: datetime | None = None) -> list[Sample]:
    """Seconds until the earliest `notAfter` in each group, read from the certificate files themselves (negative once
    expired). A group with no readable certificate reports nothing rather than a comforting number."""
    now = now or _now()
    out: list[Sample] = []
    for service, patterns in CERTIFICATE_GROUPS.items():
        expiries = []
        for pattern in patterns:
            for path in sorted(base.glob(pattern)):
                cert = x509.load_pem_x509_certificate(path.read_bytes())
                expiries.append(cert.not_valid_after_utc)
        if expiries:
            out.append(
                Sample(
                    "platform_certificate_expiry_seconds",
                    (min(expiries) - now).total_seconds(),
                    {"service_name": service},
                )
            )
    return out


def policy_samples(policy_data: Path = POLICY_DATA) -> list[Sample]:
    """The policy the engine serves must be the one the repository generated (`policy/build_data.py`): a stale engine
    would decide with yesterday's roles and routes."""
    expected = json.loads(policy_data.read_text(encoding="utf-8"))["version"]
    loaded = pdp.loaded_version()
    return [
        Sample(
            "platform_probe_target_up", 1.0 if loaded is not None else 0.0, {"service_name": "opa"}
        ),
        Sample(
            "platform_config_policy_version_match",
            1.0 if loaded == expected else 0.0,
        ),
    ]


def api_samples(url: str = API_HEALTH_URL) -> list[Sample]:
    try:
        body = httpx.get(url, timeout=3.0).json()
    except (httpx.HTTPError, ValueError):
        return [Sample("platform_probe_target_up", 0.0, {"service_name": "api"})]
    return [
        Sample("platform_probe_target_up", 1.0, {"service_name": "api"}),
        Sample(
            "platform_config_api_auth_mode_oidc", 1.0 if body.get("auth_mode") == "oidc" else 0.0
        ),
    ]


@dataclass
class ProbeConfig:
    """Where the probe looks. The defaults are the real platform; the acceptance script points single fields elsewhere
    (a scratch database, a directory of short-lived certificates) to induce one real fault at a time."""

    dsn: str
    platform_dir: Path = PLATFORM
    policy_data: Path = POLICY_DATA
    api_health_url: str = API_HEALTH_URL
    db_limit_bytes: int = HARD_SIZE_BYTES


def collect(config: ProbeConfig) -> list[Sample]:
    """Every sample this probe can read right now. Each source fails independently and says so."""
    samples: list[Sample] = []
    try:
        with psycopg.connect(config.dsn, connect_timeout=5) as conn:
            for reader in (device_samples, heartbeat_samples, migration_samples):
                samples.extend(reader(conn))
            samples.extend(storage_samples(conn, config.db_limit_bytes))
        samples.append(Sample("platform_probe_target_up", 1.0, {"service_name": "postgres"}))
    except psycopg.Error:
        samples.append(Sample("platform_probe_target_up", 0.0, {"service_name": "postgres"}))
    samples.extend(certificate_samples(config.platform_dir))
    samples.extend(policy_samples(config.policy_data))
    samples.extend(api_samples(config.api_health_url))
    samples.append(Sample("platform_probe_success_timestamp_seconds", time.time()))
    return samples


_GAUGES: dict[str, observability.BoundedGauge] = {}


def publish(samples: list[Sample]) -> None:
    for sample in samples:
        gauge = _GAUGES.get(sample.name)
        if gauge is None:
            gauge = _GAUGES[sample.name] = observability.BoundedGauge(sample.name)
        gauge.set(sample.value, **sample.labels)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--once", action="store_true", help="collect and publish one time, then exit"
    )
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--database", default=os.environ.get("POSTGRES_DB", "aiops"))
    args = parser.parse_args()

    os.environ["POSTGRES_DB"] = args.database
    from database.migrate import dsn_from_env

    observability.configure("aiops-platform-probe")
    while True:
        publish(collect(ProbeConfig(dsn=dsn_from_env())))
        observability.flush()
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
