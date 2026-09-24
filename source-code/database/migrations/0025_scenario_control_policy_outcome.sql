-- P09.03: the scenario-control API now asks the policy engine whether a caller may use an endpoint. When the engine cannot answer the
-- request is refused (fail closed) and the refusal is audited under its own outcome, so a policy outage is distinguishable from a
-- caller who lacked the capability (`denied_role`) or a valid identity (`denied_identity`).

ALTER TABLE scenario_control_audit DROP CONSTRAINT scenario_control_audit_outcome_check;
ALTER TABLE scenario_control_audit ADD CONSTRAINT scenario_control_audit_outcome_check
    CHECK (outcome IN ('allowed', 'denied_role', 'denied_bound', 'denied_identity', 'denied_policy_unavailable', 'not_found'));
