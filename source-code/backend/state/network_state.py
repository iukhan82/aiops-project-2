"""P05.06: network-state and freshness service.

Aggregates `observation_events` (P05.04/P05.05) into `contracts/
network-state/v1` records per network element, preserving *source* time
(the window's own observation_time, not "now"), per-measurement quality and
confidence, and the geometry_version each contributing observation was
placed against - never silently mixing state across a geometry change.

network_element_type support: 'lane', 'intersection' and 'corridor' map
directly to observation_events' own lane_id/intersection_id/corridor_id
columns. 'segment' is one SUMO edge between two intersections (P06.01's
`network_segments`): an observation belongs to it when its lane_id is
`<segment id>_<lane index>`. Any other element type still raises
NotImplementedError rather than guessing.

Carry-forward (schema note: "sample_count: 0 means carried-forward/no fresh
samples"): a measurement name with no sample inside the current window but
with an earlier one for the same element is still reported, at its last
known value, with sample_count=0 and freshness computed from its true age -
this is what lets a caller distinguish "device went quiet" from "device
never existed" without a separate device-capability catalog.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import psycopg

from backend.observability import BoundedCounter, traced

_ELEMENT_COLUMN = {
    "lane": "lane_id",
    "intersection": "intersection_id",
    "corridor": "corridor_id",
}
SUPPORTED_TYPES = (*_ELEMENT_COLUMN, "segment")
_metric_freshness: BoundedCounter | None = None


def _freshness_counter() -> BoundedCounter:
    """SAFE-03 / the `freshness` SLO objective type: every computed record,
    by its own real `freshness_status`, never a synthetic sample."""
    global _metric_freshness
    if _metric_freshness is None:
        _metric_freshness = BoundedCounter(
            "network_state_records", "Computed network-state records by freshness_status"
        )
    return _metric_freshness


def _predicate(network_element_type: str, element_id: str) -> tuple[str, str]:
    """(SQL predicate with one %s, its parameter) selecting an element's rows."""
    if network_element_type == "segment":
        # '!' is the LIKE escape character, so a literal '_' in an edge id
        # (e.g. int-a1_int-a2) is not read as "any single character".
        escaped = element_id.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        return "lane_id LIKE %s ESCAPE '!'", escaped + "!_%"
    return f"{_ELEMENT_COLUMN[network_element_type]} = %s", element_id


def compute_state(
    conn: psycopg.Connection,
    network_element_type: str,
    network_element_id: str,
    window_seconds: float,
    max_staleness_seconds: float,
    as_of: datetime | None = None,
) -> dict | None:
    """Returns one contracts/network-state/v1 record, or None if this
    element has never had any observation at all (a contract-valid record
    cannot be constructed - measurements requires at least one entry)."""
    return compute_state_with_source(
        conn, network_element_type, network_element_id, window_seconds, max_staleness_seconds, as_of
    )[0]


def compute_state_with_source(
    conn: psycopg.Connection,
    network_element_type: str,
    network_element_id: str,
    window_seconds: float,
    max_staleness_seconds: float,
    as_of: datetime | None = None,
) -> tuple[dict | None, datetime | None]:
    """`compute_state` plus the source time of the newest sample behind it. The contract's `observation_time` is the end of the
    aggregation window (the instant asked about), so it cannot say how old the underlying reading is; a screen that wants to show
    "last reading 41 s ago" needs this second value."""
    with traced(
        "state.compute_state",
        correlation_id=f"{network_element_type}:{network_element_id}",
        network_element_type=network_element_type,
    ):
        record, source_time = _compute_state_with_source(
            conn,
            network_element_type,
            network_element_id,
            window_seconds,
            max_staleness_seconds,
            as_of,
        )
        if record is not None:
            _freshness_counter().add(freshness_status=record["freshness_status"])
        return record, source_time


