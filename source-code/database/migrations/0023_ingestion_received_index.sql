-- P08.09: the platform-status screen measures ingestion from the events received in the last few minutes (how many, how long after they
-- were observed). Without an index on received_at that is a scan of the whole telemetry table on every refresh.

CREATE INDEX observation_events_received_idx ON observation_events (received_at DESC);
