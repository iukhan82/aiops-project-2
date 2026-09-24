"""P06.01: idempotent seed of `network_segments` from the real P03.01 net
file, with the per-segment lane share calibrated on the TRAIN split
(backend/analytics/calibration.py). Re-running changes nothing."""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.calibration import load_lane_shares  # noqa: E402
from backend.analytics.topology import parse_net_segments  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402
from database.seeds.seed_topology import GEOMETRY_VERSION, NET_XML  # noqa: E402


def seed(conn: psycopg.Connection, net_xml: Path = NET_XML) -> dict:
    if not net_xml.exists():
        return {"skipped": True, "reason": f"{net_xml} not found"}
    shares = load_lane_shares()
    segments = parse_net_segments(net_xml)
    with conn.cursor() as cur:
        for s in segments:
            cur.execute(
                """
                INSERT INTO network_segments (edge_id, geometry_version, from_node, to_node, corridor_id,
                    direction, order_index, length_m, free_flow_speed_m_s, lane_share)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (edge_id, geometry_version) DO UPDATE SET
                    corridor_id = EXCLUDED.corridor_id, direction = EXCLUDED.direction,
                    order_index = EXCLUDED.order_index, length_m = EXCLUDED.length_m,
                    free_flow_speed_m_s = EXCLUDED.free_flow_speed_m_s, lane_share = EXCLUDED.lane_share
                """,
                (
                    s.edge_id,
                    GEOMETRY_VERSION,
                    s.from_node,
                    s.to_node,
                    s.corridor_id,
                    s.direction,
                    s.order,
                    s.length_m,
                    s.free_flow_speed_m_s,
                    shares.get(s.edge_id, s.lane_share),
                ),
            )
    conn.commit()
    return {"skipped": False, "segments_seeded": len(segments), "calibrated_shares": len(shares)}


def main() -> int:
    with psycopg.connect(dsn_from_env()) as conn:
        print(seed(conn))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
