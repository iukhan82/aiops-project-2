-- P09.05 (CTL-34): 0024's `policy_decisions.point` CHECK constraint predates OVERRIDE (P09.03 built REQUEST/REVIEW/
-- APPROVAL/EXECUTION; override is new here) and would refuse every override decision, permitted or denied, with a
-- database error - the opposite of what an append-only decision record is for. Adds the missing value.

ALTER TABLE policy_decisions DROP CONSTRAINT policy_decisions_point_check;
ALTER TABLE policy_decisions ADD CONSTRAINT policy_decisions_point_check
    CHECK (point IN ('command_request', 'command_review', 'command_approval', 'command_execution', 'command_override'));
