-- P05.08: append-only audit trails for incident/command state transitions.
-- Current status already lives on incidents/commands themselves; these
-- tables are the immutable "how did it get there" record - never updated,
-- only inserted, matching the audit-append-only invariant this project
-- documents elsewhere (P09.05 protects it at the role/schema level later).

CREATE TABLE incident_transitions (
    id           bigserial PRIMARY KEY,
    incident_id  uuid NOT NULL REFERENCES incidents (incident_id),
    from_status  text,
    to_status    text NOT NULL,
    changed_by   text NOT NULL,
    note         text,
    changed_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX incident_transitions_incident_idx ON incident_transitions (incident_id, changed_at ASC);

CREATE TABLE command_transitions (
    id           bigserial PRIMARY KEY,
    command_id   uuid NOT NULL REFERENCES commands (command_id),
    from_status  text,
    to_status    text NOT NULL,
    changed_by   text NOT NULL,
    note         text,
    changed_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX command_transitions_command_idx ON command_transitions (command_id, changed_at ASC);
