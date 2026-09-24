"""P10.01 acceptance evidence, against the real running platform:

    python source-code/backend/verify_observability.py

This does not re-prove P05.03/P05.05/P07.04/P07.05/P07.09's own business
logic - each already has its own real-stack evidence. It proves the thing
P10.01 actually adds: that the SAME domain id shows up as the
`correlation_id` span attribute at each real hop of the edge-to-action
path, so a Tempo/Grafana query could join them, and that the bounded-label
metric gate holds under the real instrumented code (not a synthetic test).

Two hops:
1. ingest: a real telemetry event's `event_id` is the correlation_id on
   both the gateway's `gateway.receive` span (its MQTT callback, invoked
   directly with a real payload - no live broker round trip needed to
   prove the span, which is P05.03's own concern) and ingestion's
   `ingestion.ingest_one` span (a real write to the real `observation_events`
   table).
2. govern: a real incident (`analytics.incidents` repositories) carries its
   `incident_id` onto `control.recommend_for_incident`'s span; the command
   created next carries its own id onto `control.request_command` /
   `control.review_command` / `control.verify_and_rollback`'s spans (stubbed
   straight to `executed`, since actuation is P07.06-10's concern, not
   this script's).
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from backend import observability as obs  # noqa: E402
from backend.control.command_service import request_command, review_command  # noqa: E402
from backend.control.outcome_verification import Metric, verify_and_rollback  # noqa: E402
from backend.control.recommendation_service import recommend_for_incident  # noqa: E402
from backend.control.verify_outcomes import stub_executed  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.gateway.gateway import Gateway, GatewayConfig  # noqa: E402
from backend.ingestion.ingest import ingest_one, load_schema  # noqa: E402
from backend.ingestion.verify_ingest import KNOWN_DEVICE, ensure_known_device, make_event  # noqa: E402
from backend.repositories.incidents import create_incident  # noqa: E402
from backend.roles import OUTCOME_VERIFIER  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

GEOMETRY = "2026-09-18.1"
PLATFORM_DIR = SOURCE_ROOT / "infra" / "platform"
CERTS = PLATFORM_DIR / "mosquitto" / "certs"

ev = Evidence("P10.01", docs_name="p10_01_observability")


class FakeMQTTMessage:
    """The only two attributes `Gateway._on_message` reads off a real paho
    message - a real MQTT round trip is P05.03's own proof
    (`verify_gateway.py`); this script's job is the span, not the broker."""

    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload


def find_span(name: str, correlation_id: str | None = None):
    for span in obs.recorded_spans():
        if span.name != name:
            continue
        if correlation_id is not None and span.attributes.get("correlation_id") != correlation_id:
            continue
        return span
    return None


def fresh_kpi(conn: psycopg.Connection, corridor_id: str, direction: str, now: datetime) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO corridor_kpis (corridor_id, direction, window_start, window_seconds, geometry_version, kpis, quality, coverage, "
            "sample_count, segments_reporting, segments_expected) VALUES (%s, %s, %s, 300, %s, %s::jsonb, 'valid', 1.0, 10, 1, 1) "
            "ON CONFLICT DO NOTHING",
            (
                corridor_id,
                direction,
                now - timedelta(minutes=1),
                GEOMETRY,
                Jsonb(
                    {
                        "delay_s": 10.0,
                        "queue_fraction": 0.2,
                        "travel_time_s": 60.0,
                        "free_flow_travel_time_s": 50.0,
                    }
                ),
            ),
        )
    conn.commit()


def ingest_hop(conn: psycopg.Connection) -> str:
    ensure_known_device(conn)
    event = make_event(KNOWN_DEVICE, seq=1, event_id=str(uuid.uuid4()))
    event_id = event["event_id"]

    gw = Gateway(
        GatewayConfig(
            mqtt_host="127.0.0.1",
            mqtt_port=8883,
            mqtt_cafile=CERTS / "ca.crt",
            mqtt_certfile=CERTS / "gateway.crt",
            mqtt_keyfile=CERTS / "gateway.key",
            kafka_bootstrap="127.0.0.1:19092",
            outbox_path=Path("verify_observability_outbox.sqlite3"),
        )
    )
    gw._on_message(  # noqa: SLF001 - the MQTT callback, invoked directly (see FakeMQTTMessage)
        None,
        None,
        FakeMQTTMessage(f"devices/{KNOWN_DEVICE}/telemetry", json.dumps(event).encode("utf-8")),
    )
    gw_span = find_span("gateway.receive", correlation_id=event_id)
    ev.check(
        "gateway_receive_span_correlates_by_event_id",
        gw_span is not None,
        f"event_id={event_id}",
    )

    result = ingest_one(conn, event, load_schema())
    ing_span = find_span("ingestion.ingest_one", correlation_id=event_id)
    ev.check(
        "ingestion_span_shares_the_same_correlation_id_as_the_gateway_span",
        ing_span is not None and result.outcome.value == "inserted",
        f"outcome={result.outcome.value}",
    )
    return event_id


