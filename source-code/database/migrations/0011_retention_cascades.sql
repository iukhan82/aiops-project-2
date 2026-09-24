-- P05.10: when a terminal command/incident is retired by retention policy,
-- its own dependent records (audit transition history, command outcomes,
-- recommendations) retire with it - a retention purge retires the whole
-- case file together, not the primary row while leaving orphaned children.
-- (Non-terminal/active commands and incidents are never purged in the
-- first place - see database/retention.py - so this never touches
-- anything still under active control.)

ALTER TABLE command_transitions DROP CONSTRAINT command_transitions_command_id_fkey;
ALTER TABLE command_transitions ADD CONSTRAINT command_transitions_command_id_fkey
    FOREIGN KEY (command_id) REFERENCES commands (command_id) ON DELETE CASCADE;

ALTER TABLE command_outcomes DROP CONSTRAINT command_outcomes_command_id_fkey;
ALTER TABLE command_outcomes ADD CONSTRAINT command_outcomes_command_id_fkey
    FOREIGN KEY (command_id) REFERENCES commands (command_id) ON DELETE CASCADE;

ALTER TABLE incident_transitions DROP CONSTRAINT incident_transitions_incident_id_fkey;
ALTER TABLE incident_transitions ADD CONSTRAINT incident_transitions_incident_id_fkey
    FOREIGN KEY (incident_id) REFERENCES incidents (incident_id) ON DELETE CASCADE;

ALTER TABLE recommendations DROP CONSTRAINT recommendations_incident_id_fkey;
ALTER TABLE recommendations ADD CONSTRAINT recommendations_incident_id_fkey
    FOREIGN KEY (incident_id) REFERENCES incidents (incident_id) ON DELETE CASCADE;
