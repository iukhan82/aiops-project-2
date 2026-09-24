-- P07.01: emergency call/assignment state machine (append-only transitions,
-- same pattern as P05.08's incidents) plus the columns contracts/
-- emergency-call/v1 and emergency-unit-assignment/v1 already define but
-- migration 0006 (written before P03.04's simulator existed) did not carry.

ALTER TABLE emergency_calls ADD COLUMN call_subtype text NOT NULL DEFAULT 'unspecified';
ALTER TABLE emergency_calls ALTER COLUMN call_subtype DROP DEFAULT;

ALTER TABLE emergency_unit_assignments ADD COLUMN acknowledged_at timestamptz;
ALTER TABLE emergency_unit_assignments ADD COLUMN arrived_at timestamptz;
ALTER TABLE emergency_unit_assignments ADD COLUMN cleared_at timestamptz;
ALTER TABLE emergency_unit_assignments ADD COLUMN handover jsonb;
ALTER TABLE emergency_unit_assignments ADD CONSTRAINT emergency_unit_assignments_handover_shape CHECK (
    handover IS NULL OR (handover ? 'from_agency' AND handover ? 'to_agency' AND handover ? 'handover_at' AND handover ? 'acknowledged_by')
);

CREATE TABLE emergency_call_transitions (
    id          bigserial PRIMARY KEY,
    call_id     uuid NOT NULL REFERENCES emergency_calls (call_id) ON DELETE CASCADE,
    from_status text,
    to_status   text NOT NULL,
    changed_by  text NOT NULL,
    note        text,
    changed_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX emergency_call_transitions_call_idx ON emergency_call_transitions (call_id, changed_at ASC, id ASC);

CREATE TABLE emergency_assignment_transitions (
    id            bigserial PRIMARY KEY,
    assignment_id uuid NOT NULL REFERENCES emergency_unit_assignments (assignment_id) ON DELETE CASCADE,
    from_status   text,
    to_status     text NOT NULL,
    changed_by    text NOT NULL,
    note          text,
    changed_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX emergency_assignment_transitions_assignment_idx ON emergency_assignment_transitions (assignment_id, changed_at ASC, id ASC);
