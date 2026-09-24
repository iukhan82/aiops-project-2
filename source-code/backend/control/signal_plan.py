"""P07.04: signal-plan-change recommendations for a congested approach.

There is no per-movement signal plan model in this platform yet (P03.03's
signal devices report SPaT telemetry, not a base plan an optimizer could
rewrite), so the honest scope here is a *bounded green-time reallocation*:
borrow up to `max_signal_deviation_s` from the corridor's cross-street phase
and give it to the congested approach, holding the pedestrian clearance
interval fixed. Benefit (queue/delay reduction on the congested approach) and
harm (added wait on the phase the time was borrowed from) are both derived
from the corridor's own real, recently measured `delay_s` and `queue_fraction`
(P06.01) - the reallocation is assumed to move delay roughly one-for-one
between the two phases (a standard, stated traffic-engineering approximation,
not a claim of a calibrated signal-optimization model).
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.control.engine import Alternative, Metric, SafetyBounds, enforce  # noqa: E402

DEFAULT_BOUNDS = SafetyBounds(min_pedestrian_clearance_s=7.0, max_signal_deviation_s=20.0)
CANDIDATE_DEVIATIONS_S = (
    10.0,
    20.0,
    30.0,
)  # 30 s is deliberately over-bound, to prove enforcement drops it


def _recent_kpi(
    conn: psycopg.Connection, corridor_id: str, direction: str, geometry: str, now: datetime
) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT kpis FROM corridor_kpis WHERE corridor_id = %s AND direction = %s AND geometry_version = %s "
            "AND window_start > %s ORDER BY window_start DESC LIMIT 1",
            (corridor_id, direction, geometry, now - timedelta(seconds=900)),
        )
        row = cur.fetchone()
    return row[0] if row else None


def build_signal_recommendation(
    conn: psycopg.Connection,
    intersection_id: str,
    corridor_id: str,
    direction: str,
    geometry: str,
    now: datetime,
    bounds: SafetyBounds = DEFAULT_BOUNDS,
) -> tuple[list[Alternative], list[str]]:
    kpi = _recent_kpi(conn, corridor_id, direction, geometry, now)
    delay_s = kpi["delay_s"] if kpi else None
    queue_fraction = kpi["queue_fraction"] if kpi else None
    measured = delay_s is not None
    base_delay = (
        delay_s if measured else 15.0
    )  # a stated planning default when nothing has been measured recently

    alternatives = []
    for deviation in CANDIDATE_DEVIATIONS_S:
        # each extra second of green roughly recovers a fraction of the measured delay, saturating -
        # a simple, stated approximation (not a calibrated queueing model), with diminishing returns
        # past 20s so the engine does not report an implausibly large benefit for a large deviation.
        recovered_fraction = min(0.9, deviation / (base_delay + deviation))
        benefit_s = base_delay * recovered_fraction
        alternatives.append(
            Alternative(
                f"alt-{uuid.uuid4()}",
                f"Extend {intersection_id}'s {corridor_id} {direction} green by {deviation:.0f}s, borrowed from the cross-street phase",
                (
                    Metric("delay_reduction", benefit_s, "s"),
                    Metric("queue_fraction_before", queue_fraction or 0.0, "ratio"),
                ),
                (Metric("cross_street_added_wait", deviation, "s"),),
                0.65 if measured else 0.3,
                signal_deviation_s=deviation,
                pedestrian_clearance_s=bounds.min_pedestrian_clearance_s,
            )
        )
    alternatives.append(
        Alternative(
            f"alt-{uuid.uuid4()}", "Take no action", (), (Metric("delay_reduction", 0.0, "s"),), 1.0
        )
    )
    constraints = [
        "pedestrian-clearance-preserved",
        "measured-corridor-delay" if measured else "default-delay-assumption-used",
    ]
    return enforce(alternatives, bounds), constraints
