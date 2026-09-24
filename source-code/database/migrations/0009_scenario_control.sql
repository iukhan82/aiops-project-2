-- P05.09: scenario-control API's own state and audit trail. Deliberately
-- separate from the operational tables (P05.04/P05.08) - a demo/test
-- control surface must never share a table, let alone a status enum, with
-- real incident/command state (P08.09's "demo controls remain visibly
-- separate" applies to the data model too, not just the UI).

CREATE TABLE scenario_runs (
    run_id        uuid PRIMARY KEY,
    scenario      text NOT NULL,
    status        text NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'reset')),
    started_by    text NOT NULL,
    started_at    timestamptz NOT NULL,
    completed_at  timestamptz,
    result        jsonb
);
CREATE INDEX scenario_runs_status_idx ON scenario_runs (status, started_at DESC);

CREATE TABLE scenario_control_audit (
    id       bigserial PRIMARY KEY,
    run_id   uuid,
    action   text NOT NULL CHECK (action IN ('start', 'status', 'reset', 'replay')),
    role     text NOT NULL,
    outcome  text NOT NULL CHECK (outcome IN ('allowed', 'denied_role', 'denied_bound', 'not_found')),
    detail   text,
    at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX scenario_control_audit_at_idx ON scenario_control_audit (at DESC);
