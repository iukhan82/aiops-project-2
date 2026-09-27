-- P10.08: policy-controlled AIOps remediation - what was asked, what the policy said, what was done, and whether it worked.
--
-- The limits that matter are enforced HERE, not only in the worker:
--   * a `plan_only` request can never reach `executing`, `executed` or `verified` (CHECK);
--   * an `approval` request cannot leave `awaiting_approval` without a named approver, and the worker's role has no
--     privilege on the approver columns, so it cannot approve its own request (column-level grant);
--   * at most ONE remediation is approved-or-running anywhere on the platform (partial unique index) - the blast radius
--     is one action at a time, even with two workers racing;
--   * one row per (incident, action, target, attempt): a double dispatch of the same attempt is refused by the unique
--     idempotency key, so a retry after a crash cannot run the action twice.
-- History (`remediation_transitions`) is append-only and hash-chained like every other transition table (0026, 0029).

CREATE TABLE remediation_requests (
    remediation_id       uuid PRIMARY KEY,
    platform_incident_id uuid NOT NULL REFERENCES platform_incidents (platform_incident_id) ON DELETE CASCADE,
    action_id            text NOT NULL,
    kind                 text NOT NULL CHECK (kind IN ('restart', 'failover', 'quarantine', 'rollback', 'sampling', 'scale')),
    target               text NOT NULL CHECK (char_length(target) BETWEEN 1 AND 128),
    params               jsonb NOT NULL DEFAULT '{}'::jsonb,
    attempt_no           integer NOT NULL CHECK (attempt_no >= 0),
    idempotency_key      text NOT NULL UNIQUE,
    autonomy             text NOT NULL CHECK (autonomy IN ('auto', 'approval', 'plan_only')),
    status               text NOT NULL CHECK (status IN (
        'denied', 'awaiting_approval', 'approved', 'executing', 'executed', 'verified', 'failed', 'planned',
        'abandoned', 'expired'
    )),
    motivating_signals   text[] NOT NULL DEFAULT '{}',
    requested_by         text NOT NULL,
    approved_by          text,
    approver_role        text,
    requested_at         timestamptz NOT NULL,
    started_at           timestamptz,
    executed_at          timestamptz,
    finished_at          timestamptz,
    policy_decision      jsonb NOT NULL,
    result               jsonb,
    verification         jsonb,
    CHECK (autonomy <> 'plan_only' OR status IN ('denied', 'planned')),
    CHECK (autonomy <> 'approval' OR status IN ('denied', 'awaiting_approval', 'expired') OR approved_by IS NOT NULL),
    CHECK (status NOT IN ('executing', 'executed', 'verified', 'abandoned') OR started_at IS NOT NULL),
    CHECK (status NOT IN ('executed', 'verified') OR executed_at IS NOT NULL),
    CHECK (status NOT IN ('denied', 'planned', 'verified', 'failed', 'abandoned', 'expired') OR finished_at IS NOT NULL),
    CHECK (status NOT IN ('denied', 'planned') OR attempt_no = 0)
);
CREATE UNIQUE INDEX remediation_one_in_flight ON remediation_requests ((1)) WHERE status IN ('approved', 'executing', 'executed');
CREATE INDEX remediation_requests_incident_idx ON remediation_requests (platform_incident_id, requested_at);
CREATE INDEX remediation_requests_target_idx ON remediation_requests (target, requested_at DESC);

CREATE TABLE remediation_transitions (
    id             bigserial PRIMARY KEY,
    remediation_id uuid NOT NULL REFERENCES remediation_requests (remediation_id) ON DELETE CASCADE,
    at             timestamptz NOT NULL DEFAULT now(),
    from_status    text,
    to_status      text NOT NULL,
    actor          text NOT NULL,
    detail         jsonb NOT NULL DEFAULT '{}'::jsonb,
    prev_hash      text,
    row_hash       text
);
CREATE INDEX remediation_transitions_request_idx ON remediation_transitions (remediation_id, id);

CREATE TRIGGER remediation_transitions_chain BEFORE INSERT ON remediation_transitions
    FOR EACH ROW EXECUTE FUNCTION audit_chain_link('id');
CREATE TRIGGER remediation_transitions_no_update BEFORE UPDATE ON remediation_transitions
    FOR EACH ROW EXECUTE FUNCTION transition_refuses_rewrite_and_wipe();
CREATE TRIGGER remediation_transitions_no_truncate BEFORE TRUNCATE ON remediation_transitions
    FOR EACH STATEMENT EXECUTE FUNCTION transition_refuses_rewrite_and_wipe();

ALTER TABLE remediation_requests ADD CONSTRAINT remediation_requests_no_embedded_secret
    CHECK (refuses_embedded_secrets(params::text) AND refuses_embedded_secrets(coalesce(result::text, '')));
ALTER TABLE remediation_transitions ADD CONSTRAINT remediation_transitions_no_embedded_secret
    CHECK (refuses_embedded_secrets(detail::text));

-- Workload identity (CTL-12): the remediation worker connects as itself with exactly what it needs.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'svc_remediation_worker') THEN
        CREATE ROLE svc_remediation_worker LOGIN PASSWORD 'rotate-me-immediately' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
END $$;

DO $$ BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO svc_remediation_worker', current_database());
END $$;

GRANT USAGE ON SCHEMA public TO svc_remediation_worker;
-- It reads incidents and their signals, may escalate an incident (status and timestamp only) and log that it did.
GRANT SELECT ON platform_incidents, platform_incident_signals TO svc_remediation_worker;
GRANT UPDATE (status, updated_at) ON platform_incidents TO svc_remediation_worker;
GRANT SELECT, INSERT ON platform_incident_events TO svc_remediation_worker;
GRANT USAGE, SELECT ON SEQUENCE platform_incident_events_id_seq TO svc_remediation_worker;
-- It creates and advances its own requests, but never touches who approved one, or what was asked and decided.
GRANT SELECT ON remediation_requests TO svc_remediation_worker;
GRANT INSERT (remediation_id, platform_incident_id, action_id, kind, target, params, attempt_no, idempotency_key, autonomy,
              status, motivating_signals, requested_by, requested_at, policy_decision, finished_at)
    ON remediation_requests TO svc_remediation_worker;
GRANT UPDATE (status, started_at, executed_at, finished_at, result, verification)
    ON remediation_requests TO svc_remediation_worker;
GRANT SELECT, INSERT ON remediation_transitions TO svc_remediation_worker;
GRANT USAGE, SELECT ON SEQUENCE remediation_transitions_id_seq TO svc_remediation_worker;

-- The other workload roles have no business here (0028's default privileges hand new tables to the three older ones).
REVOKE ALL ON remediation_requests, remediation_transitions
    FROM svc_command_executor, svc_outcome_verifier, svc_scenario_control, svc_platform_correlator;
REVOKE ALL ON SEQUENCE remediation_transitions_id_seq
    FROM svc_command_executor, svc_outcome_verifier, svc_scenario_control, svc_platform_correlator;
