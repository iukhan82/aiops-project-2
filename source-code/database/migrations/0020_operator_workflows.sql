-- P08.07 - P08.09: what the operator UI writes and reads beyond the platform's own records.
--
-- * incident_notes:   free-text notes an operator adds to an incident. Append-only; a note is never edited.
-- * operator_audit:   one row for every operator-facing action the API accepted, refused or failed, with who did it in
--                     which role. Append-only: UPDATE and DELETE are refused by a trigger, not by convention.
-- * shift_handovers:  structured handover between shifts, acknowledged by the incoming person by name.
-- * service_heartbeats: the last time each platform service reported it was alive, for the platform-status screen
--                     (a probe that has never been recorded is shown as unknown, not as healthy).

CREATE TABLE incident_notes (
    note_id      bigserial PRIMARY KEY,
    incident_id  uuid NOT NULL REFERENCES incidents (incident_id) ON DELETE CASCADE,
    author       text NOT NULL,
    author_roles text[] NOT NULL,
    note         text NOT NULL CHECK (char_length(note) BETWEEN 1 AND 2000),
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX incident_notes_incident_idx ON incident_notes (incident_id, created_at ASC);

CREATE TABLE operator_audit (
    audit_id     bigserial PRIMARY KEY,
    at           timestamptz NOT NULL DEFAULT now(),
    actor        text NOT NULL,
    actor_roles  text[] NOT NULL,
    action       text NOT NULL,
    entity_type  text NOT NULL,
    entity_id    text,
    outcome      text NOT NULL CHECK (outcome IN ('allowed', 'denied', 'failed')),
    detail       jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX operator_audit_at_idx ON operator_audit (at DESC, audit_id DESC);
CREATE INDEX operator_audit_actor_idx ON operator_audit (actor, at DESC);
CREATE INDEX operator_audit_entity_idx ON operator_audit (entity_type, entity_id, at DESC);

CREATE FUNCTION operator_audit_is_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'operator_audit is append-only: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER operator_audit_no_update BEFORE UPDATE ON operator_audit
    FOR EACH ROW EXECUTE FUNCTION operator_audit_is_append_only();
CREATE TRIGGER operator_audit_no_delete BEFORE DELETE ON operator_audit
    FOR EACH ROW EXECUTE FUNCTION operator_audit_is_append_only();
CREATE TRIGGER operator_audit_no_truncate BEFORE TRUNCATE ON operator_audit
    FOR EACH STATEMENT EXECUTE FUNCTION operator_audit_is_append_only();

CREATE TABLE shift_handovers (
    handover_id     uuid PRIMARY KEY,
    created_at      timestamptz NOT NULL DEFAULT now(),
    author          text NOT NULL,
    author_roles    text[] NOT NULL,
    outgoing_shift  text NOT NULL,
    incoming_shift  text NOT NULL,
    summary         text NOT NULL CHECK (char_length(summary) BETWEEN 1 AND 4000),
    open_items      jsonb NOT NULL DEFAULT '[]'::jsonb,
    acknowledged_by text,
    acknowledged_at timestamptz,
    CHECK ((acknowledged_by IS NULL) = (acknowledged_at IS NULL))
);
CREATE INDEX shift_handovers_created_idx ON shift_handovers (created_at DESC);

CREATE TABLE service_heartbeats (
    service    text PRIMARY KEY,
    last_seen  timestamptz NOT NULL,
    detail     jsonb NOT NULL DEFAULT '{}'::jsonb
);
