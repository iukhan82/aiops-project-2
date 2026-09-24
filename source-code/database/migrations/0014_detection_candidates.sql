-- P06.04-P06.06: one table for every detector's output (congestion,
-- spillback, stalled vehicle, collision, wrong-way, flooding, visibility,
-- pedestrian/cyclist conflict) so P06.07's correlation reads a single,
-- uniform stream of candidates. A candidate is evidence-backed and is NOT an
-- incident: promotion to an incident (with an owner and lifecycle) is P06.07.
--
-- candidate_id is derived from (kind, element, onset, source), so a detector
-- re-run over the same data replaces its own candidate instead of duplicating it.

CREATE TABLE detection_candidates (
    candidate_id          uuid PRIMARY KEY,
    kind                  text NOT NULL CHECK (kind IN (
        'congestion', 'spillback', 'stalled_vehicle', 'collision', 'wrong_way',
        'flooding', 'low_visibility', 'pedestrian_conflict', 'cyclist_conflict'
    )),
    network_element_type  text NOT NULL CHECK (network_element_type IN ('lane', 'segment', 'intersection', 'corridor', 'device')),
    network_element_id    text NOT NULL,
    geometry_version      text NOT NULL,
    onset_time            timestamptz NOT NULL,
    clear_time            timestamptz,
    detected_at           timestamptz NOT NULL,
    severity              text NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    confidence            double precision NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    evidence_event_ids    uuid[] NOT NULL,
    source                text NOT NULL,
    attributes            jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at            timestamptz NOT NULL DEFAULT now(),
    CHECK (cardinality(evidence_event_ids) > 0),
    CHECK (clear_time IS NULL OR clear_time >= onset_time),
    CHECK (detected_at >= onset_time)
);
CREATE INDEX detection_candidates_time_idx ON detection_candidates (onset_time DESC, kind);
CREATE INDEX detection_candidates_element_idx ON detection_candidates (network_element_type, network_element_id, onset_time DESC);
