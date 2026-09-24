"""P05.07 acceptance evidence, run against the real P05.01/P05.04 stack.
Starts the real FastAPI app under uvicorn (a real HTTP+WebSocket server on
a real socket, not FastAPI's in-process TestClient) and drives it with real
httpx/websockets clients:

    python source-code/backend/api/verify_api.py

Proves: versioned paths (/api/v1/...), cursor pagination with no gaps or
duplicates across pages, real persisted device/observation/network-state
data, and - the core of "live reconnect" - that a client disconnecting and
reconnecting with `?since=<cursor>` receives exactly what it missed while
disconnected, with no duplicate of what it already saw.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg
import uvicorn
import websockets

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

os.environ.setdefault(
    "AIOPS_AUTH_MODE", "off"
)  # this script tests data, not authentication; verify_auth.py proves the latter
from backend.api.app import app  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

HOST, PORT = "127.0.0.1", 8791
BASE = f"http://{HOST}:{PORT}"
WS_BASE = f"ws://{HOST}:{PORT}"
GEOMETRY_VERSION = "2026-09-18.1"
TEST_DEVICE = "verify-api-device"
TEST_LANE = "verify-api-lane"
OUTPUT_DIR = SOURCE_ROOT / "infra" / "platform" / "output"

results: dict[str, bool] = {}
notes: dict[str, str] = {}


def check(name: str, ok: bool, detail: str = "") -> None:
    results[name] = ok
    notes[name] = detail
    print(f"{'PASS' if ok else 'FAIL'}: {name} {detail}")


def setup_data(n_events: int) -> list[str]:
    with psycopg.connect(dsn_from_env()) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM observation_events WHERE device_id = %s", (TEST_DEVICE,))
            cur.execute(
                "INSERT INTO devices (device_id, device_type, deployment_type, agency_scope, geometry_version, "
                "location, lane_id, status, registered_at, privacy_classification, retention_class) VALUES "
                "(%s, 'traffic_loop', 'simulated', 'city-traffic-ops', %s, "
                "ST_SetSRID(ST_MakePoint(74.3587, 31.5204), 4326)::geography, %s, 'active', now(), 'none', 'standard') "
                "ON CONFLICT (device_id) DO UPDATE SET status = 'active'",
                (TEST_DEVICE, GEOMETRY_VERSION, TEST_LANE),
            )
        conn.commit()
        ids = [insert_event(conn) for _ in range(n_events)]
    return ids


def insert_event(conn: psycopg.Connection) -> str:
    event_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO observation_events (
                event_id, event_type, device_id, agency_scope, observation_time, ingest_time,
                sequence_number, clock_quality, geometry_version, location, lane_id,
                measurements, truth_label, privacy_classification, retention_class, content_sha256
            ) VALUES (
                %s, 'traffic.loop_detector.count', %s, 'city-traffic-ops', %s, %s, %s, 'synced', %s,
                ST_SetSRID(ST_MakePoint(74.3587, 31.5204), 4326)::geography, %s, %s, 'simulated', 'none', 'standard', %s
            )
            """,
            (
                event_id,
                TEST_DEVICE,
                now,
                now,
                int(now.timestamp()),
                GEOMETRY_VERSION,
                TEST_LANE,
                json.dumps(
                    [
                        {
                            "name": "vehicle_count",
                            "value": 1,
                            "unit": "count",
                            "quality": "valid",
                            "confidence": 0.9,
                        }
                    ]
                ),
                str(uuid.uuid4()),
            ),
        )
    conn.commit()
    return event_id


def run_server() -> uvicorn.Server:
    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("uvicorn did not start in time")


async def paginate_all(client: httpx.AsyncClient, path: str, params: dict) -> list[dict]:
    items: list[dict] = []
    cursor = None
    seen_cursors = set()
    while True:
        p = {**params, "limit": 1}
        if cursor:
            p["cursor"] = cursor
        r = await client.get(path, params=p)
        r.raise_for_status()
        page = r.json()
        items.extend(page["items"])
        if page["next_cursor"] is None:
            break
        assert page["next_cursor"] not in seen_cursors, "cursor repeated - pagination loop"
        seen_cursors.add(page["next_cursor"])
        cursor = page["next_cursor"]
    return items


