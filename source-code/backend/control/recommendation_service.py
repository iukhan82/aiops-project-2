"""P07.04: ties a live incident to a generated, stored recommendation -
diversion for a closed/hazardous segment, signal-plan-change for a congested
corridor - superseding whatever was previously proposed for the same
incident, so an incident only ever has one live recommendation.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.control.diversion import DEFAULT_BOUNDS as DIVERSION_BOUNDS  # noqa: E402
from backend.control.diversion import build_diversion_recommendation  # noqa: E402
from backend.control.engine import UnsafeAlternative  # noqa: E402
from backend.control.signal_plan import DEFAULT_BOUNDS as SIGNAL_BOUNDS  # noqa: E402
from backend.control.signal_plan import build_signal_recommendation  # noqa: E402
from backend.observability import traced  # noqa: E402
from backend.repositories.recommendations import (  # noqa: E402
    active_for_trigger,
    create_recommendation,
    supersede,
)  # noqa: E402

RECOMMENDATION_TTL_S = 600.0


def recommend_for_incident(
    conn: psycopg.Connection,
    incident_id: str,
    incident_type: str,
    element_type: str,
    element_id: str,
    geometry: str,
    now: datetime,
    corridor_context: tuple[str, str] | None = None,
    diversion_endpoints: tuple[str, str] | None = None,
) -> str | None:
    """Returns the new recommendation id, or None when no generator applies to this incident kind
    or every candidate alternative would be unsafe (UnsafeAlternative is not raised to the caller -
    a policy 'no safe action exists' result is not a bug)."""
    with traced(
        "control.recommend_for_incident", correlation_id=incident_id, incident_type=incident_type
    ):
        return _recommend_for_incident(
            conn, incident_id, incident_type, element_type, element_id, geometry, now,
            corridor_context, diversion_endpoints,
        )  # fmt: skip


def _recommend_for_incident(
    conn: psycopg.Connection,
    incident_id: str,
    incident_type: str,
    element_type: str,
    element_id: str,
    geometry: str,
    now: datetime,
    corridor_context: tuple[str, str] | None = None,
    diversion_endpoints: tuple[str, str] | None = None,
) -> str | None:
    try:
        if (
            incident_type in ("stalled_vehicle", "collision", "wrong_way", "flooding")
            and diversion_endpoints
        ):
            origin, destination = diversion_endpoints
            alternatives, constraints = build_diversion_recommendation(
                conn, element_id, origin, destination, geometry, now, DIVERSION_BOUNDS
            )
            action_type, bounds = "diversion", DIVERSION_BOUNDS
        elif incident_type in ("congestion", "spillback") and corridor_context:
            intersection_id, (corridor_id, direction) = element_id, corridor_context
            alternatives, constraints = build_signal_recommendation(
                conn, intersection_id, corridor_id, direction, geometry, now, SIGNAL_BOUNDS
            )
            action_type, bounds = "signal_plan_change", SIGNAL_BOUNDS
        else:
            return None
    except UnsafeAlternative:
        return None
    if not alternatives:
        return None
    new_id = create_recommendation(
        conn,
        action_type,
        [a.as_record() for a in alternatives],
        bounds.as_record(),
        constraints,
        now,
        now + timedelta(seconds=RECOMMENDATION_TTL_S),
        trigger_incident_id=incident_id,
    )
    for prior in active_for_trigger(conn, incident_id):
        prior_id = str(prior["recommendation_id"])
        if prior_id != new_id:
            supersede(conn, prior_id, new_id)
    return new_id
