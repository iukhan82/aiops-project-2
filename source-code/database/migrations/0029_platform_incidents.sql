-- P10.07: platform incidents - the AIOps view of "what is wrong with the platform itself", kept apart from the traffic
-- incidents table (0005), whose network elements, types and lifecycle describe the road network, not services.
--
-- Truth labelling is structural, not a convention. A platform incident is INFERRED from alerts and detector output: its
-- `truth_label` can only be 'inferred', its `hypothesis` (the suspected root cause) must carry `"verified": false`, and
-- the only place a cause becomes fact is `verified_cause`, which stays NULL until a person records one. A correlator that
-- tried to write a hypothesis as verified would be refused by the database, not just by a code review.
--
-- History (`platform_incident_events`) is append-only and hash-chained exactly like the traffic incident, command and
-- call transitions (0026): UPDATE and TRUNCATE are refused, DELETE is allowed only because a resolved incident's history
-- goes with it when retention removes it. The chain trigger needs `row_hash` to be the LAST column.

CREATE TABLE platform_incidents (
    platform_incident_id uuid PRIMARY KEY,
    incident_key          text NOT NULL,
    status                text NOT NULL CHECK (status IN ('open', 'acknowledged', 'escalated', 'resolved', 'reopened')),
    severity              text NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    title                 text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 300),
    components            text[] NOT NULL CHECK (cardinality(components) > 0),
    opened_at             timestamptz NOT NULL,
    updated_at            timestamptz NOT NULL,
    resolved_at           timestamptz,
    hypothesis            jsonb NOT NULL,
    verified_cause        text,
    signal_count          integer NOT NULL CHECK (signal_count > 0),
    truth_label           text NOT NULL DEFAULT 'inferred' CHECK (truth_label = 'inferred'),
    CHECK (updated_at >= opened_at),
    CHECK (COALESCE(hypothesis -> 'verified' = 'false'::jsonb, false)),  -- a hypothesis with no flag at all is refused too
    CHECK ((status = 'resolved') = (resolved_at IS NOT NULL))
);
-- One live incident per key: a second correlator racing to open the same incident is refused by the database.
CREATE UNIQUE INDEX platform_incidents_one_live_per_key ON platform_incidents (incident_key) WHERE status <> 'resolved';
CREATE INDEX platform_incidents_status_idx ON platform_incidents (status, opened_at DESC);

CREATE TABLE platform_incident_signals (
    platform_incident_id uuid NOT NULL REFERENCES platform_incidents (platform_incident_id) ON DELETE CASCADE,
    signal_key           text NOT NULL,
    source               text NOT NULL CHECK (source IN ('alert', 'anomaly')),
    signal               text NOT NULL,
    component            text NOT NULL,
    labels               jsonb NOT NULL DEFAULT '{}'::jsonb,
    severity             text NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    first_seen           timestamptz NOT NULL,
    last_seen            timestamptz NOT NULL,
    occurrences          integer NOT NULL CHECK (occurrences > 0),
    active               boolean NOT NULL,
    PRIMARY KEY (platform_incident_id, signal_key),
    CHECK (last_seen >= first_seen)
);

CREATE TABLE platform_incident_events (
    id                   bigserial PRIMARY KEY,
    platform_incident_id uuid NOT NULL REFERENCES platform_incidents (platform_incident_id) ON DELETE CASCADE,
    at                   timestamptz NOT NULL DEFAULT now(),
    event                text NOT NULL CHECK (event IN (
        'opened', 'signal_added', 'signal_cleared', 'signal_reactivated', 'hypothesis_changed', 'severity_changed',
        'acknowledged', 'escalated', 'resolved', 'reopened', 'cause_verified'
    )),
    actor                text NOT NULL,
    detail               jsonb NOT NULL DEFAULT '{}'::jsonb,
    prev_hash            text,
    row_hash             text
);
CREATE INDEX platform_incident_events_incident_idx ON platform_incident_events (platform_incident_id, id);

CREATE TRIGGER platform_incident_events_chain BEFORE INSERT ON platform_incident_events
    FOR EACH ROW EXECUTE FUNCTION audit_chain_link('id');
CREATE TRIGGER platform_incident_events_no_update BEFORE UPDATE ON platform_incident_events
    FOR EACH ROW EXECUTE FUNCTION transition_refuses_rewrite_and_wipe();
CREATE TRIGGER platform_incident_events_no_truncate BEFORE TRUNCATE ON platform_incident_events
    FOR EACH STATEMENT EXECUTE FUNCTION transition_refuses_rewrite_and_wipe();

-- CTL-16 defence in depth, same as the other free-form columns: no token or private key in what a signal or event carries.
ALTER TABLE platform_incident_events ADD CONSTRAINT platform_incident_events_no_embedded_secret
    CHECK (refuses_embedded_secrets(detail::text));
ALTER TABLE platform_incident_signals ADD CONSTRAINT platform_incident_signals_no_embedded_secret
    CHECK (refuses_embedded_secrets(labels::text));
ALTER TABLE platform_incidents ADD CONSTRAINT platform_incidents_no_embedded_secret
    CHECK (refuses_embedded_secrets(hypothesis::text));

-- Workload identity (CTL-12): the correlator connects as itself, with exactly the access it needs and nothing else.
-- Cluster-level role, so created once and guarded; the password is set by database/rotate_service_secrets.py.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'svc_platform_correlator') THEN
        CREATE ROLE svc_platform_correlator LOGIN PASSWORD 'rotate-me-immediately' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
END $$;

DO $$ BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO svc_platform_correlator', current_database());
END $$;

GRANT USAGE ON SCHEMA public TO svc_platform_correlator;
-- Column-level on the incident row: the correlator writes what it inferred and can never write `verified_cause` (only a
-- person, through the application role, records one). Its INSERT/UPDATE grants simply do not include that column.
GRANT SELECT ON platform_incidents, platform_incident_signals TO svc_platform_correlator;
GRANT INSERT (platform_incident_id, incident_key, status, severity, title, components, opened_at, updated_at,
              resolved_at, hypothesis, signal_count) ON platform_incidents TO svc_platform_correlator;
GRANT UPDATE (status, severity, title, components, updated_at, resolved_at, hypothesis, signal_count)
    ON platform_incidents TO svc_platform_correlator;
GRANT INSERT, UPDATE ON platform_incident_signals TO svc_platform_correlator;
GRANT SELECT, INSERT ON platform_incident_events TO svc_platform_correlator;
GRANT USAGE, SELECT ON SEQUENCE platform_incident_events_id_seq TO svc_platform_correlator;
-- Deliberately no DELETE, no TRUNCATE, no UPDATE on the history, and nothing on any traffic, command or audit table.

-- 0028's default privileges hand every new table to the three older service roles; they have no business with these.
REVOKE ALL ON platform_incidents, platform_incident_signals, platform_incident_events
    FROM svc_command_executor, svc_outcome_verifier, svc_scenario_control;
REVOKE ALL ON SEQUENCE platform_incident_events_id_seq FROM svc_command_executor, svc_outcome_verifier, svc_scenario_control;
