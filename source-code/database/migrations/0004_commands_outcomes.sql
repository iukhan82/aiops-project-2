-- P05.04: control records, part 1 (contracts/command/v1, contracts/outcome/v1).
--
-- idempotency_key UNIQUE is the safety property from ADR's command path:
-- "a resubmission with the same key must not execute twice" - enforced here
-- as a real constraint, not just documented behavior.

CREATE TABLE commands (
    command_id          uuid PRIMARY KEY,
    idempotency_key      text NOT NULL UNIQUE,
    recommendation_id    uuid,
    action_type          text NOT NULL CHECK (action_type IN (
        'signal_plan_change', 'diversion', 'transit_priority',
        'emergency_preemption', 'variable_message_sign', 'other'
    )),
    target_adapter        text NOT NULL,
    target_entity_id       text NOT NULL,
    requested_by            text NOT NULL,
    requested_at             timestamptz NOT NULL,
    approved_by              text,
    approved_at              timestamptz,
    expires_at                timestamptz NOT NULL,
    policy_decision           text NOT NULL CHECK (policy_decision IN ('pending', 'approved', 'denied', 'policy_unavailable')),
    status                    text NOT NULL CHECK (status IN (
        'requested', 'approved', 'denied', 'executing', 'executed',
        'failed', 'rolled_back', 'expired'
    )),
    acknowledged_at            timestamptz,
    rolled_back_at              timestamptz,
    error_code                  text CHECK (error_code IN (
        'policy_denied', 'expired', 'stale_evidence', 'adapter_unreachable',
        'invalid_target', 'conflict', 'internal'
    )),
    error_message                text,
    error_retryable               boolean,
    updated_at                    timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_at > requested_at)
);
CREATE INDEX commands_status_idx ON commands (status, requested_at DESC);
CREATE INDEX commands_target_idx ON commands (target_adapter, target_entity_id);

CREATE TABLE command_outcomes (
    outcome_id         uuid PRIMARY KEY,
    command_id          uuid NOT NULL REFERENCES commands (command_id),
    pre_window_start     timestamptz NOT NULL,
    pre_window_end        timestamptz NOT NULL,
    post_window_start      timestamptz NOT NULL,
    post_window_end         timestamptz NOT NULL,
    classification           text NOT NULL CHECK (classification IN ('effective', 'ineffective', 'unsafe', 'unknown')),
    verified_at               timestamptz NOT NULL,
    verifier                  text NOT NULL,
    rollback_triggered         boolean NOT NULL,
    detail                      jsonb
);
CREATE INDEX command_outcomes_command_idx ON command_outcomes (command_id, verified_at DESC);
