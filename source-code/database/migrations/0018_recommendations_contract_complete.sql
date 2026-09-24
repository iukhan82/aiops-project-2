-- P07.04: columns contracts/recommendation/v1 already defines that migration
-- 0005 (written before P06.07's incidents or P07.01's emergency calls
-- existed as live tables) did not carry.

ALTER TABLE recommendations ADD COLUMN trigger_incident_id uuid REFERENCES incidents (incident_id);
ALTER TABLE recommendations ADD COLUMN trigger_emergency_call_id uuid REFERENCES emergency_calls (call_id);
ALTER TABLE recommendations ADD COLUMN constraints text[] NOT NULL DEFAULT '{}';
ALTER TABLE recommendations ADD COLUMN superseded_by uuid REFERENCES recommendations (recommendation_id);

-- migration 0005's `incident_id` predates the contract's `trigger_incident_id` naming; keep the
-- data, rename the column to match the contract exactly (a fresh column, not yet in use).
UPDATE recommendations SET trigger_incident_id = incident_id WHERE incident_id IS NOT NULL;
ALTER TABLE recommendations DROP COLUMN incident_id;

CREATE INDEX recommendations_trigger_incident_idx ON recommendations (trigger_incident_id);
CREATE INDEX recommendations_trigger_call_idx ON recommendations (trigger_emergency_call_id);
