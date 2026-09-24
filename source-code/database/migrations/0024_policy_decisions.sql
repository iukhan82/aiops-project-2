-- P09.03: every decision the policy engine makes about a command, kept with what it was asked and which policy answered
-- (`contracts/policy-decision/v1`). One row for each of: may this role start a command (`command_request`), may it review one
-- (`command_review`), may the command be approved (`command_approval`), may the executor run it (`command_execution`). API access
-- decisions are not stored here - there is one per request; a refusal of one is already in `operator_audit`.
--
-- 'unavailable' is a row too: a policy outage is evidence, not an absence of it. The table is append-only for the same reason the
-- audit trails are - a decision log that can be edited afterwards would be worth little.

CREATE TABLE policy_decisions (
    decision_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    opa_decision_id text,
    point           text NOT NULL CHECK (point IN ('command_request', 'command_review', 'command_approval', 'command_execution')),
    entity_type     text NOT NULL,
    entity_id       text,
    policy_package  text NOT NULL,
    policy_version  text,
    decision        text NOT NULL CHECK (decision IN ('permit', 'approved', 'denied', 'expired', 'unavailable')),
    reason          text,
    input           jsonb NOT NULL,
    decided_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX policy_decisions_entity_idx ON policy_decisions (entity_type, entity_id, decided_at DESC);
CREATE INDEX policy_decisions_at_idx ON policy_decisions (decided_at DESC, decision_id);

CREATE FUNCTION policy_decisions_is_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'policy_decisions is append-only: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER policy_decisions_no_update BEFORE UPDATE ON policy_decisions
    FOR EACH ROW EXECUTE FUNCTION policy_decisions_is_append_only();
CREATE TRIGGER policy_decisions_no_delete BEFORE DELETE ON policy_decisions
    FOR EACH ROW EXECUTE FUNCTION policy_decisions_is_append_only();
CREATE TRIGGER policy_decisions_no_truncate BEFORE TRUNCATE ON policy_decisions
    FOR EACH STATEMENT EXECUTE FUNCTION policy_decisions_is_append_only();
