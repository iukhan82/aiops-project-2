-- P09.05 (CTL-15): tamper-evident append-only history.
--
-- The append-only triggers added in 0008/0020/0022/0024 stop UPDATE, DELETE and TRUNCATE through normal SQL, but a database
-- superuser can still drop a trigger, rewrite a row, and re-create it - nothing before this migration would notice. A hash
-- chain closes that gap: every row also carries the hash of the row before it (per table), so a rewrite that skips
-- recomputing every later hash is detectable by an independent pass over the data (`backend/audit_chain.py`), which does not
-- rely on the trigger having run - it is a forensic check, not an enforcement mechanism, and catches exactly the case the
-- trigger cannot: history that was rewritten by someone with the privilege to defeat the trigger.
--
-- `command_transitions`, `incident_transitions` (0008), `emergency_call_transitions` and `emergency_assignment_transitions`
-- (0016) had no protection at all until now, despite being the "how did it get there" record CTL-15 names; they get the
-- chain here, and are protected against UPDATE and TRUNCATE - but deliberately NOT against DELETE, unlike the other four
-- tables. 0011 made these four cascade from their parent (`commands`/`incidents`/`emergency_calls`/
-- `emergency_unit_assignments`) `ON DELETE CASCADE` specifically so retention (`database/retention.py`, CTL-18) can purge
-- a terminal command or a resolved incident past its window and take its history with it - a real, evidenced, sanctioned
-- lifecycle action, not a rewrite. Blocking DELETE outright here would break that (and would also break every
-- verification script that cleans up its own fixture commands/incidents the same way). What must never happen to these
-- four is a REWRITE of what they say happened, or a mass wipe - UPDATE and TRUNCATE - which this migration does block;
-- what retention does to them is a designed and audited deletion of data whose window has passed, not tampering.
--
-- Extends, not replaces: 0008's audit tables, 0020's operator_audit, 0022's scenario_control_audit and 0024's
-- policy_decisions all keep their existing full (UPDATE/DELETE/TRUNCATE) append-only triggers - nothing legitimately
-- deletes from those four, ever.
--
-- `retention_runs` (0010) gets the same full protection and a chain, not because anything deletes it, but because it is
-- what makes a gap in the four cascade-eligible tables above legible: every retention purge already records its
-- `commands_deleted`/`incidents_deleted` counts and a `run_at` there, so a gap in a transition table's chain around a
-- purge's `run_at` is an accounted-for retention gap, and a gap with no matching run is not.
--
-- Not a strict single chain: two writers committing concurrently can both read the same "last row" (READ COMMITTED
-- only sees committed rows) and so both link to the same `prev_hash`, forking the chain into a tree rather than a
-- list. That is an accepted, benign consequence of concurrent legitimate writers, not a weakness: `backend/audit_chain.py`
-- verifies every row's hash recomputes from its own `prev_hash` and content, and that every `prev_hash` points to some
-- row that really exists (or is the genesis marker) - a rewritten row, a deleted row, or a row spliced in later still
-- breaks one of those, which is the property this migration exists to give.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE operator_audit ADD COLUMN prev_hash text;
ALTER TABLE operator_audit ADD COLUMN row_hash text;
ALTER TABLE scenario_control_audit ADD COLUMN prev_hash text;
ALTER TABLE scenario_control_audit ADD COLUMN row_hash text;
ALTER TABLE policy_decisions ADD COLUMN chain_seq bigserial;
ALTER TABLE policy_decisions ADD COLUMN prev_hash text;
ALTER TABLE policy_decisions ADD COLUMN row_hash text;
ALTER TABLE incident_transitions ADD COLUMN prev_hash text;
ALTER TABLE incident_transitions ADD COLUMN row_hash text;
ALTER TABLE command_transitions ADD COLUMN prev_hash text;
ALTER TABLE command_transitions ADD COLUMN row_hash text;
ALTER TABLE emergency_call_transitions ADD COLUMN prev_hash text;
ALTER TABLE emergency_call_transitions ADD COLUMN row_hash text;
ALTER TABLE emergency_assignment_transitions ADD COLUMN prev_hash text;
ALTER TABLE emergency_assignment_transitions ADD COLUMN row_hash text;
ALTER TABLE retention_runs ADD COLUMN prev_hash text;
ALTER TABLE retention_runs ADD COLUMN row_hash text;

-- One generic function: TG_ARGV[0] is the column that orders a table's own history (its own primary/sequence key), used
-- to find the previous row's hash. A bigserial default is resolved before a BEFORE INSERT trigger runs, so NEW already
-- carries its own sequence value here - `row_to_json(NEW)` therefore already makes two otherwise-identical rows hash
-- differently. It is read while `row_hash` is still NULL (so a row never includes its own hash) and after `prev_hash`
-- has been set (so the chain link is part of what is hashed).
CREATE FUNCTION audit_chain_link() RETURNS trigger AS $$
DECLARE
    order_col text := TG_ARGV[0];
    previous  text;
