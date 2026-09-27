"""P05.03: MQTT-to-stream gateway.

Bridges validated device MQTT publishes (mTLS listener, P05.02) into the
Kafka-compatible central event backbone (ADR-0002; P05.01's Redpanda).
"No service consumes MQTT directly except the gateway" (ADR-0002).

Durable buffering between "accepted off MQTT" and "confirmed in Kafka"
reuses edge.outbox.DurableOutbox (P04.07) rather than reinventing it: the
same crash-safe, ordered, idempotent-enqueue, bounded-quota SQLite WAL
buffer that protects an edge instance from an uplink outage protects this
gateway from a Kafka outage. An application-level acknowledgement is
published back to the device's own ack topic only once Kafka has durably
confirmed the write - never speculatively - so a well-behaved device/edge
outbox knows exactly when it is safe to stop retrying.

Throughput and loss (measured on the target, where the first version sustained about 15 events/s): the MQTT subscription is PERSISTENT (a fixed client id, a
session the broker keeps while the gateway is away, queueing what it would have delivered) and its acknowledgements are MANUAL - an event is acknowledged to
the broker only after it is committed to the durable outbox, so a gateway that crashes or is restarted loses nothing the broker had delivered and not yet
stored; the broker redelivers it. Events are moved to the outbox in one transaction per batch, produced to Kafka with one flush per batch, and marked
delivered in one transaction, instead of one fsync and one round trip per event. Rows already delivered are pruned periodically.

Partitioning: produced with key=device_id, so Kafka's default partitioner
sends one device's events to one partition consistently, preserving
per-device ordering (ADR-0002: "partitioned by device/corridor key").
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import paho.mqtt.client as mqtt
from confluent_kafka import KafkaException, Producer

from backend import schema_validation
from backend.observability import BoundedCounter, configure, traced
from edge.outbox import DurableOutbox

SOURCE_ROOT = Path(__file__).resolve().parents[2]
OBSERVATION_SCHEMA_PATH = SOURCE_ROOT / "contracts" / "observation-envelope" / "v1" / "schema.json"

TELEMETRY_TOPIC_FILTER = "devices/+/telemetry"
KAFKA_TOPIC = "telemetry.events"
INTAKE_BATCH = 500  # events per outbox transaction
DRAIN_BATCH = 500  # events per Kafka flush
PRUNE_EVERY_S = 60.0  # delivered rows are removed this often

log = logging.getLogger("gateway")


@dataclass(frozen=True)
class GatewayConfig:
    mqtt_host: str
    mqtt_port: int
    mqtt_cafile: Path
    mqtt_certfile: Path
    mqtt_keyfile: Path
    kafka_bootstrap: str
    outbox_path: Path
    kafka_topic: str = KAFKA_TOPIC
    drain_interval_s: float = 0.2
    produce_flush_timeout_s: float = 5.0


def load_schema() -> dict:
    return json.loads(OBSERVATION_SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_envelope(payload: dict, schema: dict) -> str | None:
    """Returns None if valid, else a short human-readable rejection reason."""
    error = schema_validation.first_error(schema, payload)
    return None if error is None else f"schema_invalid: {error.message}"


def device_id_from_topic(topic: str) -> str | None:
    parts = topic.split("/")
    if len(parts) == 3 and parts[0] == "devices" and parts[2] == "telemetry":
        return parts[1]
    return None


class Gateway:
    """One gateway process: one MQTT connection, one Kafka producer, one
    durable outbox. Safe to construct once per process (matches
    DurableOutbox's single-writer contract)."""

    def __init__(self, config: GatewayConfig) -> None:
        configure("gateway")
        self.config = config
        self.schema = load_schema()
        # DurableOutbox is documented single-writer/single-reader (P04.07);
        # its sqlite3 connection is also unusable from any thread but the one
        # that created it. So the outbox is created *inside* _worker_loop,
        # not here - self.outbox does not exist until the worker thread
        # starts, and no other thread ever touches it. The MQTT callback
        # thread hands validated events across via self._intake, a
        # thread-safe queue, instead of calling into the outbox directly.
        self.outbox: DurableOutbox | None = None
        self.producer = Producer({"bootstrap.servers": config.kafka_bootstrap})
        self._intake: queue.Queue[tuple[dict, int, int]] = (
            queue.Queue()
        )  # (event, MQTT message id, QoS): acknowledged once stored
        self._delivered: list[
            tuple[str, str]
        ] = []  # (event id, device id) confirmed by Kafka, acknowledged in bulk after the flush
        self._stop = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self.stats = {
            "received": 0,
            "rejected": 0,
            "accepted_buffered": 0,
            "delivered": 0,
            "delivery_failed": 0,
        }

        self.metric_received = BoundedCounter(
            "gateway_events_received", "Telemetry events received over MQTT"
        )
        self.metric_rejected = BoundedCounter(
            "gateway_events_rejected", "Telemetry events rejected before buffering"
        )
        self.metric_delivered = BoundedCounter(
            "gateway_events_delivered", "Telemetry events durably delivered to Kafka"
        )

        self.mqtt = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="gateway", clean_session=False
        )
        self.mqtt.manual_ack_set(True)
        self.mqtt.tls_set(
            ca_certs=str(config.mqtt_cafile),
            certfile=str(config.mqtt_certfile),
            keyfile=str(config.mqtt_keyfile),
        )
        self.mqtt.on_connect = self._on_connect
        self.mqtt.on_message = self._on_message

    # -- MQTT ingest path ---------------------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        client.subscribe(TELEMETRY_TOPIC_FILTER, qos=1)
        log.info("connected, subscribed to %s", TELEMETRY_TOPIC_FILTER)

    def _on_message(self, client, userdata, msg) -> None:
        self.stats["received"] += 1
        self.metric_received.add()
        device_id = device_id_from_topic(msg.topic)
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._reject(device_id, event_id=None, reason=f"invalid_json: {exc}")
            client.ack(msg.mid, msg.qos)  # refused, not stored: nothing to redeliver
            return

        with traced(
            "gateway.receive", correlation_id=payload.get("event_id"), device_id=str(device_id)
        ):
            reason = validate_envelope(payload, self.schema)
            if reason is not None:
                self._reject(device_id, payload.get("event_id"), reason)
                client.ack(msg.mid, msg.qos)
                return

            self._intake.put((payload, msg.mid, msg.qos))

    def _reject(self, device_id: str | None, event_id: str | None, reason: str) -> None:
        self.stats["rejected"] += 1
        self.metric_rejected.add()
        if device_id is None:
            return
        self.mqtt.publish(
            f"devices/{device_id}/ack",
            json.dumps({"event_id": event_id, "status": "rejected", "reason": reason}),
            qos=1,
        )

    # -- Kafka drain path -----------------------------------------------------

    def _on_delivery(self, err, msg, event_id: str, device_id: str) -> None:
        if err is not None:
            self.stats["delivery_failed"] += 1
            log.warning("kafka delivery failed for %s: %s", event_id, err)
            return
        self._delivered.append((event_id, device_id))

    def _confirm_delivered(self) -> None:
        """Called on the worker thread after a flush: mark everything Kafka confirmed as delivered in ONE transaction, then tell the devices."""
        delivered, self._delivered = self._delivered, []
        if not delivered:
            return
        self.outbox.ack_many([event_id for event_id, _ in delivered])
        self.stats["delivered"] += len(delivered)
        for event_id, device_id in delivered:
            self.metric_delivered.add()
            self.mqtt.publish(
                f"devices/{device_id}/ack",
                json.dumps({"event_id": event_id, "status": "accepted"}),
                qos=1,
            )

    def _intake_once(self) -> int:
        """Move what is on the intake queue into the durable outbox in ONE transaction, then acknowledge each stored message to the broker. Runs only on
        _worker_thread (the outbox's sole owner). An event the outbox refuses (backlog at the hard quota) is NOT acknowledged: the broker keeps it
        and redelivers when the gateway next connects - bounded backpressure, never a silent drop."""
        batch: list[tuple[dict, int, int]] = []
        while len(batch) < INTAKE_BATCH:
            try:
                batch.append(self._intake.get_nowait())
            except queue.Empty:
                break
        if not batch:
            return 0
        outcomes = self.outbox.append_many([payload for payload, _, _ in batch])
        for (payload, mid, qos), outcome in zip(batch, outcomes, strict=True):
            if outcome == "full":
                log.warning("outbox full, not acking event %s", payload.get("event_id"))
                continue
            if outcome == "added":
                self.stats["accepted_buffered"] += 1
            self.mqtt.ack(mid, qos)
        return len(batch)

    def drain_once(self, limit: int = DRAIN_BATCH) -> int:
        """Attempt to produce every currently-pending outbox entry to Kafka (up to `limit`), with ONE flush for the batch. Returns how many were
        attempted. A broker outage makes produce()/flush() fail or time out; those entries simply stay pending for the next call - this IS the
        backpressure mechanism, with no separate in-memory buffer to overflow."""
        entries = self.outbox.pending(limit)
        for entry in entries:
            event = entry.payload
            device_id = event["device_id"]
            with traced("gateway.publish", correlation_id=entry.event_id, device_id=device_id):
                try:
                    self.producer.produce(
                        self.config.kafka_topic,
                        key=device_id.encode("utf-8"),
                        value=json.dumps(event, sort_keys=True).encode("utf-8"),
                        callback=lambda err, msg, eid=entry.event_id, did=device_id: (
                            self._on_delivery(err, msg, eid, did)
                        ),
                    )
                except BufferError:
                    self.producer.poll(
                        0.05
                    )  # the local queue is full: let deliveries drain, and try the rest next time
                    break
                except KafkaException as exc:
                    log.warning("produce() failed for %s: %s", entry.event_id, exc)
                    break
        if entries:
            self.producer.flush(self.config.produce_flush_timeout_s)
        self._confirm_delivered()
        return len(entries)

    def _worker_loop(self) -> None:
        # Created and closed on this thread only - see the note in __init__.
        self.outbox = DurableOutbox(self.config.outbox_path)
        try:
            last_prune = time.monotonic()
            while not self._stop.is_set():
                moved = self._intake_once()
                drained = self.drain_once()
                if time.monotonic() - last_prune > PRUNE_EVERY_S:
                    self.outbox.prune_acked()
                    last_prune = time.monotonic()
                if not moved and not drained:
                    self._stop.wait(self.config.drain_interval_s)
        finally:
            self.outbox.close()

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self.mqtt.connect(self.config.mqtt_host, self.config.mqtt_port, keepalive=30)
        self.mqtt.loop_start()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=5)
        self.mqtt.loop_stop()
        self.mqtt.disconnect()
        self.producer.flush(5)
