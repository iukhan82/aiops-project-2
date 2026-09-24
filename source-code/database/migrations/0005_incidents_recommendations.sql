-- P05.04: control records, part 2 (contracts/incident/v1, contracts/recommendation/v1).

CREATE TABLE incidents (
    incident_id           uuid PRIMARY KEY,
    incident_type          text NOT NULL CHECK (incident_type IN (
        'congestion', 'spillback', 'collision', 'stalled_vehicle', 'wrong_way',
        'pedestrian_conflict', 'hazard', 'flooding', 'low_visibility',
        'signal_fault', 'device_fault', 'data_quality', 'other'
    )),
    severity                 text NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    status                    text NOT NULL CHECK (status IN (
        'open', 'acknowledged', 'investigating', 'escalated', 'resolved', 'reopened'
    )),
    network_element_type       text NOT NULL CHECK (network_element_type IN (
        'lane', 'segment', 'intersection', 'corridor', 'device'
    )),
    network_element_id           text NOT NULL,
    geometry_version               text NOT NULL,
    opened_at                       timestamptz NOT NULL,
    updated_at                       timestamptz NOT NULL,
    resolved_at                       timestamptz,
    evidence_event_ids                  uuid[] NOT NULL,
    root_cause_hypothesis                text,
    verified_cause                        text,
    owner_role                             text NOT NULL CHECK (owner_role IN ('BACKEND', 'OPS', 'EMERG', 'CONTROL', 'SEC')),
    confidence                              double precision NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    CHECK (cardinality(evidence_event_ids) > 0),
    CHECK (updated_at >= opened_at)
);
CREATE INDEX incidents_status_idx ON incidents (status, opened_at DESC);
CREATE INDEX incidents_element_idx ON incidents (network_element_type, network_element_id);

CREATE TABLE recommendations (
    recommendation_id      uuid PRIMARY KEY,
    incident_id              uuid REFERENCES incidents (incident_id),
    action_type                text NOT NULL CHECK (action_type IN (
        'signal_plan_change', 'diversion', 'transit_priority',
        'emergency_preemption', 'variable_message_sign', 'other'
    )),
    generated_at                 timestamptz NOT NULL,
    expires_at                     timestamptz NOT NULL,
    status                          text NOT NULL CHECK (status IN ('proposed', 'requested', 'superseded', 'expired')),
    alternatives                      jsonb NOT NULL,
    safety_bounds                      jsonb NOT NULL,
    CHECK (expires_at > generated_at),
    CHECK (jsonb_array_length(alternatives) > 0)
);
CREATE INDEX recommendations_status_idx ON recommendations (status, generated_at DESC);
CREATE INDEX recommendations_incident_idx ON recommendations (incident_id);