BEGIN
    EXECUTE format('SELECT row_hash FROM %I ORDER BY %I DESC LIMIT 1', TG_TABLE_NAME, order_col) INTO previous;
    NEW.prev_hash := COALESCE(previous, 'genesis:' || TG_TABLE_NAME);
    NEW.row_hash := encode(digest(NEW.prev_hash || '|' || row_to_json(NEW)::text, 'sha256'), 'hex');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER operator_audit_chain BEFORE INSERT ON operator_audit
    FOR EACH ROW EXECUTE FUNCTION audit_chain_link('audit_id');
CREATE TRIGGER scenario_control_audit_chain BEFORE INSERT ON scenario_control_audit
    FOR EACH ROW EXECUTE FUNCTION audit_chain_link('id');
CREATE TRIGGER policy_decisions_chain BEFORE INSERT ON policy_decisions
    FOR EACH ROW EXECUTE FUNCTION audit_chain_link('chain_seq');
CREATE TRIGGER retention_runs_chain BEFORE INSERT ON retention_runs
    FOR EACH ROW EXECUTE FUNCTION audit_chain_link('id');
CREATE TRIGGER incident_transitions_chain BEFORE INSERT ON incident_transitions
    FOR EACH ROW EXECUTE FUNCTION audit_chain_link('id');
CREATE TRIGGER command_transitions_chain BEFORE INSERT ON command_transitions
    FOR EACH ROW EXECUTE FUNCTION audit_chain_link('id');
CREATE TRIGGER emergency_call_transitions_chain BEFORE INSERT ON emergency_call_transitions
    FOR EACH ROW EXECUTE FUNCTION audit_chain_link('id');
CREATE TRIGGER emergency_assignment_transitions_chain BEFORE INSERT ON emergency_assignment_transitions
    FOR EACH ROW EXECUTE FUNCTION audit_chain_link('id');

-- Deliberately UPDATE/TRUNCATE only for these four - see the header comment on why DELETE is not refused here.
CREATE FUNCTION transition_refuses_rewrite_and_wipe() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% never allows %: history is corrected by adding a new row, not changing an old one', TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER incident_transitions_no_update BEFORE UPDATE ON incident_transitions
    FOR EACH ROW EXECUTE FUNCTION transition_refuses_rewrite_and_wipe();
CREATE TRIGGER incident_transitions_no_truncate BEFORE TRUNCATE ON incident_transitions
    FOR EACH STATEMENT EXECUTE FUNCTION transition_refuses_rewrite_and_wipe();
CREATE TRIGGER command_transitions_no_update BEFORE UPDATE ON command_transitions
    FOR EACH ROW EXECUTE FUNCTION transition_refuses_rewrite_and_wipe();
CREATE TRIGGER command_transitions_no_truncate BEFORE TRUNCATE ON command_transitions
    FOR EACH STATEMENT EXECUTE FUNCTION transition_refuses_rewrite_and_wipe();
CREATE TRIGGER emergency_call_transitions_no_update BEFORE UPDATE ON emergency_call_transitions
    FOR EACH ROW EXECUTE FUNCTION transition_refuses_rewrite_and_wipe();
CREATE TRIGGER emergency_call_transitions_no_truncate BEFORE TRUNCATE ON emergency_call_transitions
    FOR EACH STATEMENT EXECUTE FUNCTION transition_refuses_rewrite_and_wipe();
CREATE TRIGGER emergency_assignment_transitions_no_update BEFORE UPDATE ON emergency_assignment_transitions
    FOR EACH ROW EXECUTE FUNCTION transition_refuses_rewrite_and_wipe();
CREATE TRIGGER emergency_assignment_transitions_no_truncate BEFORE TRUNCATE ON emergency_assignment_transitions
    FOR EACH STATEMENT EXECUTE FUNCTION transition_refuses_rewrite_and_wipe();

-- CTL-16 defense in depth: even if the application's own redaction (backend/redaction.py) were bypassed, the database
-- itself refuses to store a JWT or a PEM private key in the columns known to carry free-form detail. This cannot see
-- every shape of secret (that is the application's job); it catches the two shapes worth a database-level backstop.
CREATE FUNCTION refuses_embedded_secrets(value text) RETURNS boolean AS $$
    SELECT value IS NULL
        OR (value !~ '-----BEGIN [A-Z ]*PRIVATE KEY-----'
            AND value !~ 'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*');
$$ LANGUAGE sql IMMUTABLE;

CREATE FUNCTION retention_runs_is_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'retention_runs is append-only: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER retention_runs_no_update BEFORE UPDATE ON retention_runs
    FOR EACH ROW EXECUTE FUNCTION retention_runs_is_append_only();
CREATE TRIGGER retention_runs_no_delete BEFORE DELETE ON retention_runs
    FOR EACH ROW EXECUTE FUNCTION retention_runs_is_append_only();
CREATE TRIGGER retention_runs_no_truncate BEFORE TRUNCATE ON retention_runs
    FOR EACH STATEMENT EXECUTE FUNCTION retention_runs_is_append_only();

ALTER TABLE operator_audit ADD CONSTRAINT operator_audit_no_embedded_secret CHECK (refuses_embedded_secrets(detail::text));
ALTER TABLE scenario_control_audit ADD CONSTRAINT scenario_control_audit_no_embedded_secret CHECK (refuses_embedded_secrets(detail));
ALTER TABLE policy_decisions ADD CONSTRAINT policy_decisions_no_embedded_secret CHECK (refuses_embedded_secrets(input::text));
