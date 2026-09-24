-- P08.09: the scenario-control API now authenticates a real person (a verified Keycloak token) instead of trusting a role header the
-- caller wrote. Its own audit trail therefore records who acted, refusals for requests that carried no valid identity, and reads of
-- the run list and of the trail itself. The trail becomes append-only, like the operational one: a demo control surface that could
-- quietly rewrite its own history would be worth little as evidence.

ALTER TABLE scenario_control_audit ADD COLUMN actor text;

ALTER TABLE scenario_control_audit DROP CONSTRAINT scenario_control_audit_action_check;
ALTER TABLE scenario_control_audit ADD CONSTRAINT scenario_control_audit_action_check
    CHECK (action IN ('start', 'status', 'reset', 'replay', 'list', 'audit'));

ALTER TABLE scenario_control_audit DROP CONSTRAINT scenario_control_audit_outcome_check;
ALTER TABLE scenario_control_audit ADD CONSTRAINT scenario_control_audit_outcome_check
    CHECK (outcome IN ('allowed', 'denied_role', 'denied_bound', 'denied_identity', 'not_found'));

CREATE FUNCTION scenario_control_audit_is_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'scenario_control_audit is append-only: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER scenario_control_audit_no_update BEFORE UPDATE ON scenario_control_audit
    FOR EACH ROW EXECUTE FUNCTION scenario_control_audit_is_append_only();
CREATE TRIGGER scenario_control_audit_no_delete BEFORE DELETE ON scenario_control_audit
    FOR EACH ROW EXECUTE FUNCTION scenario_control_audit_is_append_only();
CREATE TRIGGER scenario_control_audit_no_truncate BEFORE TRUNCATE ON scenario_control_audit
    FOR EACH STATEMENT EXECUTE FUNCTION scenario_control_audit_is_append_only();
