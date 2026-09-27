#!/usr/bin/env python3
"""P12.02 (REC-01, and acceptance scenario 6): edge outage recovery - every durably buffered reading replays in order, acknowledged, with no accepted duplicate.

    python source-code/acceptance/lab_edge_outage.py        # needs the platform stack up; about two minutes

An edge site (`edge_site.py`: readings committed to the durable outbox before any send, sent in order over the broker's mutually-authenticated listener, deleted only on the
platform's own application-level acknowledgement) uplinks to the REAL chain - broker, gateway, Kafka, ingestion, PostgreSQL - while three faults are injected:

  1. uplink loss     the MQTT broker container is stopped for 12 s and started again
  2. an edge crash   the edge process is killed (not stopped) mid-stream and started again on the same outbox file
  3. a gateway restart   the gateway is stopped for 3 s and started again

The gateway and ingestion are the platform's own code on their own topic; the fault targets are real containers and a real killed process. What is then read back:

  * the outbox file: every reading the site ever buffered (that is the set the guarantee covers - what the sensor produced while the process was dead was never buffered);
  * the Kafka log of the topic, in order: how many messages, how many were the same reading again (a resend after the crash), whether first occurrences are in sequence order;
  * PostgreSQL: how many rows those readings became - exactly one each.

The local broker has a two-listener configuration in which mosquitto keeps no offline queue for the gateway's persistent session (P11.06 found this on the target): readings published
while the gateway is down are not queued for it here. That is the case the application-level acknowledgement exists for - the site never got an acknowledgement for them, so it re-sent -
and this lab is the end-to-end proof that the guarantee does not depend on that queue.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import psycopg
from confluent_kafka import Consumer, KafkaError
from confluent_kafka.admin import AdminClient, NewTopic

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.evidence import Evidence  # noqa: E402
from backend.gateway.gateway import Gateway, GatewayConfig  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402
from edge.outbox import DurableOutbox  # noqa: E402

CERTS = SOURCE_ROOT / "infra" / "platform" / "mosquitto" / "certs"
DEVICE = "corridor-a-int-03-loop-01"
RUN = f"rec01-{uuid.uuid4().hex[:8]}"
TOPIC = f"telemetry.events.p12_{RUN.replace('-', '_')}"
BOOTSTRAP = "127.0.0.1:19092"
SECONDS = 50
ev = Evidence("P12.02", "p12_02_rec01_edge_outage", docs_name="p12_02_rec01_edge_outage")


def wsl(command: str, timeout: float = 90) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["wsl.exe", "-e", "bash", "-lc", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def broker_listening() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 8883), timeout=1):
            return True
    except OSError:
        return False


def wait_broker(up: bool, seconds: float = 60) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if broker_listening() == up:
            return True
        time.sleep(0.5)
    return False


def start_edge(outbox: Path, seconds: float) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            str(SOURCE_ROOT / "acceptance" / "edge_site.py"),
            "--outbox",
            str(outbox),
            "--device",
            DEVICE,
            "--run",
            RUN,
            "--rate",
            "25",
            "--seconds",
            str(seconds),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def make_gateway(outbox: Path) -> Gateway:
    return Gateway(
        GatewayConfig(
            mqtt_host="127.0.0.1", mqtt_port=8883, mqtt_cafile=CERTS / "ca.crt", mqtt_certfile=CERTS / "gateway.crt", mqtt_keyfile=CERTS / "gateway.key",
            kafka_bootstrap=BOOTSTRAP, outbox_path=outbox, kafka_topic=TOPIC,
        )
    )  # fmt: skip


def read_kafka_log() -> list[tuple[str, int]]:
    """(event id, sequence number) of every message on the topic, in log order."""
    consumer = Consumer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "group.id": f"{RUN}-reader",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([TOPIC])
    out: list[tuple[str, int]] = []
    idle = 0
    while idle < 6:
        msg = consumer.poll(1.0)
        if msg is None:
            idle += 1
            continue
        if msg.error():
            if msg.error().code() != KafkaError._PARTITION_EOF:  # noqa: SLF001
                idle += 1
            continue
        idle = 0
        body = json.loads(msg.value())
        out.append((body["event_id"], body["sequence_number"]))
    consumer.close()
    return out


def main() -> int:  # noqa: PLR0915
    work = Path(tempfile.mkdtemp(prefix="p12-rec01-"))
    edge_outbox, gateway_outbox = work / "edge.sqlite", work / "gateway.sqlite"
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP})
    for fut in admin.create_topics(
        [NewTopic(TOPIC, num_partitions=1, replication_factor=1)]
    ).values():
        fut.result(timeout=20)
    with psycopg.connect(dsn_from_env()) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO devices (device_id, device_type, deployment_type, agency_scope, geometry_version, location, status, registered_at, privacy_classification, retention_class) "
            "VALUES (%s, 'inductive_loop', 'simulated', 'city-traffic-ops', '2026-09-18.1', ST_SetSRID(ST_MakePoint(74.3587, 31.5204), 4326)::geography, 'active', now(), 'none', 'standard') "
            "ON CONFLICT (device_id) DO NOTHING",
            (DEVICE,),
        )
        conn.commit()

    ingestion = subprocess.Popen(
        [sys.executable, "-c", f"from backend.ingestion.ingest import run; print(run('{BOOTSTRAP}', group_id='{RUN}-ingestion', topic='{TOPIC}'))"],
        cwd=SOURCE_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    gateway = make_gateway(gateway_outbox)
    gateway.start()
    time.sleep(2)
    started = time.time()
    edge = start_edge(edge_outbox, SECONDS)
    facts: dict = {
        "pending_seen_during_outage": 0,
        "broker_down_confirmed": False,
        "edge_killed": False,
        "edge_restarted": False,
        "gateway_restarted": False,
    }

    def at(seconds: float) -> None:
        pause = started + seconds - time.time()
        if pause > 0:
            time.sleep(pause)

    def pending_now() -> int:
        try:
            with DurableOutbox(edge_outbox) as box:
                return box.counts()["pending"]
        except Exception:  # noqa: BLE001 - the file may be mid-write
            return 0

    try:
        # ---- 1. uplink loss
        at(6)
        wsl("docker stop aiops-mqtt-broker")
        facts["broker_down_confirmed"] = wait_broker(False, 30)
        for _ in range(6):
            time.sleep(2)
            facts["pending_seen_during_outage"] = max(
                facts["pending_seen_during_outage"], pending_now()
            )
        wsl("docker start aiops-mqtt-broker")
        facts["broker_back"] = wait_broker(True, 60)
        # ---- 2. the edge process is killed, not stopped
        at(26)
        edge.kill()
        edge.wait(timeout=10)
        facts["edge_killed"] = True
        facts["pending_at_kill"] = pending_now()
        time.sleep(2)
        edge = start_edge(edge_outbox, max(10.0, SECONDS - (time.time() - started)))
        facts["edge_restarted"] = True
        # ---- 3. the gateway restarts
        at(36)
        gateway.stop()
        time.sleep(3)
        gateway = make_gateway(gateway_outbox)
        gateway.start()
        facts["gateway_restarted"] = True
        edge_output, _ = edge.communicate(timeout=SECONDS + 120)
        facts["edge_report"] = edge_output.strip().splitlines()[-1] if edge_output.strip() else ""
    finally:
        # give the chain time to finish what it holds
        time.sleep(1)

    with DurableOutbox(edge_outbox) as box:
        rows = box.all_entries()
        counts = box.counts()
    expected = {
        r.event_id: r.seq - 1 for r in rows
    }  # the sensor's sequence number of each buffered reading
    deadline = time.time() + 90
    stored: dict[str, int] = {}
    total_rows = distinct = 0
    while time.time() < deadline:
        with psycopg.connect(dsn_from_env()) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT event_id::text, sequence_number FROM observation_events WHERE correlation_id = %s",
                (RUN,),
            )
            rows_db = cur.fetchall()
        stored = {str(e): int(s) for e, s in rows_db}
        total_rows, distinct = len(rows_db), len({r[0] for r in rows_db})
        if set(expected) <= set(stored):
            break
        time.sleep(2)
    ingestion.kill()
    gateway.stop()
    log = read_kafka_log()

    first_seen: dict[str, int] = {}
    for event_id, sequence in log:
        first_seen.setdefault(event_id, sequence)
    order = list(first_seen.values())
    in_order = all(b > a for a, b in zip(order, order[1:], strict=False))
    duplicates_in_log = len(log) - len(first_seen)
    missing = sorted(set(expected) - set(stored))

    ev.check(
        "the_uplink_really_was_down_the_broker_stopped_and_refused_connections",
        facts["broker_down_confirmed"] and facts.get("broker_back", False),
        f"down confirmed {facts['broker_down_confirmed']}, back {facts.get('broker_back')}",
    )
    ev.check(
        "the_readings_were_buffered_on_disk_while_the_uplink_was_down",
        facts["pending_seen_during_outage"] > 0,
        f"up to {facts['pending_seen_during_outage']} readings waiting in the outbox during the outage",
    )
    ev.check(
        "the_edge_process_was_killed_mid_stream_and_started_again_on_the_same_outbox",
        facts["edge_killed"] and facts["edge_restarted"],
        f"{facts.get('pending_at_kill')} readings pending at the moment of the kill",
    )
    ev.check(
        "the_gateway_was_stopped_and_started_again_during_the_stream", facts["gateway_restarted"]
    )
    ev.check(
        "the_site_buffered_a_meaningful_number_of_readings",
        len(expected) >= 500,
        f"{len(expected)} readings buffered in {SECONDS} s (two processes)",
    )
    ev.check(
        "every_durably_buffered_reading_reached_the_database_none_missing",
        not missing,
        f"{len(expected)} buffered, {len(set(expected) & set(stored))} stored, missing {len(missing)}",
    )
    ev.check(
        "zero_accepted_duplicates_one_row_per_reading",
        total_rows == distinct == len(stored) and total_rows == len(expected),
        f"{total_rows} rows, {distinct} distinct event ids, {len(expected)} buffered",
    )
    ev.check(
        "readings_replay_in_order_first_occurrences_in_the_log_are_in_sequence",
        in_order and len(order) == len(expected),
        f"{len(order)} distinct readings in the log, in sequence order: {in_order}",
    )
    ev.check(
        "every_reading_was_acknowledged_by_the_platform_and_the_outbox_is_empty",
        counts["pending"] == 0 and counts["acked"] == len(expected),
        f"outbox {counts}",
    )
    ev.metrics = {
        "run": RUN, "topic": TOPIC, "readings_buffered": len(expected), "rows_in_database": total_rows, "messages_in_the_log": len(log), "duplicate_messages_in_the_log_rejected_downstream": duplicates_in_log,
        "outbox": counts, "facts": facts, "seconds_of_sensing": SECONDS,
    }  # fmt: skip
    ev.notes["scope"] = (
        "real broker container, real gateway and ingestion code, real Kafka and PostgreSQL; one edge site of one device, 25 readings per second for 50 s; the outbox is the platform's DurableOutbox"
    )
    ev.notes["not_proven"] = (
        "several edges at once; an outage longer than the outbox quota (the quota's refusal is P04.07's unit tests); a broker or Kafka crash in the middle of a write (the gateway's own outbox and the application acknowledgement cover it and are exercised by P05.03)"
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
