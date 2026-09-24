-- P05.05: central ingestion support.
--
-- content_sha256 is what makes a duplicate event_id distinguishable from a
-- genuine conflict: on an event_id collision, ingestion compares the new
-- payload's hash against the stored one - identical hash means a replay
-- (accepted as a no-op, exactly-once-by-meaning), different hash means a
-- real content conflict (rejected, the original row is never overwritten).

ALTER TABLE observation_events ADD COLUMN content_sha256 text NOT NULL DEFAULT '';

CREATE TABLE ingestion_rejections (
    id           bigserial PRIMARY KEY,
    event_id     text,
    reason       text NOT NULL CHECK (reason IN ('schema_invalid', 'unknown_device', 'content_conflict')),
    detail       text,
    rejected_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ingestion_rejections_reason_idx ON ingestion_rejections (reason, rejected_at DESC);
