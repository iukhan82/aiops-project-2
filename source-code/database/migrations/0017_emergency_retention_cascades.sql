-- P07.01: retiring a call retires its assignments with it, matching P05.10's
-- rule (0011_retention_cascades.sql) that a retention purge retires a whole
-- case file together rather than leaving orphaned children.

ALTER TABLE emergency_unit_assignments DROP CONSTRAINT emergency_unit_assignments_call_id_fkey;
ALTER TABLE emergency_unit_assignments ADD CONSTRAINT emergency_unit_assignments_call_id_fkey
    FOREIGN KEY (call_id) REFERENCES emergency_calls (call_id) ON DELETE CASCADE;
