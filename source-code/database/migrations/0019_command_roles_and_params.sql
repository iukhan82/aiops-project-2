-- P07.10 / P08: who held which operating role when a command was requested
-- and approved (docs/security/ROLES_AND_ACTION_AUTHORITY.md, P02.08), and the
-- concrete parameters an approved command executes with.
--
-- `requested_by_role` / `approved_by_role` are nullable only because commands
-- created before this migration carry no role; the policy skips the requester-
-- authority check for such legacy rows and applies it to every new command.
--
-- `contracts/command/v1` deliberately has no magnitude/parameter field (only
-- `target.adapter` + `target.entity_id`), so the parameters a signal deviation,
-- a VMS message or a pre-emption route need live in this side table, written at
-- request time and read only by `system:command-executor`.

ALTER TABLE commands ADD COLUMN requested_by_role text;
ALTER TABLE commands ADD COLUMN approved_by_role text;

CREATE TABLE command_params (
    command_id  uuid PRIMARY KEY REFERENCES commands (command_id) ON DELETE CASCADE,
    params      jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
