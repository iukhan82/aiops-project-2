"""P11.06: drive the real ingestion path at a chosen rate and measure what happens, run as a Job inside the cluster.

    LOAD_DEVICES=8 LOAD_RATE=5 LOAD_SECONDS=60 LOAD_LABEL=nominal python backend/load_check.py

Each simulated device is a real client of the broker: its own certificate (Secret `aiops-tls-devices`: `e2e-load-N`), a mutually-authenticated
connection, QoS 1 publishes under its own topic. Every event travels broker -> gateway -> Kafka -> ingestion -> PostgreSQL. The events carry
the run's label in `correlation_id`, so what arrived is counted exactly, not estimated, and the time from the device's observation to the
committed row (`received_at - observation_time`, one node, one clock) is read from the database.

One JSON line on stdout: what was offered, what the broker acknowledged, what reached PostgreSQL (lost = offered - arrived, duplicates = 0 by
the primary key), the latency distribution, and the rate actually sustained. Exit status 0 if nothing was lost. Nothing is stubbed.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import paho.mqtt.client as mqtt
import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from database.migrate import dsn_from_env  # noqa: E402

CERTS = Path("/devices")
GEOMETRY_VERSION = "2026-09-18.1"
DEVICES = int(os.environ.get("LOAD_DEVICES", "8"))
RATE = float(os.environ.get("LOAD_RATE", "5"))
SECONDS = float(os.environ.get("LOAD_SECONDS", "60"))
LABEL = os.environ.get("LOAD_LABEL", "load")
DRAIN_S = float(os.environ.get("LOAD_DRAIN_SECONDS", "90"))
MQTT_HOST, MQTT_PORT = os.environ["MQTT_HOST"], int(os.environ.get("MQTT_PORT", "8883"))


def envelope(device: str, sequence: int, run: str) -> dict:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return {
        "schema_version": "1.0.0", "event_id": str(uuid.uuid4()), "event_type": "traffic.loop_detector.count", "device_id": device,
        "agency_scope": "city-traffic-ops", "observation_time": now, "ingest_time": now, "sequence_number": sequence, "clock_quality": "synced",
        "geometry_version": GEOMETRY_VERSION, "correlation_id": run,
        "location": {"coordinate_reference": "EPSG:4326", "latitude": 31.5204, "longitude": 74.3587},
        "measurements": [{"name": "vehicle_count", "value": sequence % 7, "unit": "count", "quality": "valid", "confidence": 0.95}],
        "truth_label": "simulated", "privacy_classification": "none", "retention_class": "standard",
    }  # fmt: skip


def publish_loop(
    device: str, run: str, results: dict, lock: threading.Lock, stop_at: float
) -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{device}-{run[-6:]}")
    client.tls_set(
        ca_certs=str(CERTS / "ca.crt"),
        certfile=str(CERTS / f"{device}.crt"),
        keyfile=str(CERTS / f"{device}.key"),
    )
    acked = 0

    def on_publish(*_args) -> None:
        nonlocal acked
        acked += 1

    client.on_publish = on_publish
    client.max_inflight_messages_set(200)
    client.max_queued_messages_set(0)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()
    deadline = time.time() + 15
    while not client.is_connected() and time.time() < deadline:
        time.sleep(0.05)
    sent = 0
    period = 1.0 / RATE
    next_at = time.time()
    while time.time() < stop_at and client.is_connected():
        info = client.publish(
            f"devices/{device}/telemetry", json.dumps(envelope(device, sent, run)), qos=1
        )
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            sent += 1
        next_at += period
        pause = next_at - time.time()
        if pause > 0:
            time.sleep(pause)
    end = time.time() + 30
    while acked < sent and time.time() < end:
        time.sleep(0.1)
    client.loop_stop()
    client.disconnect()
    with lock:
        results[device] = {"sent": sent, "acked": acked}


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 4)


class Database:
    """One connection, opened again whenever it breaks. A database outage during a load is something this check must measure - the events wait in
    Kafka - not something it may die of; only the drain deadline ends the waiting."""

    def __init__(self) -> None:
        self._conn: psycopg.Connection | None = None

    def query(self, sql: str, params: tuple = (), until: float | None = None) -> list:
        while True:
            try:
                if self._conn is None or self._conn.closed:
                    self._conn = psycopg.connect(dsn_from_env(), autocommit=True, connect_timeout=5)
                return self._conn.execute(sql, params).fetchall()
            except psycopg.Error:
                self._conn = None
                if until is not None and time.time() > until:
                    raise
                time.sleep(1)

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()


def main() -> int:
    run = f"{LABEL}-{uuid.uuid4().hex[:8]}"
    devices = [f"e2e-load-{i}" for i in range(DEVICES)]
    db = Database()
    db.query(
        "INSERT INTO geometry_versions (version, effective_from) VALUES (%s, '2026-09-18T00:00:00Z') ON CONFLICT (version) DO NOTHING RETURNING version",
        (GEOMETRY_VERSION,),
        time.time() + 60,
    )
    for device in devices:
        db.query(
            "INSERT INTO devices (device_id, device_type, deployment_type, agency_scope, geometry_version, location, status, registered_at, privacy_classification, retention_class) "
            "VALUES (%s, 'inductive_loop', 'simulated', 'city-traffic-ops', %s, ST_SetSRID(ST_MakePoint(74.3587, 31.5204), 4326)::geography, 'active', now(), 'none', 'standard') "
            "ON CONFLICT (device_id) DO NOTHING RETURNING device_id",
            (device, GEOMETRY_VERSION),
            time.time() + 60,
        )
    results: dict[str, dict] = {}
    lock = threading.Lock()
    started = time.time()
    stop_at = started + SECONDS
    threads = [
        threading.Thread(target=publish_loop, args=(d, run, results, lock, stop_at))
        for d in devices
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    publishing_s = time.time() - started
    offered = sum(r["sent"] for r in results.values())
    acked = sum(r["acked"] for r in results.values())
    arrived = 0
    deadline = time.time() + DRAIN_S
    while time.time() < deadline:
        arrived = db.query(
            "SELECT count(*) FROM observation_events WHERE correlation_id = %s", (run,), deadline
        )[0][0]
        if arrived >= offered:
            break
        time.sleep(1)
    drained_s = time.time() - started
    rows = db.query(
        "SELECT extract(epoch FROM received_at - observation_time), extract(epoch FROM observation_time) FROM observation_events WHERE correlation_id = %s",
        (run,),
        time.time() + 60,
    )
    db.close()
    latencies = [float(r[0]) for r in rows]
    profile: dict[int, list[float]] = {}
    for (
        latency,
        observed,
    ) in rows:  # where in the run the slow events were: the worst latency of each 10-second slice of the run
        profile.setdefault(max(0, int((float(observed) - started) // 10)) * 10, []).append(
            float(latency)
        )
    report = {
        "label": LABEL, "run": run, "devices": DEVICES, "rate_per_device": RATE, "offered_rate": round(DEVICES * RATE, 1), "seconds": SECONDS,
        "offered": offered, "acked_by_broker": acked, "arrived_in_postgres": arrived, "lost": offered - arrived,
        "sustained_publish_rate": round(offered / publishing_s, 1) if publishing_s else None, "seconds_until_everything_arrived": round(drained_s, 1),
        "latency_s": {"n": len(latencies), "p50": percentile(latencies, 0.50), "p95": percentile(latencies, 0.95), "p99": percentile(latencies, 0.99), "max": round(max(latencies), 3) if latencies else None, "mean": round(statistics.fmean(latencies), 4) if latencies else None},
        "latency_by_10s_of_run_max_s": {str(k): round(max(v), 2) for k, v in sorted(profile.items())},
        "passed": offered > 0 and arrived == offered,
    }  # fmt: skip
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
