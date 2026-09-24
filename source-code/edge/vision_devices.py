"""P04.06: three illustrative edge_camera devices, one per corridor, at
intersections distinct from P03.03's weather stations (`int-*2`) so device
ids never collide with that catalog. Not part of P03.03's sensor catalog
(that stage is DONE/frozen); generated fresh here the same way P03.06 added
its one new `aiops_agent` device without touching it.

Placement and the illustrative privacy zone are arbitrary choices for
demonstrating the mechanism (docs/evidence/SIMULATION_LIMITATIONS.md-style
caveat), not a claim about any real camera siting.
"""

from __future__ import annotations

import sys
from pathlib import Path

SIMULATOR_DIR = Path(__file__).resolve().parents[1] / "simulator"
sys.path.insert(0, str(SIMULATOR_DIR / "sensors"))
from build_sensor_catalog import load_junction_coordinates, project  # noqa: E402

from edge.vision_privacy import PrivacyZone  # noqa: E402

GEOMETRY_VERSION = "2026-09-18.1"
AGENCY_SCOPE = "city-traffic-ops"
CAMERA_JUNCTIONS = ("int-a3", "int-b3", "int-c3")


def edge_camera_devices(
    nod_file: Path,
    camera_junctions: tuple[str, ...] = CAMERA_JUNCTIONS,
    extra_capabilities: tuple[str, ...] = (),
) -> list[dict]:
    junctions = load_junction_coordinates(nod_file)
    devices = []
    for junction_id in camera_junctions:
        x, y = junctions[junction_id]
        lat, lon = project(x, y)
        devices.append(
            {
                "schema_version": "1.0.0",
                "device_id": f"camera-{junction_id}",
                "device_type": "edge_camera",
                "deployment_type": "simulated",
                "agency_scope": AGENCY_SCOPE,
                "location": {
                    "geometry_version": GEOMETRY_VERSION,
                    "coordinate_reference": "EPSG:4326",
                    "latitude": lat,
                    "longitude": lon,
                    "intersection_id": junction_id,
                },
                "capabilities": [
                    "count_vehicle",
                    "count_cyclist",
                    "count_pedestrian",
                    "mean_speed",
                    *extra_capabilities,
                ],
                "status": "active",
                "registered_at": "2026-09-18T00:00:00Z",
                "privacy_classification": "aggregated",
                "retention_class": "short",
            }
        )
    return devices


def default_privacy_zones(
    nod_file: Path, radius_m: float = 6.0, offset_m: float = 12.0
) -> list[PrivacyZone]:
    """One small zone per camera junction, offset from the junction center
    (e.g. an accessible-crossing waiting area a few meters off the
    intersection) that the camera must never track, even in aggregate. A
    small offset pocket, not the whole intersection, so the camera's normal
    counts are unaffected outside that pocket - the point is to prove
    suppression is local and real, not that the camera reports nothing."""
    junctions = load_junction_coordinates(nod_file)
    return [
        PrivacyZone(f"zone-{junction_id}", x, y + offset_m, radius_m)
        for junction_id, (x, y) in ((j, junctions[j]) for j in CAMERA_JUNCTIONS)
    ]