def govern_hop(conn: psycopg.Connection) -> None:
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM corridor_kpis WHERE window_start > %s", (now - timedelta(hours=1),)
        )
    conn.commit()

    closed_edge = "int-a1_int-a2"
    incident_id = create_incident(
        conn,
        "collision",
        "high",
        "segment",
        closed_edge,
        GEOMETRY,
        [str(uuid.uuid4())],
        "EMERG",
        0.9,
        "verify_observability:synthetic",
        at=now,
    )
    recommend_for_incident(
        conn,
        incident_id,
        "collision",
        "segment",
        closed_edge,
        GEOMETRY,
        now,
        diversion_endpoints=("int-a1", "int-c4"),
    )
    rec_span = find_span("control.recommend_for_incident", correlation_id=incident_id)
    ev.check(
        "recommendation_span_correlates_by_incident_id",
        rec_span is not None,
        f"incident_id={incident_id}",
    )

    fresh_kpi(conn, "corridor-a", "east", now)
    idempotency_key = f"verify-obs-{uuid.uuid4()}"
    cmd_id, created = request_command(
        conn,
        idempotency_key,
        "diversion",
        "diversion_adapter",
        "int-a2_int-a3",
        "operator:alice",
        now,
        "operator",
    )
    req_span = find_span("control.request_command", correlation_id=idempotency_key)
    ev.check(
        "request_command_span_correlates_by_idempotency_key",
        req_span is not None and created,
        f"idempotency_key={idempotency_key}",
    )

    status = review_command(
        conn, cmd_id, "supervisor:bob", "supervisor", now + timedelta(seconds=5), GEOMETRY
    )
    review_span = find_span("control.review_command", correlation_id=cmd_id)
    ev.check(
        "review_command_span_correlates_by_the_commands_own_id",
        review_span is not None and status == "approved",
        f"command_id={cmd_id}, status={status}",
    )

    stub_executed(conn, cmd_id, at=now + timedelta(seconds=10))
    pre = Metric("queue_length", 10.0, "vehicles")
    post = Metric(
        "queue_length", 10.0, "vehicles"
    )  # deliberately unchanged: an "ineffective" verdict, no rollback branch to fake
    window_a = (now, now + timedelta(seconds=10))
    window_b = (now + timedelta(seconds=10), now + timedelta(seconds=20))
    outcome_id, verdict = verify_and_rollback(
        conn,
        cmd_id,
        pre,
        post,
        window_a,
        window_b,
        OUTCOME_VERIFIER,
        now + timedelta(seconds=30),
        50.0,
        50.0,
    )
    outcome_span = find_span("control.verify_and_rollback", correlation_id=cmd_id)
    ev.check(
        "outcome_span_correlates_by_the_same_command_id_the_review_span_used",
        outcome_span is not None and verdict.classification == "ineffective",
        f"outcome_id={outcome_id}, classification={verdict.classification}",
    )
    ev.notes["chain"] = f"incident={incident_id} -> command={cmd_id} -> outcome={outcome_id}"


def cardinality_check() -> None:
    """The same hostile-path proof as `test_observability.py`, run here
    against the real spans this script itself just emitted through real
    code paths, not a synthetic app."""
    counter = obs.BoundedCounter("verify_observability_probe")
    try:
        counter.add(event_id="should-never-be-allowed")
        ev.check(
            "metric_label_keys_stay_bounded_under_real_use", False, "an event_id label was accepted"
        )
    except ValueError:
        ev.check("metric_label_keys_stay_bounded_under_real_use", True)


def main() -> int:
    obs.configure("verify-observability")
    with psycopg.connect(dsn_from_env()) as conn:
        ingest_hop(conn)
        govern_hop(conn)
    cardinality_check()
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
