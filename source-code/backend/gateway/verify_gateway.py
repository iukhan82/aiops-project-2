"""P05.03 acceptance evidence, run against the real P05.01/P05.02 stack
(source-code/infra/platform). Not a pytest suite: it starts/stops real
Docker containers (to prove backpressure across a genuine broker outage),
which is out of place in the always-green unit test suite. Run directly:

    python source-code/backend/gateway/verify_gateway.py

Proves, against the running Gateway, the four P05.03 acceptance dimensions:
1. validation - a schema-invalid event is rejected, never reaches Kafka.
2. partitioning - one device's events always land on the same partition.
3. backpressure - a Kafka outage does not crash the gateway or lose events;
   buffered events drain and get acked once Kafka recovers.
4. application ack/replay - a device receives a real "accepted" ack only
   after Kafka durably has the event, and the Kafka log can be replayed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt
from confluent_kafka import Consumer
from confluent_kafka.admin import AdminClient, NewTopic

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.gateway.gateway import Gateway, GatewayConfig  # noqa: E402

PLATFORM_DIR = SOURCE_ROOT / "infra" / "platform"
CERTS = PLATFORM_DIR / "mosquitto" / "certs"
CA = CERTS / "ca.crt"
DEVICE_A = "corridor-a-int-03-loop-01"
DEVICE_B = "corridor-a-int-03-loop-02"
KAFKA_TOPIC = "telemetry.events.p05_03"
MQTT_HOST, MQTT_PORT = "127.0.0.1", 8883
KAFKA_BOOTSTRAP = "127.0.0.1:19092"

results: dict[str, bool] = {}
notes: dict[str, str] = {}


def note(msg: str) -> None:
    print(f">> {msg}", flush=True)


def check(name: str, ok: bool, detail: str = "") -> None:
    results[name] = ok
    notes[name] = detail
    print(f"{'PASS' if ok else 'FAIL'}: {name} {detail}", flush=True)


def device_client(device_id: str) -> mqtt.Client:
    c = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2, client_id=f"test-{device_id}-{uuid.uuid4().hex[:6]}"
    )
    c.tls_set(
        ca_certs=str(CA),
        certfile=str(CERTS / f"device-{device_id}.crt"),
        keyfile=str(CERTS / f"device-{device_id}.key"),
    )
    c.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    c.loop_start()
    return c


def make_event(device_id: str, seq: int, event_id: str | None = None) -> dict:
    return {
        "schema_version": "1.0.0",
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": "traffic.loop_detector.count",
        "device_id": device_id,
        "agency_scope": "city-traffic-ops",
        "observation_time": "2026-09-19T18:00:00Z",
        "ingest_time": "2026-09-19T18:00:00Z",
        "sequence_number": seq,
        "clock_quality": "synced",
        "geometry_version": "2026-09-18.1",
        "location": {
            "coordinate_reference": "EPSG:4326",
            "latitude": 31.5204,
            "longitude": 74.3587,
            "intersection_id": "int-03",
            "corridor_id": "corridor-a",
        },
        "measurements": [
            {
                "name": "vehicle_count",
                "value": seq,
                "unit": "count",
                "quality": "valid",
                "confidence": 0.95,
            }
        ],
        "truth_label": "simulated",
        "privacy_classification": "none",
        "retention_class": "standard",
    }


def wait_for_ack(collected: list[dict], event_id: str, timeout_s: float) -> dict | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for a in collected:
            if a.get("event_id") == event_id:
                return a
        time.sleep(0.1)
    return None


def reset_kafka_topic() -> None:
    admin = AdminClient({"bootstrap.servers": KAFKA_BOOTSTRAP})
    try:
        futures = admin.delete_topics([KAFKA_TOPIC])
        for f in futures.values():
            try:
                f.result(10)
            except Exception:
                pass
    except Exception:
        pass
    time.sleep(1)
    futures = admin.create_topics([NewTopic(KAFKA_TOPIC, num_partitions=3, replication_factor=1)])
    for f in futures.values():
        f.result(10)


def kafka_all_messages(topic: str, timeout_s: float = 5.0) -> list[dict]:
    consumer = Consumer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "group.id": f"verify-{uuid.uuid4().hex}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([topic])
    out = []
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        msg = consumer.poll(0.5)
        if msg is None:
            continue
        if msg.error():
            continue
        out.append(
            {
                "partition": msg.partition(),
                "key": msg.key().decode() if msg.key() else None,
                "value": json.loads(msg.value()),
            }
        )
    consumer.close()
    return out


def docker(*args: str) -> subprocess.CompletedProcess:
    """Shells out through WSL: this verify script runs under the Windows
    venv (where confluent-kafka/paho-mqtt are installed and can already
    reach the Docker-Desktop-forwarded ports), but the `docker` CLI itself
    is only on PATH inside WSL in this environment."""
    cmd = "docker " + " ".join(args)
    return subprocess.run(["wsl.exe", "-e", "bash", "-lc", cmd], capture_output=True, text=True)


def main() -> int:
    note("Resetting Kafka topic...")
    reset_kafka_topic()

    outbox_path = PLATFORM_DIR / "output" / "gateway_verify_outbox.sqlite"
    outbox_path.parent.mkdir(parents=True, exist_ok=True)
    outbox_path.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(outbox_path) + suffix).unlink(missing_ok=True)

    config = GatewayConfig(
        mqtt_host=MQTT_HOST,
        mqtt_port=MQTT_PORT,
        mqtt_cafile=CA,
        mqtt_certfile=CERTS / "gateway.crt",
        mqtt_keyfile=CERTS / "gateway.key",
        kafka_bootstrap=KAFKA_BOOTSTRAP,
        outbox_path=outbox_path,
        kafka_topic=KAFKA_TOPIC,
    )
    gw = Gateway(config)
    gw.start()
    time.sleep(1.5)

    acks_a: list[dict] = []
    acks_b: list[dict] = []
    dev_a = device_client(DEVICE_A)
    dev_b = device_client(DEVICE_B)
    dev_a.on_message = lambda c, u, m: acks_a.append(json.loads(m.payload))
    dev_b.on_message = lambda c, u, m: acks_b.append(json.loads(m.payload))
    dev_a.subscribe(f"devices/{DEVICE_A}/ack", qos=1)
    dev_b.subscribe(f"devices/{DEVICE_B}/ack", qos=1)
    time.sleep(0.5)

    try:
        # ---- 1. validation: a bad event must be rejected, not reach Kafka ----
        note("Testing validation (schema-invalid event)...")
        bad = make_event(DEVICE_A, seq=1)
        del bad["measurements"]  # required field missing
        bad_id = bad["event_id"]
        dev_a.publish(f"devices/{DEVICE_A}/telemetry", json.dumps(bad), qos=1)
        ack = wait_for_ack(acks_a, bad_id, timeout_s=5)
        check(
            "validation_rejects_invalid_event",
            ack is not None and ack["status"] == "rejected",
            detail=str(ack),
        )

        # ---- 2. valid events + partitioning ----
        note("Publishing valid events for two devices (partitioning check)...")
        a_ids, b_ids = [], []
        for i in range(3):
            ev = make_event(DEVICE_A, seq=100 + i)
            a_ids.append(ev["event_id"])
            dev_a.publish(f"devices/{DEVICE_A}/telemetry", json.dumps(ev), qos=1)
        for i in range(3):
            ev = make_event(DEVICE_B, seq=200 + i)
            b_ids.append(ev["event_id"])
            dev_b.publish(f"devices/{DEVICE_B}/telemetry", json.dumps(ev), qos=1)

        all_acked = True
        for eid in a_ids:
            ack = wait_for_ack(acks_a, eid, timeout_s=10)
            all_acked = all_acked and ack is not None and ack["status"] == "accepted"
        for eid in b_ids:
            ack = wait_for_ack(acks_b, eid, timeout_s=10)
            all_acked = all_acked and ack is not None and ack["status"] == "accepted"
        check("valid_events_get_accepted_ack", all_acked)

        messages = kafka_all_messages(KAFKA_TOPIC, timeout_s=5)
        by_id = {m["value"]["event_id"]: m for m in messages}
        check(
            "valid_events_land_in_kafka",
            all(eid in by_id for eid in a_ids + b_ids),
        )
        check("rejected_event_not_in_kafka", bad_id not in by_id)

        a_partitions = {by_id[eid]["partition"] for eid in a_ids if eid in by_id}
        b_partitions = {by_id[eid]["partition"] for eid in b_ids if eid in by_id}
        check(
            "device_partitioning_consistent",
            len(a_partitions) == 1 and len(b_partitions) == 1,
            detail=f"device A partitions={a_partitions}, device B partitions={b_partitions}",
        )

        # ---- 3. backpressure: Kafka outage must not crash or drop events ----
        note("Stopping Kafka broker to test backpressure...")
        docker("stop", "aiops-kafka-broker")
        time.sleep(2)

        outage_ids = []
        for i in range(3):
            ev = make_event(DEVICE_A, seq=300 + i)
            outage_ids.append(ev["event_id"])
            dev_a.publish(f"devices/{DEVICE_A}/telemetry", json.dumps(ev), qos=1)
        time.sleep(3)

        no_premature_ack = all(wait_for_ack(acks_a, eid, timeout_s=1) is None for eid in outage_ids)
        # gw.outbox lives on the worker thread only (single-writer contract,
        # P04.07); stats counters are simple dict increments, safe enough
        # to read cross-thread for this informational detail.
        approx_pending = gw.stats["accepted_buffered"] - gw.stats["delivered"]
        check(
            "backpressure_no_premature_ack_and_gateway_alive",
            no_premature_ack and gw._worker_thread.is_alive(),
            detail=f"approx_pending={approx_pending}",
        )

        note("Restarting Kafka broker, expecting buffered events to drain...")
        docker("start", "aiops-kafka-broker")
        recovered = True
        for eid in outage_ids:
            ack = wait_for_ack(acks_a, eid, timeout_s=45)
            recovered = recovered and ack is not None and ack["status"] == "accepted"
        check("backpressure_drains_after_recovery", recovered)

        messages_after = kafka_all_messages(KAFKA_TOPIC, timeout_s=5)
        ids_after = {m["value"]["event_id"] for m in messages_after}
        check("outage_events_eventually_in_kafka", all(eid in ids_after for eid in outage_ids))

        # ---- 4. replay: consuming from earliest twice yields the same set ----
        note("Testing replay (consume from earliest twice)...")
        run1 = {m["value"]["event_id"] for m in kafka_all_messages(KAFKA_TOPIC, timeout_s=5)}
        run2 = {m["value"]["event_id"] for m in kafka_all_messages(KAFKA_TOPIC, timeout_s=5)}
        check(
            "replay_from_earliest_is_reproducible",
            run1 == run2 and len(run1) > 0,
            detail=f"n={len(run1)}",
        )

    finally:
        gw.stop()
        dev_a.loop_stop()
        dev_b.loop_stop()

    all_passed = all(results.values())
    evidence = {
        "task": "P05.03",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": results,
        "notes": notes,
        "all_passed": all_passed,
    }
    out_path = PLATFORM_DIR / "output" / "p05_03_evidence.json"
    out_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    note(f"Evidence written to {out_path}")
    note("P05.03: ALL CHECKS PASSED" if all_passed else "P05.03: ONE OR MORE CHECKS FAILED")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
