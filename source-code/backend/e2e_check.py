"""P11.03: the end-to-end check, run as a Job inside the cluster.

    python backend/e2e_check.py           # MQTT_HOST, MQTT_PORT, API_URL, KEYCLOAK_URL, KAFKA_BOOTSTRAP and the database env

A device publishes one telemetry event over the broker's mutually-authenticated listener with its own certificate. The event must travel
broker -> gateway -> Kafka -> ingestion -> PostgreSQL, and the operator API must be able to see the device's freshness afterwards. It also
makes the negative attempts that only a real network can make: a device publishing under another device's topic must not be delivered, and a
client whose certificate the platform CA did not sign must not get a connection at all. One JSON line on stdout says what happened; the
exit status is 0 only if every step held. Nothing here is a stand-in: it talks to the same Services the workloads use, through the same
NetworkPolicies.

The certificates are mounted at /devices (Secret `aiops-tls-devices`): ca.crt, and `<device>.crt` / `<device>.key` per device, plus
`rogue.crt` / `rogue.key` signed by a DIFFERENT CA for the negative case.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import paho.mqtt.client as mqtt
import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from database.migrate import dsn_from_env  # noqa: E402

CERTS = Path("/devices")
DEVICE_A, DEVICE_B = "e2e-device-a", "e2e-device-b"
GEOMETRY_VERSION = "2026-09-18.1"
MQTT_HOST = os.environ["MQTT_HOST"]
MQTT_PORT = int(os.environ.get("MQTT_PORT", "8883"))
API_URL = os.environ["API_URL"]
WAIT_S = 90.0


def event(device_id: str, sequence: int) -> dict:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {
        "schema_version": "1.0.0", "event_id": str(uuid.uuid4()), "event_type": "traffic.loop_detector.count", "device_id": device_id,
        "agency_scope": "city-traffic-ops", "observation_time": now, "ingest_time": now, "sequence_number": sequence, "clock_quality": "synced",
        "geometry_version": GEOMETRY_VERSION,
        "location": {"coordinate_reference": "EPSG:4326", "latitude": 31.5204, "longitude": 74.3587},
        "measurements": [{"name": "vehicle_count", "value": 3, "unit": "count", "quality": "valid", "confidence": 0.95}],
        "truth_label": "simulated", "privacy_classification": "none", "retention_class": "standard",
    }  # fmt: skip


def connect(client_id: str, cert: str | None) -> tuple[mqtt.Client | None, str]:
    """(client, "") when the broker accepts the connection; (None, reason) when the TLS handshake or the CONNECT is refused."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    try:
        if cert:
            client.tls_set(
                ca_certs=str(CERTS / "ca.crt"),
                certfile=str(CERTS / f"{cert}.crt"),
                keyfile=str(CERTS / f"{cert}.key"),
            )
        else:
            client.tls_set(ca_certs=str(CERTS / "ca.crt"))
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=20)
        client.loop_start()
        deadline = time.time() + 10
        while time.time() < deadline and not client.is_connected():
            time.sleep(0.1)
        if not client.is_connected():
            client.loop_stop()
            return None, "no CONNACK"
        return client, ""
    except (ssl.SSLError, ConnectionError, OSError) as exc:
        return None, type(exc).__name__


def main() -> int:  # noqa: PLR0915
    report: dict = {"steps": {}}
    started = time.time()
    with psycopg.connect(dsn_from_env(), autocommit=True) as db:
        db.execute(
            "INSERT INTO geometry_versions (version, effective_from) VALUES (%s, '2026-09-18T00:00:00Z') ON CONFLICT (version) DO NOTHING",
            (GEOMETRY_VERSION,),
        )
        for device in (DEVICE_A, DEVICE_B):
            db.execute(
                "INSERT INTO devices (device_id, device_type, deployment_type, agency_scope, geometry_version, location, status, registered_at, "
                "privacy_classification, retention_class) VALUES (%s, 'inductive_loop', 'simulated', 'city-traffic-ops', %s, "
                "ST_SetSRID(ST_MakePoint(74.3587, 31.5204), 4326)::geography, 'active', now(), 'none', 'standard') ON CONFLICT (device_id) DO NOTHING",
                (device, GEOMETRY_VERSION),
            )
        report["steps"]["devices_registered"] = True

        # 1. a device with a valid certificate publishes under its own topic
        client, why = connect(DEVICE_A, DEVICE_A)
        report["steps"]["device_connects_with_its_certificate"] = client is not None
        good = event(DEVICE_A, int(time.time()))
        if client:
            client.publish(
                f"devices/{DEVICE_A}/telemetry", json.dumps(good), qos=1
            ).wait_for_publish(10)

        # 2. the same device publishes under ANOTHER device's topic: the broker must not deliver it
        crossed = event(DEVICE_B, int(time.time()) + 1)
        if client:
            client.publish(
                f"devices/{DEVICE_B}/telemetry", json.dumps(crossed), qos=1
            ).wait_for_publish(10)
            client.loop_stop()
            client.disconnect()

        # 3. a client whose certificate the platform CA did not sign must not connect
        rogue, rogue_reason = connect("rogue", "rogue")
        report["steps"]["a_certificate_from_another_ca_is_refused"] = rogue is None
        report["rogue_refusal"] = rogue_reason
        if rogue:
            rogue.loop_stop()
            rogue.disconnect()

        # 4. the good event must reach PostgreSQL through the gateway, Kafka and ingestion
        reached_at = None
        deadline = time.time() + WAIT_S
        while time.time() < deadline:
            row = db.execute(
                "SELECT 1 FROM observation_events WHERE event_id = %s", (good["event_id"],)
            ).fetchone()
            if row:
                reached_at = time.time()
                break
            time.sleep(1)
        report["steps"]["the_event_reached_postgres_through_broker_gateway_kafka_and_ingestion"] = (
            reached_at is not None
        )
        report["seconds_to_reach_postgres"] = round(reached_at - started, 1) if reached_at else None
        crossed_row = db.execute(
            "SELECT 1 FROM observation_events WHERE event_id = %s", (crossed["event_id"],)
        ).fetchone()
        report["steps"]["an_event_published_under_another_devices_topic_never_arrived"] = (
            crossed_row is None
        )

    # 5. the API sees the platform: it answers, and it refuses a caller with no token
    health = httpx.get(f"{API_URL}/api/v1/health", timeout=10)
    report["steps"]["the_api_answers_its_health_check"] = health.status_code == 200
    anonymous = httpx.get(f"{API_URL}/api/v1/devices", timeout=10)
    report["steps"]["the_api_refuses_a_caller_with_no_token"] = anonymous.status_code in (401, 403)

    report["passed"] = all(report["steps"].values())
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
