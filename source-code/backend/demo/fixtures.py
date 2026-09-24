"""Small, real-path fixtures for the browser tests, in the demo database.

    python source-code/backend/demo/fixtures.py incident --severity high
    python source-code/backend/demo/fixtures.py unit-position --unit ambulance-1 --intersection int-b4 [--age-minutes 30 --reset]
    python source-code/backend/demo/fixtures.py loop-event --device loop-int-a1-int-a2 --count 57
    python source-code/backend/demo/fixtures.py policy-outage
    python source-code/backend/demo/fixtures.py probe-device [--id probe-loop-latency]
    python source-code/backend/demo/fixtures.py as-user --user fin.hassan --method POST --path /api/v1/commands --json '{...}'
    python source-code/backend/demo/fixtures.py demo-running --count 2      # and: demo-clear

Each prints one JSON object. Incidents come from `repositories.incidents.create_incident` and telemetry goes through the platform's
own ingestion (`ingest_one`), so a test starts from a state the running system could really be in. The test then acts through the
browser, the API and the database triggers as a person would.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.api import oidc_client  # noqa: E402
from backend.control.command_service import request_command  # noqa: E402
from backend.demo import world  # noqa: E402
from backend.ingestion.ingest import ingest_one, load_schema  # noqa: E402
from backend.loader.platform_loader import register_devices  # noqa: E402
from backend.repositories import commands as command_repo  # noqa: E402
from backend.repositories import incidents as incident_repo  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def incident(conn: psycopg.Connection, args: argparse.Namespace) -> dict:
    element = f"e2e-{uuid.uuid4().hex[:8]}"
    incident_id = incident_repo.create_incident(
        conn,
        args.type,
        args.severity,
        "lane",
        f"{element}_0",
        world.GEOMETRY,
        [str(uuid.uuid4())],
        args.owner,
        args.confidence,
        "e2e-fixture",
    )
    return {"incident_id": incident_id, "element": f"{element}_0"}


def unit_position(conn: psycopg.Connection, args: argparse.Namespace) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ST_Y(location::geometry), ST_X(location::geometry) FROM intersections WHERE intersection_id = %s",
            (args.intersection,),
        )
        row = cur.fetchone()
        cur.execute("SELECT agency_scope FROM devices WHERE device_id = %s", (f"avl-{args.unit}",))
        agency = cur.fetchone()
    if row is None or agency is None:
        raise SystemExit(f"unknown intersection {args.intersection} or unit {args.unit}")
    if args.reset:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM observation_events WHERE device_id = %s", (f"avl-{args.unit}",)
            )
        conn.commit()
    now = datetime.now(timezone.utc) - timedelta(minutes=args.age_minutes)
    event = {
        "schema_version": "1.0.0",
        "event_id": str(uuid.uuid4()),
        "event_type": "emergency.unit_position.avl",
        "device_id": f"avl-{args.unit}",
        "agency_scope": agency[0],
        "observation_time": stamp(now),
        "ingest_time": stamp(now),
        "sequence_number": int(time.time() * 1000),
        "clock_quality": "synced",
        "geometry_version": world.GEOMETRY,
        "location": {"coordinate_reference": "EPSG:4326", "latitude": row[0], "longitude": row[1]},
        "measurements": [
            {"name": "speed", "value": 0.0, "unit": "m_s-1", "quality": "valid", "confidence": 0.9}
        ],
        "truth_label": "simulated",
        "privacy_classification": "none",
        "retention_class": "standard",
    }
    result = ingest_one(conn, event, load_schema())
    return {
        "outcome": result.outcome.value,
        "event_id": event["event_id"],
        "observation_time": event["observation_time"],
    }


def loop_event(conn: psycopg.Connection, args: argparse.Namespace) -> dict:
    """One detector count with a distinctive value, so a test can see exactly that reading arrive on screen."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT corridor_id, lane_id, ST_Y(location::geometry), ST_X(location::geometry) FROM devices WHERE device_id = %s",
            (args.device,),
        )
        row = cur.fetchone()
    if row is None:
        raise SystemExit(f"unknown device {args.device}")
    now = datetime.now(timezone.utc)
    location = {
        "coordinate_reference": "EPSG:4326",
        "latitude": row[2],
        "longitude": row[3],
        "corridor_id": row[0],
    }
    if row[1]:
        location["lane_id"] = row[1]
    event = {
        "schema_version": "1.0.0",
        "event_id": str(uuid.uuid4()),
        "event_type": "traffic.loop_detector.count",
        "device_id": args.device,
        "agency_scope": "city-traffic-ops",
        "observation_time": stamp(now),
        "ingest_time": stamp(now),
        "sequence_number": int(time.time() * 1000),
        "clock_quality": "synced",
        "geometry_version": world.GEOMETRY,
        "location": location,
        "measurements": [
            {
                "name": "vehicle_count",
                "value": args.count,
                "unit": "count",
                "quality": "valid",
                "confidence": 0.98,
            }
        ],
        "truth_label": "simulated",
        "privacy_classification": "none",
        "retention_class": "standard",
    }
    result = ingest_one(conn, event, load_schema())
    with conn.cursor() as cur:
        cur.execute(
            "SELECT received_at FROM observation_events WHERE event_id = %s", (event["event_id"],)
        )
        received = cur.fetchone()
    return {
        "outcome": result.outcome.value,
        "event_id": event["event_id"],
        "observation_time": event["observation_time"],
        "received_at_ms": int(received[0].timestamp() * 1000) if received else None,
        "emitted_at_ms": int(time.time() * 1000),
    }


