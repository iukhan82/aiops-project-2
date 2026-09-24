-- P05.10: retention rollups and a record of every retention pass run.
--
-- retention_rollups holds a daily per-device aggregate computed from raw
-- observation_events *before* those rows are purged - historical trend
-- survives the raw data's retention window even though the raw rows do not.

CREATE TABLE retention_rollups (
    id            bigserial PRIMARY KEY,
    device_id     text NOT NULL,
    event_type    text NOT NULL,
    bucket_date   date NOT NULL,
    sample_count  integer NOT NULL,
    mean_value    double precision,
    retention_class text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (device_id, event_type, bucket_date)
);
CREATE INDEX retention_rollups_device_idx ON retention_rollups (device_id, bucket_date DESC);

CREATE TABLE retention_runs (
    id                  bigserial PRIMARY KEY,
    run_at              timestamptz NOT NULL DEFAULT now(),
    storage_pressure    boolean NOT NULL,
    database_size_bytes bigint NOT NULL,
    events_rolled_up    integer NOT NULL,
    events_deleted      integer NOT NULL,
    commands_deleted    integer NOT NULL,
    incidents_deleted   integer NOT NULL,
    detail              jsonb
);