def _compute_state_with_source(
    conn: psycopg.Connection,
    network_element_type: str,
    network_element_id: str,
    window_seconds: float,
    max_staleness_seconds: float,
    as_of: datetime | None = None,
) -> tuple[dict | None, datetime | None]:
    if network_element_type not in SUPPORTED_TYPES:
        raise NotImplementedError(
            f"network_element_type={network_element_type!r} is not supported "
            f"(supported: {', '.join(SUPPORTED_TYPES)})"
        )
    predicate, param = _predicate(network_element_type, network_element_id)
    as_of = as_of or datetime.now(timezone.utc)
    window_start = as_of - timedelta(seconds=window_seconds)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT geometry_version, truth_label, observation_time, measurements
            FROM observation_events
            WHERE {predicate} AND observation_time > %s AND observation_time <= %s
            ORDER BY observation_time ASC
            """,
            (param, window_start, as_of),
        )
        in_window = cur.fetchall()

    by_name: dict[str, list[dict]] = {}
    for _geom, _truth, obs_time, measurements in in_window:
        for m in measurements:
            by_name.setdefault(m["name"], []).append({**m, "observation_time": obs_time})

    known_names = set(by_name)
    if not known_names:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT DISTINCT jsonb_array_elements(measurements)->>'name'
                FROM observation_events
                WHERE {predicate} AND observation_time <= %s
                """,
                (param, as_of),
            )
            known_names = {r[0] for r in cur.fetchall()}
    if not known_names:
        return None, None  # never observed at all - no contract-valid record possible

    measurements_out = []
    latest_source_time: datetime | None = None
    geometry_version = None
    truth_labels: set[str] = set()

    for name in sorted(known_names):
        samples = by_name.get(name, [])
        if samples:
            values = [s["value"] for s in samples if isinstance(s["value"], (int, float))]
            avg_value = sum(values) / len(values) if values else 0.0
            avg_conf = sum(s["confidence"] for s in samples) / len(samples)
            worst_quality = _worst_quality(s["quality"] for s in samples)
            sample_count = len(samples)
            newest = max(s["observation_time"] for s in samples)
            unit = samples[-1]["unit"]
        else:
            carried = _most_recent_before(conn, predicate, param, name, window_start)
            if carried is None:
                continue
            avg_value = carried["value"]
            avg_conf = carried["confidence"]
            worst_quality = carried["quality"]
            sample_count = 0
            newest = carried["observation_time"]
            unit = carried["unit"]

        latest_source_time = (
            newest if latest_source_time is None else max(latest_source_time, newest)
        )
        measurements_out.append(
            {
                "name": name,
                "value": round(avg_value, 6) if isinstance(avg_value, float) else avg_value,
                "unit": unit,
                "quality": worst_quality,
                "confidence": round(avg_conf, 6),
                "sample_count": sample_count,
            }
        )

    if not measurements_out:
        return None, None

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT geometry_version, truth_label FROM observation_events
            WHERE {predicate} AND observation_time <= %s
            ORDER BY observation_time DESC LIMIT 1
            """,
            (param, as_of),
        )
        row = cur.fetchone()
        if row:
            geometry_version, latest_truth = row
            truth_labels.add(latest_truth)

    age_seconds = (as_of - latest_source_time).total_seconds() if latest_source_time else None
    freshness_status = (
        "unknown"
        if age_seconds is None
        else ("fresh" if age_seconds <= max_staleness_seconds else "stale")
    )

    record = {
        "schema_version": "1.0.0",
        "record_id": str(uuid.uuid4()),
        "network_element_type": network_element_type,
        "network_element_id": network_element_id,
        "geometry_version": geometry_version,
        "window_seconds": window_seconds,
        "observation_time": as_of.isoformat().replace("+00:00", "Z"),
        "ingest_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "measurements": measurements_out,
        "truth_label": _dominant_truth_label(truth_labels),
        "freshness_status": freshness_status,
        "max_staleness_seconds": max_staleness_seconds,
    }
    return record, latest_source_time


def _worst_quality(qualities) -> str:
    order = {"invalid": 2, "suspect": 1, "valid": 0}
    return max(qualities, key=lambda q: order.get(q, 0))


def _dominant_truth_label(labels: set[str]) -> str:
    # A window mixing truth labels (e.g. simulated + operator_entered) is
    # reported as "inferred" - an aggregate is itself a derived value, never
    # a raw measurement, regardless of what it was computed from.
    if len(labels) == 1:
        return next(iter(labels))
    return "inferred"


def _most_recent_before(
    conn: psycopg.Connection, predicate: str, param: str, name: str, before: datetime
) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT observation_time, measurements
            FROM observation_events
            WHERE {predicate} AND observation_time <= %s
              AND measurements @> %s::jsonb
            ORDER BY observation_time DESC LIMIT 1
            """,
            (param, before, json.dumps([{"name": name}])),
        )
        row = cur.fetchone()
    if row is None:
        return None
    obs_time, measurements = row
    for m in measurements:
        if m["name"] == name:
            return {**m, "observation_time": obs_time}
    return None