def policy_outage(conn: psycopg.Connection, args: argparse.Namespace) -> dict:
    """A fresh command whose approval was refused by an injected policy outage: it waits, it is not approved (fault injection)."""
    command_id, _ = request_command(
        conn,
        f"e2e-outage-{uuid.uuid4().hex[:8]}",
        "variable_message_sign",
        "vms_adapter",
        "int-a3_int-a4",
        "alex.chen",
        datetime.now(timezone.utc),
        "operator",
        ttl_s=1800.0,
        params={"message": "Slow traffic ahead"},
    )
    command_repo.apply_policy_unavailable(
        conn,
        command_id,
        "internal",
        "policy evaluation failed: injected outage (fault injection), so the command waits rather than being approved",
    )
    return {"command_id": command_id}


def probe_device(conn: psycopg.Connection, args: argparse.Namespace) -> dict:
    """A detector of its own, cloned from a recorded one, that nothing in the demo replay reports for: a test that times a reading to the screen
    then sees exactly its own reading, not one the replay overwrote a moment later."""
    source = next(
        d
        for d in world.jsonl(world.DATASET / "devices.jsonl")
        if d["device_id"] == "loop-int-a1-int-a2"
    )
    added = register_devices(conn, [{**source, "device_id": args.id}])
    return {"device_id": args.id, "registered": bool(added)}


def as_user(conn: psycopg.Connection, args: argparse.Namespace) -> dict:
    """One call to the running operator API with a real access token for a named demo person - so a test can produce, for instance, a refused request."""
    identities = json.loads(
        (SOURCE_ROOT / "infra" / "platform" / "output" / "demo_identities.json").read_text(
            encoding="utf-8"
        )
    )
    token = oidc_client.login(args.user, identities[args.user]["password"]).access_token
    response = httpx.request(
        args.method,
        f"http://127.0.0.1:8100{args.path}",
        headers={"Authorization": f"Bearer {token}"},
        json=json.loads(args.json) if args.json else None,
        timeout=30,
    )
    try:
        body = response.json()
    except ValueError:
        body = None
    return {"status": response.status_code, "body": body}


def demo_running(conn: psycopg.Connection, args: argparse.Namespace) -> dict:
    """Runs left 'running', so the scenario-control bound is really reached. `demo-clear` resets them."""
    with conn.cursor() as cur:
        for _ in range(args.count):
            cur.execute(
                "INSERT INTO scenario_runs (run_id, scenario, status, started_by, started_at) VALUES (%s, 'manifest', 'running', 'e2e-fixture', now())",
                (str(uuid.uuid4()),),
            )
    conn.commit()
    return {"running": args.count}


def demo_clear(conn: psycopg.Connection, args: argparse.Namespace) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE scenario_runs SET status = 'reset', completed_at = now() WHERE status = 'running'"
        )
        cleared = cur.rowcount
    conn.commit()
    return {"cleared": cleared}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=world.DEFAULT_DATABASE)
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("incident")
    a.add_argument("--severity", default="high", choices=["low", "medium", "high", "critical"])
    a.add_argument("--type", default="congestion")
    a.add_argument("--owner", default="OPS")
    a.add_argument("--confidence", type=float, default=0.7)
    b = sub.add_parser("unit-position")
    b.add_argument("--unit", required=True)
    b.add_argument("--intersection", required=True)
    b.add_argument(
        "--age-minutes",
        type=float,
        default=0.0,
        help="report the position as this old, to stage a unit that has gone quiet",
    )
    b.add_argument(
        "--reset",
        action="store_true",
        help="forget the unit's earlier positions first, so this one is the newest",
    )
    c = sub.add_parser("loop-event")
    c.add_argument("--device", required=True)
    c.add_argument("--count", type=int, required=True)
    sub.add_parser("policy-outage")
    f = sub.add_parser("probe-device")
    f.add_argument("--id", default="probe-loop-latency")
    d = sub.add_parser("as-user")
    d.add_argument("--user", required=True)
    d.add_argument("--method", default="GET")
    d.add_argument("--path", required=True)
    d.add_argument("--json", default=None)
    e = sub.add_parser("demo-running")
    e.add_argument("--count", type=int, default=2)
    sub.add_parser("demo-clear")
    args = parser.parse_args()
    world.load_platform_env()
    world.use_database(args.database)
    with psycopg.connect(dsn_from_env()) as conn:
        result = {
            "incident": incident,
            "unit-position": unit_position,
            "loop-event": loop_event,
            "policy-outage": policy_outage,
            "probe-device": probe_device,
            "as-user": as_user,
            "demo-running": demo_running,
            "demo-clear": demo_clear,
        }[args.command](conn, args)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
