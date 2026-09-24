-- P06.03: persisted forecasts (contracts/forecast/v1). forecast_id is derived
-- from (element, horizon, valid_from, model, version), so recomputing the same
-- forecast from late-arriving inputs replaces the row instead of duplicating it.

CREATE TABLE forecasts (
    forecast_id           uuid PRIMARY KEY,
    network_element_type  text NOT NULL CHECK (network_element_type IN ('lane', 'segment', 'intersection', 'corridor')),
    network_element_id    text NOT NULL,
    geometry_version      text NOT NULL,
    predicted_at          timestamptz NOT NULL,
    horizon_seconds       integer NOT NULL CHECK (horizon_seconds IN (300, 900, 1800)),
    valid_from            timestamptz NOT NULL,
    valid_until           timestamptz NOT NULL,
    model_id              text NOT NULL,
    model_version         text NOT NULL,
    baseline_id           text,
    measurements          jsonb NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_until > valid_from),
    CHECK (jsonb_array_length(measurements) > 0)
);
CREATE INDEX forecasts_element_idx ON forecasts (network_element_id, valid_from DESC, horizon_seconds);
CREATE INDEX forecasts_time_idx ON forecasts (valid_from DESC, network_element_id);
