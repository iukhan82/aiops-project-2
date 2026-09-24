"""Real-time feeder for the demo world.

    python source-code/backend/demo/feeder.py [--reset] [--start-offset-min 45]

Replays the recorded simulator streams against the wall clock, so the operator UI
has live, current data to show:

* a 3-hour `am-peak-blockage` loop-detector run (P06 dataset), the recorded CAD
  export and AVL telemetry of the emergency scenario (P03.04) - every event
  re-timed by one constant offset so that "simulated minute N of the run" happens
  at (start + N minutes). Events keep their `simulated` truth label and go through
  the platform's own ingestion, so a WebSocket client sees them exactly as it would
  see live telemetry;
* the recorded 10-minute sensor stream (signal phase and timing, weather, road condition, pedestrian and cycle detectors),
  repeated end to end so those devices keep reporting;
* once a minute, on a second connection, the platform's own detectors, incident
  correlator, KPI computation and forecast package run over what has arrived.
  Incidents in the UI are what those produce - nothing here creates one.

The run has a fixed length (3 h). When it ends it restarts with new event ids, so a
demo can run all day. `--start-offset-min` says how far into the run "now" is: the
first recorded blockage begins at minute 53, so the default 45 shows one developing
within minutes of starting.
"""

from __future__ import annotations

import argparse
import copy
import signal
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics import congestion_service as cs  # noqa: E402
from backend.analytics import forecast_service as fs  # noqa: E402
from backend.analytics import incident_service as inc  # noqa: E402
from backend.analytics import kpi_service  # noqa: E402
from backend.analytics import safety_service as ss  # noqa: E402
from backend.analytics.correlation import load_policy  # noqa: E402
from backend.analytics.forecast_model import load_package  # noqa: E402
from backend.analytics.kpis import iso_z  # noqa: E402
from backend.control.heartbeat import heartbeat  # noqa: E402
from backend.control.recommendation_service import recommend_for_incident  # noqa: E402
from backend.demo import world  # noqa: E402
from backend.emergency.cad_avl_adapter import plan_replay  # noqa: E402
from backend.ingestion.ingest import ingest_one, load_schema  # noqa: E402
from backend.repositories import emergency as emergency_repo  # noqa: E402
from backend.repositories import recommendations as reco_repo  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

ANCHOR = datetime(2026, 9, 18, 9, 0, tzinfo=timezone.utc)
CYCLE = timedelta(hours=3)
WINDOW = timedelta(minutes=5)
RUN = "test/p06-test-s20260925-am-peak-blockage"
NAMESPACE = uuid.UUID("6f0c1d4e-3a52-4c1e-9d0a-8a6e0b1c2d3e")
FORECAST_PACKAGE = SOURCE_ROOT / "models" / "registry" / "traffic-forecast" / "1.0.0"
CAD_ACTOR = "cad-avl-adapter"
RECONNECT_S = 5.0


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def now() -> datetime:
    return datetime.now(timezone.utc)