async def main() -> int:
    event_ids = setup_data(n_events=3)
    server = run_server()
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=10) as client:
            r = await client.get("/api/v1/devices/does-not-exist")
            check("unknown_device_returns_404", r.status_code == 404)

            r = await client.get(f"/api/v1/devices/{TEST_DEVICE}")
            check(
                "known_device_returns_real_data",
                r.status_code == 200 and r.json()["device_id"] == TEST_DEVICE,
                detail=str(r.json()),
            )

            devices = await paginate_all(client, "/api/v1/devices", {})
            check(
                "device_pagination_no_gap_no_duplicate",
                TEST_DEVICE in {d["device_id"] for d in devices}
                and len(devices) == len({d["device_id"] for d in devices}),
                detail=f"n_devices={len(devices)}",
            )

            observations = await paginate_all(
                client, "/api/v1/observations", {"device_id": TEST_DEVICE}
            )
            obs_ids = {o["event_id"] for o in observations}
            check(
                "observation_history_pagination_returns_exact_set",
                obs_ids == set(event_ids),
                detail=f"expected={len(event_ids)} got={len(obs_ids)}",
            )

            r = await client.get(f"/api/v1/network-state/lane/{TEST_LANE}")
            check(
                "network_state_endpoint_returns_real_aggregate",
                r.status_code == 200 and r.json()["network_element_id"] == TEST_LANE,
                detail=str(r.json()),
            )

            r = await client.get("/api/v1/network-state/lane/no-such-lane-ever")
            check("network_state_unknown_element_returns_404", r.status_code == 404)

            r = await client.get("/api/v1/network-state/segment/no-such-segment-ever")
            check("network_state_unknown_segment_returns_404", r.status_code == 404)

            r = await client.get("/api/v1/network-state/zone/x")
            check(
                "network_state_unsupported_type_returns_501_not_silently_wrong",
                r.status_code == 501,
            )

        # ---- live reconnect ----
        seen_first: list[str] = []
        last_received_at = None
        async with websockets.connect(f"{WS_BASE}/api/v1/live?device_id={TEST_DEVICE}") as ws:
            deadline = time.time() + 5
            while len(seen_first) < 3 and time.time() < deadline:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                seen_first.append(msg["event_id"])
                last_received_at = msg["received_at"]
        check(
            "live_feed_sends_backlog_on_connect",
            set(seen_first) == set(event_ids),
            detail=f"got={len(seen_first)}",
        )

        # insert an event WHILE disconnected - this is what "no gap" tests
        with psycopg.connect(dsn_from_env()) as conn:
            missed_event_id = insert_event(conn)

        seen_second: list[str] = []
        since_q = urllib.parse.quote(last_received_at, safe="")
        async with websockets.connect(
            f"{WS_BASE}/api/v1/live?device_id={TEST_DEVICE}&since={since_q}"
        ) as ws:
            deadline = time.time() + 5
            while len(seen_second) < 1 and time.time() < deadline:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                seen_second.append(msg["event_id"])

        check(
            "reconnect_with_since_receives_missed_event_not_old_ones",
            seen_second == [missed_event_id],
            detail=f"seen_second={seen_second} expected=[{missed_event_id}]",
        )
        check(
            "reconnect_never_redelivers_already_seen_events",
            not (set(seen_second) & set(seen_first)),
        )

        # ---- scoping: a feed for a DIFFERENT device must not see our test events ----
        other_device_saw_ours = False
        async with websockets.connect(
            f"{WS_BASE}/api/v1/live?device_id=some-other-device-entirely"
        ) as ws:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                other_device_saw_ours = msg["event_id"] in set(event_ids) | {missed_event_id}
            except TimeoutError:
                pass
        check("live_feed_device_scoping_excludes_other_devices", not other_device_saw_ours)
    finally:
        server.should_exit = True
        await asyncio.sleep(0.5)

    all_passed = all(results.values())
    evidence = {
        "task": "P05.07",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": results,
        "notes": notes,
        "all_passed": all_passed,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "p05_07_evidence.json"
    out_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f">> Evidence written to {out_path}")
    print("P05.07: ALL CHECKS PASSED" if all_passed else "P05.07: ONE OR MORE CHECKS FAILED")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
