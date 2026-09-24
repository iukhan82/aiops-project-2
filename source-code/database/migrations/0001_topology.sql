-- P05.04: topology (contracts/road-geometry/v1's core entities).
-- Nested MAP substructures (approaches, movements, conflict groups, stop
-- lines, crossing zones) stay in road-geometry/v1's own JSON payload,
-- surfaced by P05.06's network-state service; this migration gives the
-- queryable, constrained, spatially-indexed core: what exists, where, and
-- which geometry_version superseded which.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE geometry_versions (
    version         text PRIMARY KEY,
    effective_from  timestamptz NOT NULL,
    superseded_by   text REFERENCES geometry_versions (version),
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE intersections (
    intersection_id  text NOT NULL,
    geometry_version text NOT NULL REFERENCES geometry_versions (version),
    corridor_id      text,
    location         geography(Point, 4326) NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (intersection_id, geometry_version)
);
CREATE INDEX intersections_location_idx ON intersections USING GIST (location);
CREATE INDEX intersections_corridor_idx ON intersections (corridor_id, geometry_version);

CREATE TABLE lanes (
    lane_id          text NOT NULL,
    geometry_version text NOT NULL REFERENCES geometry_versions (version),
    intersection_id  text,
    corridor_id      text,
    centerline       geography(LineString, 4326),
    created_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (lane_id, geometry_version),
    FOREIGN KEY (intersection_id, geometry_version)
        REFERENCES intersections (intersection_id, geometry_version)
);
CREATE INDEX lanes_centerline_idx ON lanes USING GIST (centerline);
