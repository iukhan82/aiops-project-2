#!/usr/bin/env python3
"""P12.02 (SAFE-03): stale network-state and signal-state records trigger fail-safe behaviour in every dependent service, never silent use as fresh.

    python source-code/acceptance/lab_stale_state.py        # needs the platform stack (PostgreSQL) up; about a minute

The target reads: "100% of stale network-state or signal-state records (`freshness_status = stale`) trigger fail-safe behavior in dependent services, never silent use as fresh".
A "dependent service" is anything that reads the state and decides or shows something from it. This drives each of them with the same fault - the readings stop, time moves
on - beside a control in which they are fresh, so a consumer that ignored freshness altogether would fail the control's opposite:

  the state service        serves the old reading, labelled `stale` (a record does not get fresher by being served again)
  the KPI record           the corridor record built from a stored window is `stale` once the window is older than its budget
  the forecast service     refuses ("stale input") rather than forecast from old windows
  routing (ETA)            does not use an old corridor window: with a measured delay of 5x free flow, a fresh window lengthens the ETA and a stale one leaves it at free flow
  signal-plan recommendations   fall back to a stated planning default, say so in their constraints and lower their confidence
  the command policy       every protected target kind (a signal, a corridor segment, a corridor, and a segment with no corridor) has no fresh evidence once the readings are old

The dispatch service's refusal to route from an old unit position, and the console's labelling of stale values, are exercised by their own evidence (cited by the matrix). The
data-quality catalogue's faults are detected by P10.10; a fault that makes a reading LOOK fresh and wrong (a stuck sensor) is not a staleness fault and is not claimed here.
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.correlation import Topology  # noqa: E402
from backend.analytics.forecast_model import load_package  # noqa: E402
from backend.analytics.forecast_service import MAX_INPUT_AGE_S, forecast_from_kpi_rows  # noqa: E402
from backend.analytics.kpi_service import load_segments  # noqa: E402
from backend.analytics.kpis import to_network_state_record  # noqa: E402
from backend.control import policy  # noqa: E402
from backend.control.signal_plan import DEFAULT_BOUNDS as SIGNAL_BOUNDS  # noqa: E402
from backend.control.signal_plan import build_signal_recommendation  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.loader.platform_loader import load_events, register_devices  # noqa: E402
from backend.routing.live_state import fetch_live_state  # noqa: E402
from backend.routing.route_service import route  # noqa: E402
from backend.state.network_state import compute_state  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

GEOMETRY = "2026-09-18.1"
PACKAGE_DIR = SOURCE_ROOT / "models" / "registry" / "traffic-forecast" / "1.0.0"
ev = Evidence("P12.02", "p12_02_safe03_stale_state", docs_name="p12_02_safe03_stale_state")
LATER = timedelta(hours=2)


def kpi(
    conn: psycopg.Connection, corridor: str, direction: str, start: datetime, delay_ratio: float
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO corridor_kpis (corridor_id, direction, window_start, window_seconds, geometry_version, kpis, quality, coverage, sample_count, segments_reporting, segments_expected) "
            "VALUES (%s, %s, %s, 300, %s, %s::jsonb, 'valid', 1.0, 10, 1, 1) ON CONFLICT (corridor_id, direction, window_start, window_seconds, geometry_version) DO UPDATE SET kpis = EXCLUDED.kpis",
            (
                corridor,
                direction,
                start,
                GEOMETRY,
                Jsonb(
                    {
                        "delay_s": 10.0 * delay_ratio,
                        "queue_fraction": 0.2,
                        "travel_time_s": 50.0 * delay_ratio,
                        "free_flow_travel_time_s": 50.0,
                    }
                ),
            ),
        )
    conn.commit()


def signal_reading(
    conn: psycopg.Connection, device_id: str, intersection: str, at: datetime
) -> None:
    register_devices(
        conn,
        [
            {
                "device_id": device_id, "device_type": "signal_controller", "deployment_type": "simulated", "agency_scope": "city-traffic-ops",
                "location": {"geometry_version": GEOMETRY, "longitude": 74.36, "latitude": 31.52, "intersection_id": intersection},
                "capabilities": ["signal_state"], "status": "active", "registered_at": at, "privacy_classification": "none", "retention_class": "standard",
            }
        ],
    )  # fmt: skip
    load_events(
        conn,
        [
            {
                "schema_version": "1.0.0", "event_id": str(uuid.uuid4()), "event_type": "signal.controller.spat", "device_id": device_id, "agency_scope": "city-traffic-ops",
                "observation_time": at.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "ingest_time": at.strftime("%Y-%m-%dT%H:%M:%S.250Z"), "sequence_number": 0, "clock_quality": "synced",
                "geometry_version": GEOMETRY,
                "location": {"coordinate_reference": "EPSG:4326", "latitude": 31.52, "longitude": 74.36, "intersection_id": intersection},
                "measurements": [
                    {"name": "active_phase", "value": 0, "unit": "index", "quality": "valid", "confidence": 1.0},
                    {"name": "signal_state", "value": "GGGGGGr", "unit": "category", "quality": "valid", "confidence": 1.0},
                ],
                "truth_label": "simulated", "privacy_classification": "none", "retention_class": "standard",
                "provenance": {"producer": "p12-safe03-lab", "pipeline_version": "lab-1"},
            }
        ],
    )  # fmt: skip


def main() -> int:  # noqa: PLR0915
    with psycopg.connect(dsn_from_env()) as conn:
        now = datetime.now(UTC).replace(microsecond=0)
        later = now + LATER
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM corridor_kpis WHERE window_start > %s", (now - timedelta(hours=6),)
            )
        conn.commit()
        signal_reading(conn, "signal-int-a2", "int-a2", now)
        kpi(conn, "corridor-a", "east", now - timedelta(minutes=6), 5.0)

        # ---- the state service
        fresh = compute_state(conn, "intersection", "int-a2", 300, 120, now + timedelta(seconds=30))
        stale = compute_state(conn, "intersection", "int-a2", 300, 120, later)
        ev.check(
            "the_state_service_serves_a_fresh_reading_as_fresh_and_the_same_reading_two_hours_on_as_stale",
            fresh is not None
            and stale is not None
            and fresh["freshness_status"] == "fresh"
            and stale["freshness_status"] == "stale",
            f"fresh -> {fresh and fresh['freshness_status']}, later -> {stale and stale['freshness_status']}",
        )

        # ---- the KPI record
        row = {
            "corridor_id": "corridor-a", "direction": "east", "window_start": (now - timedelta(minutes=6)).strftime("%Y-%m-%dT%H:%M:%SZ"), "window_seconds": 300,
            "geometry_version": GEOMETRY, "kpis": {"delay_s": 50.0, "travel_time_s": 250.0}, "quality": "valid", "coverage": 1.0, "sample_count": 10,
        }  # fmt: skip
        a = to_network_state_record(row, now)
        b = to_network_state_record(row, later)
        ev.check(
            "a_corridor_kpi_record_is_stale_once_its_window_is_older_than_its_budget_and_is_not_made_fresh_by_being_served_again",
            a["freshness_status"] == "fresh" and b["freshness_status"] == "stale",
            f"now -> {a['freshness_status']}, later -> {b['freshness_status']}",
        )

        # ---- the forecast service
        package = load_package(PACKAGE_DIR)
        origin_end = now
        old_records, old_abstain = forecast_from_kpi_rows(
            [], package, origin_end, origin_end + timedelta(seconds=MAX_INPUT_AGE_S + 60), GEOMETRY
        )
        new_records, new_abstain = forecast_from_kpi_rows(
            [], package, origin_end, origin_end + timedelta(seconds=30), GEOMETRY
        )
        ev.check(
            "the_forecast_service_refuses_stale_input_and_gives_no_forecast",
            not old_records and old_abstain and "stale input" in old_abstain[0]["reason"],
            str(old_abstain[:1]),
        )
        ev.check(
            "control_the_forecast_service_does_not_call_input_stale_when_it_is_fresh",
            not any("stale input" in a["reason"] for a in new_abstain),
            f"{len(new_abstain)} abstentions, none for staleness",
        )

        # ---- routing: a measured delay 5x free flow, fresh and stale
        segments = load_segments(conn, GEOMETRY)
        topo = Topology(segments, 3)

        def eta(at: datetime) -> float:
            routes = route(conn, "int-a2", "int-a3", GEOMETRY, at)
            return routes[0].eta_seconds

        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM corridor_kpis WHERE window_start > %s", (now - timedelta(hours=6),)
            )
        conn.commit()
        eta_no_evidence = eta(now)
        kpi(conn, "corridor-a", "east", now - timedelta(minutes=6), 5.0)
        eta_fresh = eta(now)
        eta_stale = eta(later)
        state_fresh = {
            k: v
            for k, v in fetch_live_state(conn, GEOMETRY, topo, now).items()
            if v.travel_time_s is not None
        }
        state_stale = {
            k: v
            for k, v in fetch_live_state(conn, GEOMETRY, topo, later).items()
            if v.travel_time_s is not None
        }
        ev.check(
            "routing_uses_a_fresh_measured_delay_and_ignores_the_same_window_once_it_is_old",
            eta_fresh > eta_no_evidence * 1.5
            and abs(eta_stale - eta_no_evidence) < 1e-6
            and state_fresh
            and not state_stale,
            f"ETA without evidence {eta_no_evidence:.0f} s, with a fresh window at 5x {eta_fresh:.0f} s, with the same window two hours old {eta_stale:.0f} s; segments with a live travel time: {len(state_fresh)} fresh, {len(state_stale)} stale",
        )

        # ---- signal-plan recommendations
        kpi(conn, "corridor-a", "east", now - timedelta(minutes=1), 5.0)
        alt_fresh, constraints_fresh = build_signal_recommendation(
            conn, "int-a2", "corridor-a", "east", GEOMETRY, now, SIGNAL_BOUNDS
        )
        alt_stale, constraints_stale = build_signal_recommendation(
            conn, "int-a2", "corridor-a", "east", GEOMETRY, later, SIGNAL_BOUNDS
        )
        conf_fresh = max(a.confidence for a in alt_fresh if a.signal_deviation_s)
        conf_stale = max(a.confidence for a in alt_stale if a.signal_deviation_s)
        ev.check(
            "a_signal_plan_recommendation_from_old_evidence_says_so_uses_a_stated_default_and_is_less_confident",
            "measured-corridor-delay" in constraints_fresh
            and "default-delay-assumption-used" in constraints_stale
            and "measured-corridor-delay" not in constraints_stale
            and conf_stale < conf_fresh,
            f"fresh: {constraints_fresh}, confidence {conf_fresh}; stale: {constraints_stale}, confidence {conf_stale}",
        )

        # ---- the command policy's evidence rule, for every kind of protected target
        cross = next(s for s in segments if s.corridor_id is None)
        corridor_segment = next(
            s for s in segments if s.corridor_id == "corridor-a" and s.direction == "east"
        )
        targets = {
            "a_signal (an intersection)": ("signal_controller_adapter", "int-a2"),
            "a_corridor_segment": ("diversion_adapter", corridor_segment.edge_id),
            "a_corridor": ("emergency_preemption_adapter", "corridor-a"),
            "a_cross_street_segment_with_no_corridor": ("vms_adapter", cross.edge_id),
        }
        signal_reading(
            conn, "signal-cross-a", cross.from_node, now
        )  # a reading at one end of the cross street: what evidence for it looks like
        kpi(conn, "corridor-a", "east", now - timedelta(minutes=1), 1.0)
        kpi(conn, "corridor-a", "west", now - timedelta(minutes=1), 1.0)
        verdicts = {}
        for label, (adapter, entity) in targets.items():
            verdicts[label] = (
                policy._evidence_fresh(conn, adapter, entity, GEOMETRY, topo, now),
                policy._evidence_fresh(conn, adapter, entity, GEOMETRY, topo, later),
            )  # noqa: SLF001
        for label, (fresh_now, fresh_later) in verdicts.items():
            ev.check(
                f"policy_evidence_for_{label}_is_fresh_with_a_recent_reading_and_not_fresh_two_hours_on",
                fresh_now is True and fresh_later is False,
                f"now -> {fresh_now}, two hours on -> {fresh_later}",
            )
        ev.metrics["consumers"] = {
            "routing_eta_s": {
                "no_evidence": eta_no_evidence,
                "fresh_5x": eta_fresh,
                "stale_5x": eta_stale,
            },
            "policy": {k: list(v) for k, v in verdicts.items()},
        }
    ev.notes["cited_elsewhere"] = (
        "dispatch refuses to route from an old unit position (p08_07_operator_actions_api); the console labels stale values and never leaves them looking live (p08_10_ui_acceptance, failures spec); the command executor re-checks evidence at execution (p12_02_safe02_protected_actions)"
    )
    ev.notes["not_claimed"] = (
        "a stuck or drifting sensor keeps a reading fresh and wrong: that is a data-quality fault (P10.10 detects it), not staleness; nothing gates a consumer on a data-quality incident"
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
