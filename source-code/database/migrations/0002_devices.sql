-- P05.04: devices (contracts/device/v1).

CREATE TABLE devices (
    device_id              text PRIMARY KEY
                            CHECK (device_id ~ '^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$'),
    device_type            text NOT NULL,
    deployment_type        text NOT NULL CHECK (deployment_type IN ('simulated', 'physical')),
    agency_scope           text NOT NULL,
    geometry_version       text NOT NULL REFERENCES geometry_versions (version),
    location               geography(Point, 4326) NOT NULL,
    intersection_id        text,
    corridor_id            text,
    lane_id                text,
    capabilities            text[] NOT NULL DEFAULT '{}',
    certificate_fingerprint text,
    certificate_not_before  timestamptz,
    certificate_not_after   timestamptz,
    configuration_version  text,
    status                  text NOT NULL CHECK (status IN ('active', 'inactive', 'maintenance', 'decommissioned')),
    registered_at           timestamptz NOT NULL,
    privacy_classification  text NOT NULL CHECK (privacy_classification IN ('none', 'aggregated', 'pseudonymous', 'sensitive')),
    retention_class         text NOT NULL CHECK (retention_class IN ('short', 'standard', 'extended', 'audit')),
    FOREIGN KEY (intersection_id, geometry_version)
        REFERENCES intersections (intersection_id, geometry_version)
);
CREATE INDEX devices_location_idx ON devices USING GIST (location);
CREATE INDEX devices_corridor_idx ON devices (corridor_id, geometry_version);
CREATE INDEX devices_status_idx ON devices (status);