def floor_window(moment: datetime) -> datetime:
    return datetime.fromtimestamp(
        int(moment.timestamp() // WINDOW.total_seconds() * WINDOW.total_seconds()), timezone.utc
    )


class Timeline:
    """One constant offset between the recorded clock and the wall clock, plus a token that keeps ids unique across cycles."""

    def __init__(self, start: datetime, token: str) -> None:
        self.start, self.token = start, token
        self.offset = start - ANCHOR

    def shift(self, ts: str) -> datetime:
        return parse(ts) + self.offset

    def new_id(self, original: str) -> str:
        return str(uuid.uuid5(NAMESPACE, f"{self.token}:{original}"))

    def event(self, event: dict) -> dict:
        moved = copy.deepcopy(event)
        moved["event_id"] = self.new_id(event["event_id"])
        moved["observation_time"] = iso_z(self.shift(event["observation_time"]))
        moved["ingest_time"] = iso_z(self.shift(event["ingest_time"]))
        return moved


def loop_events() -> list[tuple[datetime, dict]]:
    events = world.jsonl(world.DATASET / "test" / RUN.split("/", 1)[1] / "events.jsonl")
    return sorted(((parse(e["observation_time"]), e) for e in events), key=lambda pair: pair[0])


def emergency_streams() -> tuple[list[tuple[datetime, dict]], list, dict, dict]:
    avl = sorted(
        (
            (parse(e["observation_time"]), e)
            for e in world.jsonl(world.EMERGENCY / "avl_events.jsonl")
        ),
        key=lambda pair: pair[0],
    )
    calls = world.jsonl(world.EMERGENCY / "calls.jsonl")
    assignments = world.jsonl(world.EMERGENCY / "assignments.jsonl")
    steps = plan_replay(calls, assignments)
    return (
        avl,
        steps,
        {c["call_id"]: c for c in calls},
        {a["assignment_id"]: a for a in assignments},
    )


SENSOR_TYPES = {
    "signal.controller.spat",
    "weather.station.reading",
    "road.condition_sensor.reading",
    "vru.crossing_detector.demand",
    "vru.crossing_detector.clearance",
    "vru.cycle_counter.count",
}
SENSOR_CYCLE = timedelta(minutes=10)


def sensor_events() -> list[tuple[datetime, dict]]:
    """The recorded sensor run minus its loop counts, which the 3-hour run already provides for the same devices."""
    events = [
        e
        for e in world.jsonl(world.SENSORS / "observations.jsonl")
        if e["event_type"] in SENSOR_TYPES
    ]
    return sorted(((parse(e["observation_time"]), e) for e in events), key=lambda pair: pair[0])


class Feeder:
    def __init__(
        self,
        database: str,
        start_offset: timedelta,
        emergency_delay: timedelta,
        with_emergency: bool,
        detect_every: float,
    ) -> None:
        self.database = database
        self.detect_every = detect_every
        self.stop = threading.Event()
        self.schema = load_schema()
        self.loops = loop_events()
        self.cycle = 0
        self.timeline = Timeline(floor_window(now() - start_offset), "c0")
        self.loop_cursor = 0
        self.emergency_delay = emergency_delay
        self.with_emergency = with_emergency
        if with_emergency:
            self.avl, self.cad_steps, self.calls, self.assignments = emergency_streams()
            self.emergency_timeline = Timeline(now() + emergency_delay, "e0")
            self.avl_cursor = self.cad_cursor = 0
        self.sensors = sensor_events()
        self.sensor_timeline = Timeline(floor_window(now() - SENSOR_CYCLE), "s0")
        self.sensor_cursor = 0
        self.sensor_cycle = 0
        self.policy = load_policy()
        self.package = load_package(FORECAST_PACKAGE)
        self.last_forecast_origin: datetime | None = None
        self.stats = {
            "loop_events": 0,
            "sensor_events": 0,
            "avl_events": 0,
            "cad_steps": 0,
            "analysis_runs": 0,
            "analysis_errors": 0,
            "last_analysis_s": None,
        }

    # ---- ingestion (main thread) ----

    def ingest_due(self, conn: psycopg.Connection) -> None:
        moment = now()
        while self.loop_cursor < len(self.loops):
            source_time, event = self.loops[self.loop_cursor]
            if self.timeline.shift(iso_z(source_time)) > moment:
                break
            ingest_one(conn, self.timeline.event(event), self.schema)
            self.stats["loop_events"] += 1
            self.loop_cursor += 1
        if self.loop_cursor >= len(self.loops):
            self.cycle += 1
            self.timeline = Timeline(self.timeline.start + CYCLE, f"c{self.cycle}")
            self.loop_cursor = 0
        self.ingest_sensors_due(conn, moment)
        if self.with_emergency:
            self.ingest_emergency_due(conn, moment)

    def ingest_sensors_due(self, conn: psycopg.Connection, moment: datetime) -> None:
        while self.sensor_cursor < len(self.sensors):
            source_time, event = self.sensors[self.sensor_cursor]
            if source_time + self.sensor_timeline.offset > moment:
                break
            ingest_one(conn, self.sensor_timeline.event(event), self.schema)
            self.stats["sensor_events"] += 1
            self.sensor_cursor += 1
        if self.sensor_cursor >= len(self.sensors):
            self.sensor_cycle += 1
            self.sensor_timeline = Timeline(
                self.sensor_timeline.start + SENSOR_CYCLE, f"s{self.sensor_cycle}"
            )
            self.sensor_cursor = 0

    def ingest_emergency_due(self, conn: psycopg.Connection, moment: datetime) -> None:
        tl = self.emergency_timeline
        while self.avl_cursor < len(self.avl):
            source_time, event = self.avl[self.avl_cursor]
            if source_time + tl.offset > moment:
                break
            ingest_one(conn, tl.event(event), self.schema)
            self.stats["avl_events"] += 1
            self.avl_cursor += 1
        while self.cad_cursor < len(self.cad_steps):
            step = self.cad_steps[self.cad_cursor]
            at = step.at + tl.offset
            if at > moment:
                break
            self.apply_cad_step(conn, step, at)
            self.cad_cursor += 1
        if self.avl_cursor >= len(self.avl) and self.cad_cursor >= len(self.cad_steps):
            self.with_emergency = False

    def apply_cad_step(self, conn: psycopg.Connection, step, at: datetime) -> None:  # noqa: ANN001
        tl = self.emergency_timeline
        entity_id = tl.new_id(step.entity_id)
        try:
            if step.entity == "call":
                if step.to_status == "received":
                    c = self.calls[step.entity_id]
                    emergency_repo.create_call(
                        conn,
                        c["call_type"],
                        c["call_subtype"],
                        c["priority"],
                        c["location"],
                        c["geometry_version"],
                        c["source_reliability"],
                        c["truth_label"],
                        CAD_ACTOR,
                        at=at,
                        call_id=entity_id,
                    )
                else:
                    emergency_repo.transition_call(
                        conn, entity_id, step.to_status, CAD_ACTOR, step.note, at=at
                    )
            else:
                a = self.assignments[step.entity_id]
                if step.to_status == "assigned":
                    emergency_repo.create_assignment(
                        conn,
                        tl.new_id(a["call_id"]),
                        a["unit_id"],
                        a["agency"],
                        a["capability"],
                        a["route_alternatives"],
                        a["truth_label"],
                        CAD_ACTOR,
                        at=at,
                        assignment_id=entity_id,
                    )
                else:
                    emergency_repo.transition_assignment(
                        conn, entity_id, step.to_status, CAD_ACTOR, step.note, at=at
                    )
            self.stats["cad_steps"] += 1
        except (emergency_repo.InvalidTransition, emergency_repo.NotFound) as exc:
            print(f"cad step skipped: {exc}", flush=True)

    # ---- analysis (second connection) ----

    def analyse(self, conn: psycopg.Connection) -> None:
        moment = now()
        tl = self.timeline
        start = tl.start - timedelta(seconds=1)
        cs.detect_and_store(conn, start, moment, world.GEOMETRY)
        ss.detect_stall(conn, start, moment, world.GEOMETRY, world.DATASET / "devices.jsonl")
        inc.sync(conn, moment, world.GEOMETRY, self.policy)
        self.recommend(conn, moment)
        kpi_start = max(tl.start, floor_window(moment) - timedelta(minutes=45))
        kpi_service.compute_and_store(conn, kpi_start, moment, world.GEOMETRY)
        origin = floor_window(moment)
        if origin != self.last_forecast_origin and origin - tl.start >= WINDOW * 6:
            self.forecast(conn, origin, moment)
            self.last_forecast_origin = origin

    def recommend(self, conn: psycopg.Connection, moment: datetime) -> None:
        """Each active high or critical incident without a live recommendation gets one from the platform's own generators (a
        detour for a blocked road, a bounded green-time change for congestion); a recommendation nobody acted on in time expires."""
        reco_repo.expire_stale(conn, moment)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT incident_id, incident_type, network_element_type, network_element_id FROM incidents "
                "WHERE status IN ('open', 'acknowledged', 'investigating', 'escalated', 'reopened') AND duplicate_of IS NULL AND severity IN ('high', 'critical')"
            )
            active = cur.fetchall()
        for incident_id, kind, element_type, element_id in active:
            if reco_repo.active_for_trigger(conn, str(incident_id)):
                continue
            segment = element_id.rsplit("_", 1)[0] if element_type == "lane" else element_id
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT corridor_id, direction, to_node FROM network_segments WHERE edge_id = %s AND geometry_version = %s AND corridor_id IS NOT NULL",
                    (segment, world.GEOMETRY),
                )
                row = cur.fetchone()
                if row is None:
                    continue
                corridor, direction, to_node = row
                cur.execute(
                    "SELECT from_node FROM network_segments WHERE corridor_id = %s AND direction = %s AND geometry_version = %s ORDER BY order_index ASC LIMIT 1",
                    (corridor, direction, world.GEOMETRY),
                )
                first = cur.fetchone()[0]
                cur.execute(
                    "SELECT to_node FROM network_segments WHERE corridor_id = %s AND direction = %s AND geometry_version = %s ORDER BY order_index DESC LIMIT 1",
                    (corridor, direction, world.GEOMETRY),
                )
                last = cur.fetchone()[0]
            if kind in ("stalled_vehicle", "collision", "wrong_way", "flooding"):
                recommend_for_incident(
                    conn,
                    str(incident_id),
                    kind,
                    "segment",
                    segment,
                    world.GEOMETRY,
                    moment,
                    diversion_endpoints=(first, last),
                )
            elif kind in ("congestion", "spillback"):
                recommend_for_incident(
                    conn,
                    str(incident_id),
                    kind,
                    "intersection",
                    to_node,
                    world.GEOMETRY,
                    moment,
                    corridor_context=(corridor, direction),
                )

    def forecast(self, conn: psycopg.Connection, origin_end: datetime, as_of: datetime) -> None:
        """The forecast package indexes windows from the recorded timeline's anchor, so inputs and outputs are mapped through the same offset as the events."""
        offset = self.timeline.offset
        rows = fs.fetch_kpi_rows(conn, origin_end, world.GEOMETRY)
        for row in rows:
            row["window_start"] = iso_z(parse(row["window_start"]) - offset)
        records, _abstentions = fs.forecast_from_kpi_rows(
            rows, self.package, origin_end - offset, as_of - offset, world.GEOMETRY
        )
        shifted = []
        for record in records:
            record = dict(record)
            for key in ("predicted_at", "valid_from", "valid_until"):
                record[key] = iso_z(parse(record[key]) + offset)
            record["forecast_id"] = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"forecast:{record['network_element_id']}:{record['horizon_seconds']}:{record['valid_from']}:{record['model_id']}",
                )
            )
            shifted.append(record)
        if shifted:
            fs.store_forecasts(conn, shifted)

    def analysis_loop(self) -> None:
        while not self.stop.is_set():
            try:
                with psycopg.connect(dsn_from_env()) as conn:
                    while not self.stop.is_set():
                        started = time.monotonic()
                        try:
                            self.analyse(conn)
                            heartbeat(conn, "demo-feeder", dict(self.stats))
                            self.stats["analysis_runs"] += 1
                            self.stats["last_analysis_s"] = round(time.monotonic() - started, 2)
                        except psycopg.OperationalError:
                            raise
                        except Exception:  # noqa: BLE001
                            conn.rollback()
                            self.stats["analysis_errors"] += 1
                            traceback.print_exc()
                        self.stop.wait(self.detect_every)
            except psycopg.OperationalError as exc:
                # The database restarted or went away: the heartbeat stops meanwhile (platform status says so); reconnect rather than die.
                self.stats["analysis_errors"] += 1
                print(
                    f"analysis: database unavailable ({exc.__class__.__name__}); reconnecting",
                    flush=True,
                )
                self.stop.wait(RECONNECT_S)

    # ---- run ----

    def backfill(self, conn: psycopg.Connection) -> None:
        began = time.monotonic()
        self.ingest_due(conn)
        print(
            f"backfilled {self.stats['loop_events']} loop events up to minute {(now() - self.timeline.start).total_seconds() / 60:.0f} in {time.monotonic() - began:.0f}s",
            flush=True,
        )

    def reconnect(self) -> psycopg.Connection | None:
        while not self.stop.is_set():
            try:
                return psycopg.connect(dsn_from_env())
            except psycopg.OperationalError:
                self.stop.wait(RECONNECT_S)
        return None

    def run(self, backfill_only: bool) -> None:
        conn = psycopg.connect(dsn_from_env())
        try:
            self.backfill(conn)
            if backfill_only:
                self.analyse(conn)
                print("analysis complete", self.stats, flush=True)
                return
            worker = threading.Thread(target=self.analysis_loop, daemon=True)
            worker.start()
            last_report = time.monotonic()
            while not self.stop.is_set():
                try:
                    self.ingest_due(conn)
                except psycopg.OperationalError as exc:
                    print(
                        f"ingest: database unavailable ({exc.__class__.__name__}); reconnecting",
                        flush=True,
                    )
                    conn.close()
                    replacement = self.reconnect()
                    if replacement is None:
                        break
                    conn = replacement
                if time.monotonic() - last_report > 60:
                    print(f"{iso_z(now())} {self.stats}", flush=True)
                    last_report = time.monotonic()
                self.stop.wait(1.0)
            worker.join(timeout=30)
        finally:
            conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--database", default=world.DEFAULT_DATABASE)
    parser.add_argument(
        "--reset", action="store_true", help="drop and rebuild the demo database first"
    )
    parser.add_argument(
        "--start-offset-min",
        type=float,
        default=45.0,
        help="minutes into the recorded run that 'now' is",
    )
    parser.add_argument("--emergency-delay-s", type=float, default=30.0)
    parser.add_argument("--no-emergency", action="store_true")
    parser.add_argument("--detect-every-s", type=float, default=60.0)
    parser.add_argument(
        "--backfill-only",
        action="store_true",
        help="load the history, run the analysis once and exit",
    )
    args = parser.parse_args()
    world.load_platform_env()
    print(world.ensure(args.database, reset=args.reset), flush=True)
    feeder = Feeder(
        args.database,
        timedelta(minutes=args.start_offset_min),
        timedelta(seconds=args.emergency_delay_s),
        not args.no_emergency,
        args.detect_every_s,
    )
    signal.signal(signal.SIGINT, lambda *_: feeder.stop.set())
    signal.signal(signal.SIGTERM, lambda *_: feeder.stop.set())
    feeder.run(args.backfill_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
