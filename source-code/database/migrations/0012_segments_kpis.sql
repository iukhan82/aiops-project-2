-- P06.01: segment topology (what P05.06 lacked for `segment` state) and the
-- persisted corridor KPIs computed by backend/analytics/kpi_service.py.
--
-- lane_share is a fitted per-segment parameter (calibration.py, TRAIN split
-- only); its provenance lives in backend/analytics/artifacts/lane_share.json.

CREATE TABLE network_segments (
    edge_id              text NOT NULL,
    geometry_version     text NOT NULL REFERENCES geometry_versions (version),
    from_node            text NOT NULL,
    to_node              text NOT NULL,
    corridor_id          text,
    direction            text NOT NULL CHECK (direction IN ('east', 'west', 'cross')),
    order_index          integer CHECK (order_index IS NULL OR order_index >= 1),
    length_m             double precision NOT NULL CHECK (length_m > 0),
    free_flow_speed_m_s  double precision NOT NULL CHECK (free_flow_speed_m_s > 0),
    lane_share           double precision NOT NULL DEFAULT 0.5 CHECK (lane_share > 0 AND lane_share <= 1),
    PRIMARY KEY (edge_id, geometry_version)
);
CREATE INDEX network_segments_corridor_idx ON network_segments (corridor_id, direction, order_index);

CREATE TABLE corridor_kpis (
    corridor_id         text NOT NULL,
    direction           text NOT NULL CHECK (direction IN ('east', 'west')),
    window_start        timestamptz NOT NULL,
    window_seconds      integer NOT NULL CHECK (window_seconds > 0),
    geometry_version    text NOT NULL,
    kpis                jsonb NOT NULL,
    quality             text NOT NULL CHECK (quality IN ('valid', 'suspect', 'invalid')),
    coverage            double precision NOT NULL CHECK (coverage >= 0 AND coverage <= 1),
    sample_count        integer NOT NULL CHECK (sample_count >= 0),
    segments_reporting  integer NOT NULL,
    segments_expected   integer NOT NULL,
    computed_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (corridor_id, direction, window_start, window_seconds, geometry_version)
);
CREATE INDEX corridor_kpis_time_idx ON corridor_kpis (window_start DESC, corridor_id, direction);
