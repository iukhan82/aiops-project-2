-- P09.04 (CTL-12): workload identity at the database, closing the gap P09.05's controls.json recorded - "the executor
-- and verifier record a service name in the audit trail but authenticate to the database with the shared application
-- role." Three roles, one per real safety-critical service (the ones CTL-12's own gap text names, plus the separate
-- scenario-control service P05.09/P08.09 already keeps structurally apart): `svc_command_executor`,
-- `svc_outcome_verifier`, `svc_scenario_control`. `aiops_app` remains the migration/owner role and what the operator
-- API itself connects as (the API's own authority comes from the OIDC token on every request, not from its DB role);
-- these three connect as themselves.
--
-- Each new role is NOT an owner of anything (only `aiops_app` owns tables) and gets SELECT/INSERT/UPDATE across the
-- operational schema, explicitly never DELETE or TRUNCATE (nothing these three services do legitimately deletes a
-- row) and never DDL. UPDATE is additionally revoked, at the grant level, on every table the append-only triggers
-- already protect - two independent layers, not one: a role that could not even ATTEMPT the UPDATE never reaches the
-- trigger that would have refused it anyway.
--
-- Password is set separately by `database/rotate_service_secrets.py` (P09.04), never embedded in a migration file
-- (migrations are checksummed and, in this project, read by anyone with the source) - CREATE ROLE here with a
-- throwaway password that rotation replaces on first run in any environment that cares.
--
-- Roles are CLUSTER-level objects (shared by every database on the server), but this file runs once per database
-- (migrate.py applies it separately against `aiops` and `aiops_demo`, which are two databases on the SAME cluster) -
-- a second, unguarded `CREATE ROLE` on the second database would fail with "role already exists". The GRANTs below
-- ARE per-database (they apply to the tables of whichever database is current) and correctly run once per database.

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'svc_command_executor') THEN
        CREATE ROLE svc_command_executor LOGIN PASSWORD 'rotate-me-immediately' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'svc_outcome_verifier') THEN
        CREATE ROLE svc_outcome_verifier LOGIN PASSWORD 'rotate-me-immediately' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'svc_scenario_control') THEN
        CREATE ROLE svc_scenario_control LOGIN PASSWORD 'rotate-me-immediately' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
END $$;

-- This same file runs against both `aiops` and `aiops_demo`; a literal `GRANT ... ON DATABASE aiops` would grant
-- CONNECT on the wrong database when run against the other one, since a database-level GRANT names its target
-- cluster-wide, independent of which database the connection is currently on - so it is named dynamically instead.
DO $$ BEGIN
    EXECUTE format(
        'GRANT CONNECT ON DATABASE %I TO svc_command_executor, svc_outcome_verifier, svc_scenario_control',
        current_database()
    );
END $$;

GRANT USAGE ON SCHEMA public TO svc_command_executor, svc_outcome_verifier, svc_scenario_control;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO svc_command_executor, svc_outcome_verifier, svc_scenario_control;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO svc_command_executor, svc_outcome_verifier, svc_scenario_control;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE ON TABLES TO svc_command_executor, svc_outcome_verifier, svc_scenario_control;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO svc_command_executor, svc_outcome_verifier, svc_scenario_control;

REVOKE UPDATE ON operator_audit, scenario_control_audit, policy_decisions, retention_runs,
    incident_transitions, command_transitions, emergency_call_transitions, emergency_assignment_transitions
    FROM svc_command_executor, svc_outcome_verifier, svc_scenario_control;
