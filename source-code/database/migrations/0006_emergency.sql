-- P05.04: control records, part 3 (contracts/emergency-call/v1,
-- contracts/emergency-unit-assignment/v1). Deliberately no patient/medical
-- column anywhere, matching the contracts' own explicit exclusion.

CREATE TABLE emergency_calls (
    call_id                uuid PRIMARY KEY,
    call_type                text NOT NULL CHECK (call_type IN ('ambulance', 'fire', 'police')),
    priority                   text NOT NULL CHECK (priority IN ('low', 'medium', 'high', 'critical')),
    location                    geography(Point, 4326) NOT NULL,
    geometry_version              text NOT NULL,
    reported_at                    timestamptz NOT NULL,
    source_reliability               text NOT NULL CHECK (source_reliability IN (
        'verified_dispatch', 'unverified_report', 'automated_detection'
    )),
    status                            text NOT NULL CHECK (status IN (
        'received', 'dispatched', 'unit_assigned', 'en_route', 'on_scene', 'cleared', 'cancelled'
    )),
    truth_label                        text NOT NULL CHECK (
        truth_label IN ('simulated', 'measured', 'inferred', 'predicted', 'operator_entered', 'verified')
    )
);
CREATE INDEX emergency_calls_status_idx ON emergency_calls (status, reported_at DESC);
CREATE INDEX emergency_calls_location_idx ON emergency_calls USING GIST (location);

CREATE TABLE emergency_unit_assignments (
    assignment_id          uuid PRIMARY KEY,
    call_id                  uuid NOT NULL REFERENCES emergency_calls (call_id),
    unit_id                    text NOT NULL,
    agency                       text NOT NULL,
    capability                    text NOT NULL,
    status                         text NOT NULL CHECK (status IN (
        'assigned', 'acknowledged', 'en_route', 'staged', 'on_scene', 'clear', 'unavailable'
    )),
    assigned_at                      timestamptz NOT NULL,
    route_alternatives                 jsonb NOT NULL,
    truth_label                         text NOT NULL CHECK (
        truth_label IN ('simulated', 'measured', 'inferred', 'predicted', 'operator_entered', 'verified')
    ),
    CHECK (jsonb_array_length(route_alternatives) > 0)
);
CREATE INDEX emergency_unit_assignments_call_idx ON emergency_unit_assignments (call_id);
CREATE INDEX emergency_unit_assignments_status_idx ON emergency_unit_assignments (status, assigned_at DESC);
