#!/usr/bin/env python3
"""A small edge site for the outage lab (P12.02, REC-01): sense, buffer durably, uplink in order, delete only on the platform's own acknowledgement.

    python acceptance/edge_site.py --outbox FILE --device ID --run LABEL --rate 25 --seconds 45

It is the smallest thing that has the edge's obligations: every reading is committed to the durable outbox (`edge/outbox.py`) BEFORE any attempt to send it; the uplink sends the
pending readings in order over the broker's mutually-authenticated listener (the device's own certificate) and marks a reading delivered only when the gateway's application-level
acknowledgement for that event arrives on `devices/<id>/ack` - not when the broker accepts the publish; the first failure stops the pass, so a later reading is never acknowledged
ahead of an earlier one; a restart re-sends whatever is still pending, under the same event ids. Event ids are derived from the run and the sequence number, so a reading that
was buffered twice (a restart replays the loop) is still one reading.

It prints one JSON line on exit and can be killed at any moment: the outbox is on disk.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import paho.mqtt.client as mqtt

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from edge.outbox import DurableOutbox  # noqa: E402

CERTS = SOURCE_ROOT / "infra" / "platform" / "mosquitto" / "certs"
NAMESPACE = uuid.UUID("6a0a3a86-0f8e-4b0a-9d0f-4d6b1f7f2c11")
ACK_WAIT_S = 6.0
WINDOW = 100  # readings sent before their acknowledgements are waited for


def reading(device: str, run: str, sequence: int) -> dict:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return {
        "schema_version": "1.0.0", "event_id": str(uuid.uuid5(NAMESPACE, f"{run}/{device}/{sequence}")), "event_type": "traffic.loop_detector.count", "device_id": device,
        "agency_scope": "city-traffic-ops", "observation_time": now, "ingest_time": now, "sequence_number": sequence, "clock_quality": "synced",
        "geometry_version": "2026-09-18.1", "correlation_id": run,
        "location": {"coordinate_reference": "EPSG:4326", "latitude": 31.5204, "longitude": 74.3587},
        "measurements": [{"name": "vehicle_count", "value": sequence % 7, "unit": "count", "quality": "valid", "confidence": 0.95}],
        "truth_label": "simulated", "privacy_classification": "none", "retention_class": "standard",
    }  # fmt: skip


def next_sequence(path: Path) -> int:
    """The sensor's sequence continues past everything the outbox has ever held (this site never prunes it, one row per reading, no gaps); what the sensor produced while this
    process was down was never buffered, so it is not owed."""
    if not path.exists():
        return 0
    con = sqlite3.connect(str(path))
    try:
        return int(con.execute("SELECT COUNT(*) FROM outbox").fetchone()[0])
    finally:
        con.close()


def main() -> int:  # noqa: PLR0915
    parser = argparse.ArgumentParser()
    parser.add_argument("--outbox", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--rate", type=float, default=25.0)
    parser.add_argument("--seconds", type=float, default=45.0)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8883)
    parser.add_argument("--topic-suffix", default="telemetry")
    args = parser.parse_args()

    path = Path(args.outbox)
    acked_by_platform: set[str] = set()
    lock = threading.Lock()
    stats = {"published": 0, "publish_failed": 0, "ack_timeouts": 0, "buffered": 0}
    stop = threading.Event()

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2, client_id=f"edge-{args.device}-{uuid.uuid4().hex[:6]}"
    )
    client.tls_set(
        ca_certs=str(CERTS / "ca.crt"),
        certfile=str(CERTS / f"device-{args.device}.crt"),
        keyfile=str(CERTS / f"device-{args.device}.key"),
    )

    def on_message(_c, _u, msg) -> None:
        try:
            body = json.loads(msg.payload)
        except ValueError:
            return
        if body.get("status") == "accepted":
            with lock:
                acked_by_platform.add(body.get("event_id"))

    client.on_message = on_message
    client.on_connect = lambda c, *_a: c.subscribe(f"devices/{args.device}/ack", qos=1)
    client.reconnect_delay_set(min_delay=1, max_delay=2)
    client.max_inflight_messages_set(WINDOW)
    client.max_queued_messages_set(0)
    try:
        client.connect(args.host, args.port, keepalive=15)
    except OSError:
        pass  # the uplink may be down when the site starts: readings are buffered regardless, and the loop reconnects
    client.loop_start()

    def uplink() -> None:
        outbox = DurableOutbox(path)
        try:
            while not stop.is_set():
                if not client.is_connected():
                    time.sleep(0.2)
                    continue
                window = outbox.pending(WINDOW)
                if not window:
                    time.sleep(0.05)
                    continue
                published = []
                for entry in window:  # the window goes out in outbox order; the broker keeps one client's publishes in order
                    info = client.publish(
                        f"devices/{args.device}/{args.topic_suffix}",
                        json.dumps(entry.payload),
                        qos=1,
                    )
                    if info.rc != mqtt.MQTT_ERR_SUCCESS:
                        stats["publish_failed"] += 1
                        break
                    stats["published"] += 1
                    published.append(entry)
                deadline = time.time() + ACK_WAIT_S
                for entry in published:
                    while time.time() < deadline:
                        with lock:
                            if entry.event_id in acked_by_platform:
                                break
                        time.sleep(0.005)
                    else:
                        stats["ack_timeouts"] += 1
                        break  # the first missing acknowledgement ends the pass: nothing later is deleted ahead of it; the rest is sent again
                    outbox.ack(entry.event_id)
        finally:
            outbox.close()

    sequence = next_sequence(path)
    writer = DurableOutbox(path)
    sender = threading.Thread(target=uplink, daemon=True)
    sender.start()
    period = 1.0 / args.rate
    started = time.time()
    next_at = started
    while time.time() - started < args.seconds:
        if writer.append(reading(args.device, args.run, sequence)):
            stats["buffered"] += 1
        sequence += 1
        next_at += period
        pause = next_at - time.time()
        if pause > 0:
            time.sleep(pause)
    # the sensor stops; what is still pending is delivered before the process ends (or is left for the next process)
    settle = time.time() + 60
    while time.time() < settle and writer.counts().get("pending", 0) > 0:
        time.sleep(0.2)
    stop.set()
    sender.join(timeout=10)
    print(json.dumps({**stats, "counts": writer.counts()}), flush=True)
    writer.close()
    client.loop_stop()
    client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
