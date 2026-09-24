"""P08.05: what the live operations map needs beyond the existing device, KPI and incident APIs.

* `GET /api/v1/network/topology` - the static schematic: intersections with coordinates and the directed segments between
  them, for one geometry version. Read straight from the tables the platform's own detectors and router use.
* `GET /api/v1/network-state` - the current `network-state/v1` record of every element of one type, so a map can draw
  the whole network from one call instead of one call per segment. It runs the same `compute_state` as the single-element
  endpoint; a short cache keeps ten open maps from becoming ten times the database load, and never serves a record older
  than `CACHE_SECONDS` (freshness is computed from source time, so it stays honest).
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, HTTPException

from backend.api.db import get_conn
from backend.state.network_state import SUPPORTED_TYPES, compute_state_with_source

CACHE_SECONDS = 2.0
_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def _current_geometry(conn: psycopg.Connection, requested: str | None) -> str:
    with conn.cursor() as cur:
        if requested:
            cur.execute("SELECT version FROM geometry_versions WHERE version = %s", (requested,))
        else:
            cur.execute(
                "SELECT version FROM geometry_versions WHERE superseded_by IS NULL ORDER BY effective_from DESC LIMIT 1"
            )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(
            404,
            f"unknown geometry version {requested!r}"
            if requested
            else "no geometry version is loaded",
        )
    return row[0]


def _element_ids(conn: psycopg.Connection, element_type: str, geometry: str) -> list[str]:
    with conn.cursor() as cur:
        if element_type == "segment":
            cur.execute(
                "SELECT edge_id FROM network_segments WHERE geometry_version = %s ORDER BY edge_id",
                (geometry,),
            )
        elif element_type == "intersection":
            cur.execute(
                "SELECT intersection_id FROM intersections WHERE geometry_version = %s ORDER BY intersection_id",
                (geometry,),
            )
        elif element_type == "corridor":
            cur.execute(
                "SELECT DISTINCT corridor_id FROM network_segments WHERE geometry_version = %s AND corridor_id IS NOT NULL ORDER BY corridor_id",
                (geometry,),
            )
        else:
            return []
        return [r[0] for r in cur.fetchall()]


def register(app: FastAPI) -> None:
    @app.get("/api/v1/network/topology")
    def network_topology(
        conn: Annotated[psycopg.Connection, Depends(get_conn)], geometry_version: str | None = None
    ) -> dict:
        geometry = _current_geometry(conn, geometry_version)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT intersection_id, corridor_id, ST_Y(location::geometry), ST_X(location::geometry) FROM intersections WHERE geometry_version = %s ORDER BY intersection_id",
                (geometry,),
            )
            intersections = [
                {"intersection_id": i, "corridor_id": c, "latitude": lat, "longitude": lon}
                for i, c, lat, lon in cur.fetchall()
            ]
            cur.execute(
                "SELECT edge_id, from_node, to_node, corridor_id, direction, order_index, length_m, free_flow_speed_m_s FROM network_segments "
                "WHERE geometry_version = %s ORDER BY corridor_id NULLS LAST, direction, order_index NULLS LAST, edge_id",
                (geometry,),
            )
            segments = [
                {
                    "edge_id": e,
                    "from_node": f,
                    "to_node": t,
                    "corridor_id": c,
                    "direction": d,
                    "order_index": o,
                    "length_m": length,
                    "free_flow_speed_m_s": ff,
                }
                for e, f, t, c, d, o, length, ff in cur.fetchall()
            ]
        return {
            "geometry_version": geometry,
            "coordinate_reference": "EPSG:4326",
            "intersections": intersections,
            "segments": segments,
        }

    @app.get("/api/v1/network-state")
    def list_network_state(
        conn: Annotated[psycopg.Connection, Depends(get_conn)],
        element_type: str = "segment",
        window_seconds: float = 300,
        max_staleness_seconds: float = 120,
        as_of: datetime | None = None,
    ) -> dict:
        """One `network-state/v1` record per element of `element_type` that has ever been observed; elements never observed are listed in `unobserved`.
        `newest_sample_time` maps each element to the source time of the newest reading behind its record (the record's own
        `observation_time` is the end of the window, not the age of the data).
        `as_of` (a past instant, for replay) computes the state as it would have been then; freshness is judged against that instant, not against now."""
        if element_type not in SUPPORTED_TYPES or element_type == "lane":
            raise HTTPException(
                400,
                f"element_type must be one of segment, intersection, corridor (got {element_type!r})",
            )
        if as_of is not None:
            if as_of.tzinfo is None:
                as_of = as_of.replace(tzinfo=timezone.utc)
            if as_of > datetime.now(timezone.utc):
                raise HTTPException(400, "as_of must not be in the future")
        key = (element_type, window_seconds, max_staleness_seconds, as_of)
        with _cache_lock:
            hit = _cache.get(key)
            if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
                return hit[1]
        geometry = _current_geometry(conn, None)
        items, unobserved, newest = [], [], {}
        for element_id in _element_ids(conn, element_type, geometry):
            state, source_time = compute_state_with_source(
                conn, element_type, element_id, window_seconds, max_staleness_seconds, as_of
            )
            if state is None:
                unobserved.append(element_id)
                continue
            items.append(state)
            newest[element_id] = (
                source_time.isoformat().replace("+00:00", "Z") if source_time else None
            )
        payload = {
            "element_type": element_type,
            "geometry_version": geometry,
            "window_seconds": window_seconds,
            "max_staleness_seconds": max_staleness_seconds,
            "as_of": as_of.isoformat().replace("+00:00", "Z") if as_of else None,
            "items": items,
            "newest_sample_time": newest,
            "unobserved": unobserved,
        }
        with _cache_lock:
            for stale in [
                k for k, (stamp, _) in _cache.items() if time.monotonic() - stamp >= CACHE_SECONDS
            ]:
                del _cache[stale]
            _cache[key] = (time.monotonic(), payload)
        return payload
