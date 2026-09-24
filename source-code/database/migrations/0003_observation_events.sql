-- P05.04: telemetry (contracts/observation-envelope/v1).
--
-- event_id PRIMARY KEY is the exactly-once-by-meaning mechanism P05.05's
-- ingestion (and P05.03's gateway-to-Kafka replay) relies on: a duplicate
-- delivery, however it happens, always resolves to the same primary key and
-- an idempotent INSERT ... ON CONFLICT (event_id) DO NOTHING.
--
-- device_id intentionally has NO foreign key to devices: the schema allows
-- observation events to arrive (and be validated/typed) independently of
-- whether the emitting device's own registration record has landed yet -
-- device-referential-integrity is checked at a different layer (P03.09,
-- edge.validation's DeviceRegistry), not enforced as a hard DB constraint
-- that would reject otherwise-valid telemetry.

CREATE TABLE observation_events (
    event_id               uuid PRIMARY KEY,
    event_type             text NOT NULL CHECK (event_type ~ '^[a-z0-9_]+(\.[a-z0-9_]+)+$'),
    device_id              text NOT NULL,
    agency_scope           text NOT NULL,
    observation_time       timestamptz NOT NULL,
    ingest_time             timestamptz NOT NULL,
    sequence_number         bigint NOT NULL CHECK (sequence_number >= 0),
    clock_quality           text NOT NULL CHECK (clock_quality IN ('synced', 'drifting', 'unknown')),
    geometry_version        text NOT NULL,
    location                 geography(Point, 4326) NOT NULL,
    intersection_id          text,
    corridor_id               text,
    lane_id                   text,
    measurements              jsonb NOT NULL,
    truth_label               text NOT NULL CHECK (
        truth_label IN ('simulated', 'measured', 'inferred', 'predicted', 'operator_entered', 'verified')
    ),
    privacy_classification    text NOT NULL CHECK (privacy_classification IN ('none', 'aggregated', 'pseudonymous', 'sensitive')),
    retention_class           text NOT NULL CHECK (retention_class IN ('short', 'standard', 'extended', 'audit')),
    correlation_id             text,
    provenance                  jsonb,
    received_at                 timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX observation_events_device_time_idx ON observation_events (device_id, observation_time DESC);
CREATE INDEX observation_events_corridor_time_idx ON observation_events (corridor_id, observation_time DESC);
CREATE INDEX observation_events_type_time_idx ON observation_events (event_type, observation_time DESC);
CREATE INDEX observation_events_location_idx ON observation_events USING GIST (location);
