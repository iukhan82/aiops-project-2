"""P05.04: idempotent topology seed, real (not synthetic) data - the 12
traffic-light junctions from the actual P03.01 SUMO network
(source-code/simulator/network/output/district.net.xml), not invented rows.

Unlike migrations (run-once, checksum-locked), seeds are safe to re-run at
any time: every write is ON CONFLICT DO UPDATE, so re-running against an
already-seeded database changes nothing (proven in verify_migrate.py).

Anchor point and flat-earth projection match
source-code/simulator/sensors/build_sensor_catalog.py's ORIGIN_LAT/ORIGIN_LON
exactly, so seeded intersections land at the same coordinates P03.03's
device catalog uses - not a geodetic claim about any real place, an
equirectangular approximation for a ~900m x 800m synthetic district.
"""

from __future__ import annotations

import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from database.migrate import dsn_from_env  # noqa: E402

NET_XML = SOURCE_ROOT / "simulator" / "network" / "output" / "district.net.xml"

GEOMETRY_VERSION = "2026-09-18.1"
EFFECTIVE_FROM = "2026-09-18T00:00:00Z"
ORIGIN_LAT = 31.5204
ORIGIN_LON = 74.3587
_EARTH_RADIUS_M = 6378137.0


def project(x_m: float, y_m: float) -> tuple[float, float]:
    lat = ORIGIN_LAT + (y_m / _EARTH_RADIUS_M) * (180.0 / math.pi)
    lon = ORIGIN_LON + (x_m / (_EARTH_RADIUS_M * math.cos(math.radians(ORIGIN_LAT)))) * (
        180.0 / math.pi
    )
    return round(lat, 6), round(lon, 6)


def corridor_of(intersection_id: str) -> str | None:
    # "int-a3" -> "corridor-a"
    if not intersection_id.startswith("int-") or len(intersection_id) < 6:
        return None
    return f"corridor-{intersection_id[4]}"


def read_intersections(net_xml: Path) -> list[dict]:
    root = ET.parse(net_xml).getroot()
    out = []
    for junction in root.findall("junction"):
        jid = junction.get("id", "")
        if junction.get("type") != "traffic_light" or jid.startswith(":"):
            continue
        x, y = float(junction.get("x")), float(junction.get("y"))
        lat, lon = project(x, y)
        out.append(
            {
                "intersection_id": jid,
                "corridor_id": corridor_of(jid),
                "lat": lat,
                "lon": lon,
            }
        )
    return sorted(out, key=lambda r: r["intersection_id"])


def seed(conn: psycopg.Connection, net_xml: Path = NET_XML) -> dict:
    if not net_xml.exists():
        return {
            "skipped": True,
            "reason": f"{net_xml} not found; run simulator/network/run_container.sh first",
        }

    intersections = read_intersections(net_xml)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO geometry_versions (version, effective_from)
            VALUES (%s, %s)
            ON CONFLICT (version) DO NOTHING
            """,
            (GEOMETRY_VERSION, EFFECTIVE_FROM),
        )
        for row in intersections:
            cur.execute(
                """
                INSERT INTO intersections (intersection_id, geometry_version, corridor_id, location)
                VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)
                ON CONFLICT (intersection_id, geometry_version)
                DO UPDATE SET corridor_id = EXCLUDED.corridor_id, location = EXCLUDED.location
                """,
                (
                    row["intersection_id"],
                    GEOMETRY_VERSION,
                    row["corridor_id"],
                    row["lon"],
                    row["lat"],
                ),
            )
    conn.commit()
    return {
        "skipped": False,
        "geometry_version": GEOMETRY_VERSION,
        "intersections_seeded": len(intersections),
    }


def main() -> int:
    with psycopg.connect(dsn_from_env()) as conn:
        report = seed(conn)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
